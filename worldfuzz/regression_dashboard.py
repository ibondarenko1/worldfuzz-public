"""Data-only navigation comparison facade sharing the dashboard's single worker."""
from copy import deepcopy
from importlib.util import find_spec
import re
import uuid

from . import regression
from .dashboard import BusyError
from .engine import write_json
from .experiment import now, read_json


class RegressionDashboard:
    def __init__(self, parent):
        self.parent = parent
        self.root = parent.root / "regressions"
        if self.root.is_symlink():
            raise ValueError("Comparison output must be a regular directory")
        self.root.mkdir(exist_ok=True)
        self.verifications = {}
        reports = self.root / "verification"
        if reports.is_dir() and not reports.is_symlink():
            for path in reports.glob("*.json"):
                try:
                    report = read_json(path)
                    record_id = report.pop("record_id")
                    if not re.fullmatch(r"[0-9a-f]{32}", record_id):
                        continue
                    if report["status"] == "RUNNING":
                        report["status"] = "INTERRUPTED"
                    if report.get("started_at", "") >= self.verifications.get(record_id, {}).get("started_at", ""):
                        self.verifications[record_id] = report
                except (OSError, ValueError, KeyError, TypeError):
                    continue

    def _directory(self, record_id):
        if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id):
            raise ValueError("Unknown comparison")
        path = self.root / record_id
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Unknown comparison")
        return path

    def _summary(self, record_id, result):
        journal = result["journal"]
        state = journal["status"]
        with self.parent.lock:
            if state in {"QUEUED", "RUNNING"} and self.parent.active != record_id:
                state = "INTERRUPTED"
        error = journal.get("error")
        failure = self.root / record_id / "dashboard-error.json"
        if failure.is_file() and not failure.is_symlink():
            failure_report = read_json(failure)
            state, error = "FAILED", failure_report["message"]
        return {"id": record_id, "title": result["project"]["name"], "status": state,
                "counts": journal["counts"], "limits": journal["limits"],
                "started_at": journal.get("started_at", journal.get("created_at", "")),
                "error": error}

    def listing(self):
        runs = []
        for path in self.root.iterdir():
            if not re.fullmatch(r"[0-9a-f]{32}", path.name) or path.is_symlink() or not path.is_dir():
                continue
            try:
                runs.append(self._summary(path.name, regression.get_regression(path)))
            except (OSError, ValueError, KeyError, TypeError):
                runs.append({"id": path.name, "title": "Unreadable comparison evidence", "status": "ERROR"})
        runs.sort(key=lambda item: item.get("started_at", ""), reverse=True)
        with self.parent.lock:
            activity = ({"id": self.parent.active, "kind": self.parent.active_kind}
                        if self.parent.active else None)
        return {"runs": runs, "activity": activity}

    def bootstrap(self):
        from .navigation_tasks import task_catalog
        return {"csrf_token": self.parent.token, "default_project": regression.default_project(),
                "tasks": task_catalog(), **self.listing()}

    def detail(self, record_id):
        result = regression.get_regression(self._directory(record_id))
        detail = {**result, **self._summary(record_id, result)}
        with self.parent.lock:
            detail["verification"] = deepcopy(self.verifications.get(record_id, result.get("verification")))
            detail["activity"] = self.parent.active_kind if self.parent.active == record_id else None
        return detail

    def start(self, project):
        normalized = regression.validate_project(project)
        if any(find_spec(name) is None for name in ("numpy", "matplotlib")):
            raise ImportError("Navigation dependencies are missing")
        with self.parent.lock:
            if self.parent.active:
                raise BusyError("A local operation is already running")
            if sum(bool(re.fullmatch(r"[0-9a-f]{32}", path.name)) for path in self.root.iterdir()) >= 100:
                raise ValueError("This workspace has 100 comparisons. Start with a new output folder.")
            record_id = uuid.uuid4().hex
            directory = self.root / record_id
            regression.new_regression(directory, normalized)

            def work():
                try:
                    regression.run_regression(directory, cancelled=self.parent.cancel_event.is_set)
                except Exception:
                    # The engine preserves partial operations. A separate receipt retains
                    # unexpected facade/storage failures without rewriting hashed evidence.
                    try:
                        write_json(directory / "dashboard-error.json", {
                            "status": "FAILED", "finished_at": now(),
                            "message": "Local execution stopped. Completed evidence was preserved."})
                    except OSError:
                        pass
            self.parent._launch(record_id, "regression_search", work)
        return {"id": record_id}

    def cancel(self, record_id):
        with self.parent.lock:
            if self.parent.active != record_id or self.parent.active_kind != "regression_search":
                raise BusyError("That comparison is no longer running")
            self.parent.cancel_event.set()
        return {"status": "CANCELLATION_REQUESTED", "message": "Stops after the current local simulation."}

    def verify(self, record_id):
        with self.parent.lock:
            if self.parent.active:
                raise BusyError("A local operation is already running")
            self.detail(record_id)
            reports = self.root / "verification"
            if reports.is_symlink():
                raise ValueError("Verification output must be a regular directory")
            reports.mkdir(exist_ok=True)
            report_path = reports / (uuid.uuid4().hex + ".json")
            initial = {"status": "RUNNING", "started_at": now(), "started_simulations": 0,
                       "completed_simulations": 0, "provider_calls": 0}
            self.verifications[record_id] = initial

            def checkpoint(counts):
                receipt = {**initial, **counts, "status": "RUNNING"}
                write_json(report_path, {"record_id": record_id, **receipt})
                with self.parent.lock:
                    self.verifications[record_id] = receipt
            checkpoint({})

            def work():
                try:
                    result = regression.verify_regression(self._directory(record_id), checkpoint=checkpoint)
                except Exception:
                    with self.parent.lock:
                        result = {**self.verifications[record_id], "status": "FAILED",
                                  "message": "Evidence verification failed. No retry was made."}
                result = {**result, "started_at": initial["started_at"]}
                try:
                    write_json(report_path, {"record_id": record_id, **result})
                except OSError:
                    result["report_saved"] = False
                with self.parent.lock:
                    self.verifications[record_id] = result
            self.parent._launch(record_id, "regression_verification", work)
        return {"id": record_id, "status": "RUNNING"}

    def download(self, record_id):
        with self.parent.lock:
            if self.parent.active == record_id:
                raise BusyError("Wait for this operation to stop before downloading evidence")
            return regression.export_regression(self._directory(record_id))
