"""Focused local dashboard/evidence contracts, including real loopback HTTP."""
from copy import deepcopy
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from worldfuzz import experiment
from worldfuzz.dashboard import BusyError, Dashboard, make_server
from worldfuzz.engine import write_json


def config(case="tuning-clear-crossing", method="crossing_time", budget=1):
    return {"case_id": case, "method": method, "policy": "protective_distance", "budget": budget}


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "run"

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, value=None):
        return experiment.new_experiment(self.directory, value or config())

    def test_rejects_paid_strategy_weakened_case_and_bad_limits(self):
        for changes in ({"method": "nemotron"}, {"budget": True}, {"budget": 49},
                        {"case_id": "heldout"}, {"policy": "unknown"}, {"api_key": "fake"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                experiment.configuration({**config(), **changes})

    def test_cancellation_before_start_preserves_zero_simulation_evidence(self):
        self.prepare()
        with patch.object(experiment, "simulate", side_effect=AssertionError("Must not simulate")):
            result = experiment.run_experiment(self.directory, lambda: True)
            verified = experiment.verify_experiment(self.directory)
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(verified["local_simulations"], 0)
        self.assertFalse(verified["artifact_complete"])

    def test_cancel_between_policies_preserves_unpaired_real_result(self):
        self.prepare()
        calls = []
        original = experiment.simulate
        def run(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs)
        with patch.object(experiment, "simulate", side_effect=run):
            result = experiment.run_experiment(self.directory, lambda: bool(calls))
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["counts"]["completed_simulations"], 1)
        self.assertIsNone(experiment.representative(self.directory, result))
        self.assertEqual(experiment.verify_experiment(self.directory)["local_simulations"], 1)

    def test_no_finding_remains_inspectable_and_is_not_a_safety_claim(self):
        self.prepare(config("tuning-interrupted-observation", "random"))
        result = experiment.run_experiment(self.directory)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["counts"]["findings"], 0)
        self.assertIsNotNone(experiment.representative(self.directory, result))
        self.assertEqual(experiment.verify_experiment(self.directory)["local_simulations"], 2)

    def test_simulator_failure_preserves_uncertain_operation_and_never_retries(self):
        self.prepare()
        with patch.object(experiment, "simulate", side_effect=RuntimeError("Injected local failure")) as mock:
            result = experiment.run_experiment(self.directory)
        self.assertEqual(mock.call_count, 1)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["counts"]["unconfirmed_simulations"], 1)
        self.assertEqual(experiment.verify_experiment(self.directory)["unconfirmed_search_simulations"], 1)
        with self.assertRaises(ValueError):
            experiment.run_experiment(self.directory)

    def test_impossible_lifecycle_rejected_before_replay(self):
        journal = self.prepare()
        variants = [dict(status="COMPLETED"), dict(status="FAILED", finished_at=experiment.now()),
                    dict(status="QUEUED", finished_at=experiment.now()), dict(experiment_schema_version=True)]
        with patch.object(experiment, "simulate", side_effect=AssertionError("Must reject before replay")):
            for changes in variants:
                write_json(self.directory / "experiment.json", {**journal, **changes})
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    experiment.verify_experiment(self.directory)

    def test_counter_keeps_simulations_when_replay_mismatches(self):
        self.prepare()
        experiment.run_experiment(self.directory)
        receipts = []
        meter = experiment.ReplayMeter(lambda receipt: receipts.append(deepcopy(receipt)))
        original = experiment.simulate
        def wrong(*args, **kwargs):
            run = original(*args, **kwargs)
            run["summary"]["minimum_distance_m"] += 1
            return run
        with patch.object(experiment, "simulate", side_effect=wrong), self.assertRaises(ValueError):
            experiment.verify_experiment(self.directory, meter=meter)
        self.assertEqual(meter.receipt()["local_simulations"], 1)
        self.assertEqual(receipts[0]["unconfirmed_simulations"], 1)
        self.assertEqual(receipts[-1]["unconfirmed_simulations"], 0)

    def test_duplicate_and_nonfinite_json_rejected(self):
        path = Path(self.temp.name) / "bad.json"
        for value in ('{"x":1,"x":2}', '{"x":NaN}'):
            path.write_text(value)
            with self.assertRaises(ValueError):
                experiment.read_json(path)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Dashboard(self.temp.name)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_recordings_load_without_simulation_or_provider_configuration(self):
        with patch.object(experiment, "simulate", side_effect=AssertionError("Loading is not replay")):
            live = self.app.detail("recorded-nemotron")
            self.assertEqual(live["origin"], "recording")
            self.assertEqual(live["pair"]["target"]["scenario"], live["pair"]["hardened"]["scenario"])
            for record_id in self.app.recordings:
                self.assertIsNotNone(self.app.detail(record_id)["pair"])

    def test_concurrent_start_and_cancel_have_no_hidden_second_job(self):
        entered, release = threading.Event(), threading.Event()
        real = experiment.run_experiment
        def wait_then_run(directory, cancelled):
            entered.set()
            release.wait(3)
            return real(directory, cancelled)
        with patch("worldfuzz.dashboard.run_experiment", side_effect=wait_then_run):
            result = self.app.start(config())
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(BusyError):
                    self.app.start(config())
                self.app.cancel(result["id"])
            finally:
                release.set()
                self.app.thread.join(4)
        detail = self.app.detail(result["id"])
        self.assertEqual(detail["status"], "CANCELLED")
        self.assertEqual(detail["counts"]["completed_simulations"], 0)

    def test_restart_marks_incomplete_search_and_restores_verification(self):
        record_id = "a" * 32
        experiment.new_experiment(Path(self.temp.name) / record_id, config())
        reports = Path(self.temp.name) / "verification"
        reports.mkdir()
        write_json(reports / "receipt.json", {"record_id": record_id, "status": "RUNNING",
                   "started_at": experiment.now(), "started_simulations": 1, "local_simulations": 0})
        restored = Dashboard(self.temp.name)
        self.assertEqual(restored.detail(record_id)["status"], "INTERRUPTED")
        self.assertEqual(restored.detail(record_id)["verification"]["status"], "INTERRUPTED")
        self.assertIsNone(restored.active)

    def test_http_search_verify_download_and_local_boundary(self):
        server = make_server(self.app, 0)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        def request(method, path, value=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            body = json.dumps(value) if value is not None else None
            chosen = {"Content-Type": "application/json"} if body else {}
            chosen.update(headers or {})
            conn.request(method, path, body, chosen)
            response = conn.getresponse()
            data, status, response_headers = response.read(), response.status, dict(response.getheaders())
            conn.close()
            return status, data, response_headers
        try:
            status, body, headers = request("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn(b"WorldFuzz", body)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            for path in ("/app.mjs", "/app.css", "/favicon.svg"):
                self.assertEqual(request("GET", path)[0], 200)
            self.assertEqual(request("GET", "/api/bootstrap", headers={"Host": "remote.invalid"})[0], 403)
            self.assertEqual(request("POST", "/api/experiments", config())[0], 403)
            auth = {"X-WorldFuzz-Token": self.app.token}
            self.assertEqual(request("POST", "/api/experiments", config(), {**auth, "Origin": "https://remote.invalid"})[0], 403)
            self.assertEqual(request("POST", "/api/experiments", {**config(), "method": "nemotron"}, auth)[0], 400)
            status, body, _ = request("POST", "/api/experiments", config(), auth)
            self.assertEqual(status, 202)
            record_id = json.loads(body)["id"]
            self.app.thread.join(5)
            detail = self.app.detail(record_id)
            self.assertEqual(detail["status"], "COMPLETED")
            self.assertEqual(detail["counts"]["completed_simulations"], 2)
            self.assertEqual(request("POST", f"/api/experiments/{record_id}/verify", {}, auth)[0], 202)
            self.app.thread.join(5)
            self.assertEqual(self.app.detail(record_id)["verification"]["local_simulations"], 2)
            status, zipped, _ = request("GET", f"/api/experiments/{record_id}/download")
            self.assertEqual(status, 200)
            with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
                self.assertEqual(set(archive.namelist()), {"experiment.json", "representative.json", "manifest.json", "REPLAY.txt", "dashboard-verification.json"})
                self.assertEqual(json.loads(archive.read("experiment.json"))["counts"]["findings"], 1)
            self.assertEqual(request("GET", "/api/experiments/not-a-run/download")[0], 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
