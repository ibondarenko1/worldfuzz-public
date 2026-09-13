"""Loopback-only dashboard. No provider endpoints, secrets, or remote execution."""
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import urlsplit, parse_qs
import uuid
import webbrowser
import zipfile

from .cases import TUNING_CASES
from .engine import json_bytes, write_json
from .experiment import (ReplayMeter, configuration, new_experiment, now, read_json,
                         representative, run_experiment, verify_experiment, verify_pair)


ASSETS = Path(__file__).parent / "ui"
TASK_NAMES = {"tuning-clear-crossing": "Clear crossing",
              "tuning-interrupted-observation": "Interrupted observations"}
EXPERIMENT_FILES = {"experiment.json", "representative.json", "manifest.json"}
CAPSULE_FILES = {"scenario.json", "baseline.json", "hardened.json", "control.json",
                 "control_baseline.json", "search.json", "minimization.json", "replays.json",
                 "comparison.json", "provenance.json", "manifest.json"}


class BusyError(ValueError):
    pass


def _project_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Project JSON contains duplicate object keys")
        result[key] = value
    return result


def _project_constant(_value):
    raise ValueError("Project JSON requires finite numbers")


class Dashboard:
    def __init__(self, output, examples=None):
        self.root = Path(output).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.examples = Path(examples) if examples is not None else Path(__file__).parent.parent / "examples"
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.active = None
        self.cancel_event = threading.Event()
        self.thread = None
        self.verifications = {}
        self.recordings = self._recordings()
        self._regressions = None
        reports = self.root / "verification"
        if reports.is_dir() and not reports.is_symlink():
            for path in reports.glob("*.json"):
                try:
                    report = read_json(path)
                    record_id = report.pop("record_id")
                    old = self.verifications.get(record_id, {})
                    if report.get("started_at", "") >= old.get("started_at", ""):
                        if report["status"] == "RUNNING":
                            report["status"] = "INTERRUPTED"
                        self.verifications[record_id] = report
                except (OSError, ValueError, KeyError, TypeError):
                    continue

    def regressions(self):
        # Keep the legacy simulator usable without optional navigation dependencies.
        with self.lock:
            if self._regressions is None:
                from .regression_dashboard import RegressionDashboard
                self._regressions = RegressionDashboard(self)
            return self._regressions

    def _recordings(self):
        records = {}
        live = self.examples / "first-live-20260909" / "capsule"
        if (live / "manifest.json").is_file():
            records["recorded-nemotron"] = {"id": "recorded-nemotron", "title": "Nemotron crossing case",
                "origin": "recording", "description": "Recorded NVIDIA Nemotron proposal, reduced and replayed locally. September 9, 2026.",
                "directory": live, "kind": "capsule"}
        pilot = self.examples / "offline-pilot-20260910" / "run"
        for case in TUNING_CASES:
            for method in ("crossing_time", "random"):
                filename = f"representative-{case.case_id}-{method}.json"
                if (pilot / filename).is_file():
                    record_id = f"recorded-{case.case_id}-{method}"
                    records[record_id] = {"id": record_id, "title": TASK_NAMES[case.case_id],
                        "origin": "recording", "description": "Recorded tuning pilot. " +
                        ("Crossing-time search." if method == "crossing_time" else "Random search."),
                        "directory": pilot, "filename": filename, "kind": "pilot_pair"}
        return records

    def _directory(self, record_id):
        if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id):
            raise ValueError("Unknown experiment")
        path = self.root / record_id
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Unknown experiment")
        return path

    def _journal(self, record_id):
        return read_json(self._directory(record_id) / "experiment.json")

    def _job_summary(self, record_id, journal):
        state = journal["status"]
        with self.lock:
            if state in {"QUEUED", "RUNNING"} and self.active != record_id:
                state = "INTERRUPTED"
        return {"id": record_id, "title": TASK_NAMES.get(journal["config"]["case_id"], "Local experiment"),
                "origin": "local", "status": state, "config": journal["config"],
                "counts": journal["counts"], "limits": journal["limits"],
                "started_at": journal["started_at"], "error": journal["error"],
                "description": "Local paired search. " + ("Crossing-time search." if
                    journal["config"]["method"] == "crossing_time" else "Random search.")}

    def listing(self):
        runs = []
        for path in sorted(self.root.iterdir(), reverse=True):
            if not re.fullmatch(r"[0-9a-f]{32}", path.name) or path.is_symlink() or not path.is_dir():
                continue
            try:
                runs.append(self._job_summary(path.name, self._journal(path.name)))
            except (OSError, ValueError, KeyError, TypeError):
                runs.append({"id": path.name, "title": "Unreadable local evidence", "origin": "local",
                             "status": "ERROR", "description": "Evidence files could not be read."})
        runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        records = [{k: v for k, v in record.items() if k not in {"directory", "filename", "kind"}}
                   for record in self.recordings.values()]
        with self.lock:
            activity = {"id": self.active, "kind": getattr(self, "active_kind", None)} if self.active else None
        return {"runs": runs, "recordings": records, "activity": activity}

    def bootstrap(self):
        return {"csrf_token": self.token, "cases": [
            {"id": c.case_id, "name": TASK_NAMES[c.case_id], "crossing_x_m": c.crossing_x_m,
             "seed": c.world_seed, "description": f"Crossing at {c.crossing_x_m:g} m. " +
             ("Clear observations." if len(c.sensor_delays_ms) == 1 else "0–300 ms delay; optional observation blackout.")}
            for c in TUNING_CASES], **self.listing()}

    def _archived_pair(self, record):
        directory = record["directory"]
        manifest = read_json(directory / "manifest.json")
        if record["kind"] == "capsule":
            names = ["baseline.json", "hardened.json", "provenance.json"]
        else:
            names = [record["filename"]]
        for name in names:
            path = directory / name
            read_json(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"].get(name):
                raise ValueError("Archived evidence hash differs")
        if record["kind"] == "capsule":
            return {"target": read_json(directory / "baseline.json"),
                    "hardened": read_json(directory / "hardened.json"), "index": None}
        pair = read_json(directory / record["filename"])
        return {"target": pair["search"], "hardened": pair["hardened"], "index": pair["index"]}

    def detail(self, record_id):
        if record_id in self.recordings:
            record = self.recordings[record_id]
            data = {k: v for k, v in record.items() if k not in {"directory", "filename", "kind"}}
            data.update(status="RECORDED", pair=self._archived_pair(record), counts=None)
        else:
            journal = self._journal(record_id)
            data = self._job_summary(record_id, journal)
            data["pair"] = representative(self._directory(record_id), journal)
        with self.lock:
            data["verification"] = deepcopy(self.verifications.get(record_id))
            data["activity"] = self.active_kind if self.active == record_id else None
        return data

    def _launch(self, record_id, kind, worker):
        self.active, self.active_kind = record_id, kind
        self.cancel_event = threading.Event()

        def guarded():
            try:
                worker()
            finally:
                with self.lock:
                    self.active = None
                    self.active_kind = None
        self.thread = threading.Thread(target=guarded, name="worldfuzz-local-job", daemon=False)
        self.thread.start()

    def start(self, config):
        configuration(config)
        with self.lock:
            if self.active:
                raise BusyError("A local operation is already running")
            if len([p for p in self.root.iterdir() if re.fullmatch(r"[0-9a-f]{32}", p.name)]) >= 100:
                raise ValueError("This workspace has 100 experiments. Start with a new output folder.")
            record_id = uuid.uuid4().hex
            directory = self.root / record_id
            new_experiment(directory, config)

            def work():
                try:
                    run_experiment(directory, self.cancel_event.is_set)
                except (Exception, KeyboardInterrupt):
                    # Preserve a readable stopped journal even after storage/finalization errors.
                    try:
                        journal = read_json(directory / "experiment.json")
                        journal.update(status="FAILED", finished_at=now(),
                                       error="Local execution stopped. Inspect the saved journal.")
                        write_json(directory / "experiment.json", journal)
                        # Recovery changed the journal; an old manifest is no longer valid.
                        (directory / "manifest.json").unlink(missing_ok=True)
                    except (OSError, ValueError):
                        pass
            self._launch(record_id, "search", work)
            return {"id": record_id}

    def cancel(self, record_id):
        with self.lock:
            if self.active != record_id or self.active_kind != "search":
                raise BusyError("That search is no longer running")
            self.cancel_event.set()
        return {"status": "CANCELLATION_REQUESTED", "message": "Stops after the current local simulation."}

    def verify(self, record_id):
        with self.lock:
            if self.active:
                raise BusyError("A local operation is already running")
            detail = self.detail(record_id)
            if detail["origin"] == "local" and detail["status"] in {"QUEUED", "RUNNING"}:
                raise BusyError("Search must stop before verification")
            report_dir = self.root / "verification"
            report_dir.mkdir(exist_ok=True)
            if report_dir.is_symlink():
                raise ValueError("Verification output must be a regular directory")
            report_path = report_dir / (uuid.uuid4().hex + ".json")
            initial = {"status": "RUNNING", "started_at": now()}
            self.verifications[record_id] = initial
            def checkpoint(counts):
                write_json(report_path, {"record_id": record_id, **initial, **counts})
            meter = ReplayMeter(checkpoint)
            checkpoint(meter.receipt())

            def work():
                try:
                    result = (verify_pair(detail["pair"], meter=meter) if detail["origin"] == "recording"
                              else verify_experiment(self._directory(record_id), meter=meter))
                except Exception:
                    result = {"status": "FAILED", "message": "Evidence verification failed. No retry was made.",
                              "verified_at": now(), **meter.receipt()}
                result["started_at"] = initial["started_at"]
                try:
                    write_json(report_path, {"record_id": record_id, **result})
                except OSError:
                    result = {**result, "report_saved": False}
                with self.lock:
                    self.verifications[record_id] = result
            self._launch(record_id, "verification", work)
        return {"status": "RUNNING", "id": record_id}

    def download(self, record_id):
        with self.lock:
            if self.active == record_id and self.active_kind == "search":
                raise BusyError("Stop the search before downloading its evidence")
        if record_id in self.recordings:
            record = self.recordings[record_id]
            self._archived_pair(record)
            directory = record["directory"]
            names = CAPSULE_FILES if record["kind"] == "capsule" else {
                "plan.json", "pilot.json", "result.json", "manifest.json",
                *(f"representative-{c.case_id}-{m}.json" for c in TUNING_CASES for m in ("random", "crossing_time"))}
            command = "python -m worldfuzz verify PATH" if record["kind"] == "capsule" else "python -m worldfuzz pilot-verify PATH"
        else:
            directory = self._directory(record_id)
            names = EXPERIMENT_FILES
            command = "python -m worldfuzz experiment-verify PATH"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(names):
                path = directory / name
                if path.exists():
                    read_json(path)
                    archive.writestr(name, path.read_bytes())
            archive.writestr("REPLAY.txt", "Extract this folder, then run from the WorldFuzz checkout:\n" + command +
                             "\nReplace PATH with the extracted folder. Verification runs local simulations only.\n"
                             "Recorded pilot downloads contain the full pilot evidence; its CLI verifier reruns 388 simulations.\n")
            with self.lock:
                report = self.verifications.get(record_id)
                if report and report["status"] != "RUNNING":
                    archive.writestr("dashboard-verification.json", json_bytes(report))
        return buffer.getvalue()

    def close(self):
        self.cancel_event.set()
        if self.thread:
            self.thread.join(timeout=35)


def make_server(app, port=8765):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WorldFuzzLocal"

        def log_message(self, *_args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def _valid_host(self):
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return len(self.headers.get_all("Host", [])) == 1 and self.headers.get("Host", "").lower() in hosts

        def _send(self, status, payload, content_type="application/json", download=None):
            data = json_bytes(payload) if content_type == "application/json" else payload
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="worldfuzz-{download}.zip"')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self._valid_host():
                return self._send(403, {"error": "Use the local dashboard address"})
            path = urlsplit(self.path).path
            try:
                assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"),
                          "/app.mjs": ("app.mjs", "text/javascript; charset=utf-8"), "/favicon.svg": ("favicon.svg", "image/svg+xml"),
                          "/regression": ("regression.html", "text/html; charset=utf-8"),
                          "/data": ("data.html", "text/html; charset=utf-8"),
                          "/data.mjs": ("data.mjs", "text/javascript; charset=utf-8"),
                          "/benchmarks": ("published-benchmarks.html", "text/html; charset=utf-8"),
                          "/regression.css": ("regression.css", "text/css; charset=utf-8"),
                          "/guided-project.mjs": ("guided-project.mjs", "text/javascript; charset=utf-8"),
                          "/regression.mjs": ("regression.mjs", "text/javascript; charset=utf-8")}
                if path in assets:
                    name, mime = assets[path]
                    return self._send(200, (ASSETS / name).read_bytes(), mime)
                if path in ("/api/data", "/api/data/export"):
                    from .corpus import Corpus
                    corpus = Corpus()
                    if path == "/api/data":
                        return self._send(200, corpus.listing())
                    query = parse_qs(urlsplit(self.path).query)
                    if set(query) - {"domain", "status", "label", "split"} or any(len(v) != 1 for v in query.values()):
                        raise ValueError("Invalid export filters")
                    payload = corpus.export(**{k: v[0] for k,v in query.items()})
                    return self._send(200, payload, "application/x-ndjson")
                if path == "/api/bootstrap":
                    return self._send(200, app.bootstrap())
                if path == "/api/experiments":
                    return self._send(200, app.listing())
                if path == "/api/regressions/bootstrap":
                    return self._send(200, app.regressions().bootstrap())
                if path == "/api/regressions":
                    return self._send(200, app.regressions().listing())
                match = re.fullmatch(r"/api/regressions/([0-9a-f]{32})(/download)?", path)
                if match:
                    record_id, download = match.groups()
                    if download:
                        return self._send(200, app.regressions().download(record_id), "application/zip", record_id)
                    return self._send(200, app.regressions().detail(record_id))
                match = re.fullmatch(r"/api/experiments/([a-z0-9_-]+)(/download)?", path)
                if match:
                    record_id, download = match.groups()
                    if download:
                        return self._send(200, app.download(record_id), "application/zip", record_id)
                    return self._send(200, app.detail(record_id))
                self._send(404, {"error": "Not found"})
            except BusyError as exc:
                self._send(409, {"error": str(exc)})
            except ImportError:
                self._send(503, {"error": 'Navigation comparison needs its optional dependency. Install with: pip install -e ".[navigation]"'})
            except (OSError, ValueError, KeyError, TypeError):
                self._send(400, {"error": "Evidence could not be read or validated"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (not self._valid_host() or self.headers.get("X-WorldFuzz-Token") != app.token
                    or (origin is not None and origin != "http://" + self.headers.get("Host"))):
                return self._send(403, {"error": "Reload the local dashboard before starting an operation"})
            try:
                path = urlsplit(self.path).path
                is_regression = path == "/api/regressions" or path.startswith("/api/regressions/")
                if self.headers.get("Content-Type") != "application/json" or self.headers.get("Transfer-Encoding"):
                    raise ValueError("Use a JSON request")
                if len(self.headers.get_all("Content-Length", [])) != 1:
                    raise ValueError("Missing request length")
                size = int(self.headers["Content-Length"])
                if not 0 < size <= (65536 if is_regression else 4096):
                    raise ValueError("Request is too large")
                body = self.rfile.read(size)
                data = (json.loads(body, object_pairs_hook=_project_object, parse_constant=_project_constant)
                        if is_regression else json.loads(body))
                if path == "/api/data/import-included" and data == {}:
                    from .corpus import Corpus, import_navigation
                    receipt = import_navigation(Corpus(), Path(__file__).resolve().parents[1] / "examples/published-street-cases-20260910")
                    return self._send(200, receipt)
                if path == "/api/regressions":
                    return self._send(202, app.regressions().start(data))
                match = re.fullmatch(r"/api/regressions/([0-9a-f]{32})/(cancel|verify)", path)
                if match and data == {}:
                    record_id, action = match.groups()
                    facade = app.regressions()
                    return self._send(202, facade.cancel(record_id) if action == "cancel" else facade.verify(record_id))
                if path == "/api/experiments":
                    return self._send(202, app.start(data))
                match = re.fullmatch(r"/api/experiments/([a-z0-9_-]+)/(cancel|verify)", path)
                if match and data == {}:
                    record_id, action = match.groups()
                    return self._send(202, app.cancel(record_id) if action == "cancel" else app.verify(record_id))
                self._send(404, {"error": "Not found"})
            except BusyError as exc:
                self._send(409, {"error": str(exc)})
            except ImportError:
                self._send(503, {"error": 'Navigation comparison needs its optional dependency. Install with: pip install -e ".[navigation]"'})
            except (ValueError, KeyError, TypeError) as exc:
                self._send(400, {"error": str(exc)[:300] if is_regression else "Invalid local experiment request"})
            except OSError:
                self._send(500, {"error": "Local operation could not be saved"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def serve(output, port=8765, open_browser=False):
    app = Dashboard(output)
    server = make_server(app, port)
    address = f"http://127.0.0.1:{server.server_port}/data"
    print(f"WorldFuzz local dashboard: {address}\nLocal simulations only. Stop with Ctrl+C.", flush=True)
    if open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
    return 0
