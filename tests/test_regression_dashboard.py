"""Cross-workspace operation and HTTP contracts; no provider or planner calls."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from worldfuzz import regression
from worldfuzz.dashboard import BusyError, Dashboard, make_server


class RegressionDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Dashboard(self.temp.name)
        self.facade = self.app.regressions()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_global_worker_prevents_legacy_and_navigation_overlap_and_keeps_cancelled_journal(self):
        entered, release = threading.Event(), threading.Event()
        actual_run = regression.run_regression
        def waiting(directory, cancelled):
            entered.set()
            release.wait(3)
            return actual_run(directory, cancelled)
        with patch("worldfuzz.regression_dashboard.find_spec", return_value=object()), \
                patch.object(regression, "run_regression", side_effect=waiting), \
                patch.object(regression, "simulate_navigation", side_effect=AssertionError("Cancelled before planning")):
            record = self.facade.start(regression.default_project())
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(BusyError):
                    self.app.start({"case_id": "tuning-clear-crossing", "method": "random", "policy": "baseline", "budget": 1})
                with self.assertRaises(BusyError):
                    self.facade.start(regression.default_project())
                with self.assertRaises(BusyError):
                    self.app.verify("recorded-nemotron")
                self.facade.cancel(record["id"])
            finally:
                release.set()
                self.app.thread.join(4)
        detail = self.facade.detail(record["id"])
        self.assertEqual(detail["status"], "CANCELLED")
        self.assertEqual(detail["counts"]["started_simulations"], 0)
        self.assertIsNone(self.app.active)
        self.assertIsNone(detail["selected"])

    def test_unexpected_worker_failure_remains_visible_after_restart(self):
        with patch("worldfuzz.regression_dashboard.find_spec", return_value=object()), \
                patch.object(regression, "run_regression", side_effect=OSError("Injected storage error")):
            record = self.facade.start(regression.default_project())
            self.app.thread.join(3)
        restored = Dashboard(self.temp.name)
        try:
            detail = restored.regressions().detail(record["id"])
            self.assertEqual(detail["status"], "FAILED")
            self.assertEqual(detail["counts"]["completed_simulations"], 0)
            self.assertNotIn("Injected", detail["error"])
        finally:
            restored.close()

    def test_verification_failure_keeps_started_attempt_counts(self):
        record_id = "b" * 32
        regression.new_regression(self.facade.root / record_id, regression.default_project())
        regression.run_regression(self.facade.root / record_id, cancelled=lambda: True)
        def failing(_directory, checkpoint):
            checkpoint({"started_simulations": 1, "completed_simulations": 0})
            raise OSError("Injected verifier failure")
        with patch.object(regression, "verify_regression", side_effect=failing):
            self.facade.verify(record_id)
            self.app.thread.join(3)
        report = self.facade.detail(record_id)["verification"]
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["started_simulations"], 1)
        self.assertEqual(report["completed_simulations"], 0)
        restored = Dashboard(self.temp.name)
        try:
            self.assertEqual(restored.regressions().detail(record_id)["verification"], report)
        finally:
            restored.close()

    def test_http_guards_reject_ambiguous_projects_before_execution_and_serve_assets(self):
        server = make_server(self.app, 0)
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        worker.start()
        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            selected = {"Content-Type": "application/json"} if body else {}
            selected.update(headers or {})
            connection.request(method, path, body, selected)
            response = connection.getresponse()
            result = response.status, response.read()
            connection.close()
            return result
        token = {"X-WorldFuzz-Token": self.app.token}
        project = json.dumps(regression.default_project())
        try:
            for path in ("/regression", "/regression.css", "/regression.mjs"):
                self.assertEqual(request("GET", path)[0], 200)
            self.assertEqual(request("GET", "/api/regressions/bootstrap")[0], 200)
            with patch.object(self.facade, "start", return_value={"id": "a" * 32}) as start:
                self.assertEqual(request("POST", "/api/regressions", project)[0], 403)
                self.assertEqual(request("POST", "/api/regressions", project, {**token, "Origin": "https://example.com"})[0], 403)
                self.assertEqual(request("POST", "/api/regressions", project, {**token, "Host": "example.com"})[0], 403)
                for invalid in ('{"budget":1,"budget":2}', '{"budget":NaN}', "x" * 65537):
                    self.assertEqual(request("POST", "/api/regressions", invalid, token)[0], 400)
                start.assert_not_called()
                self.assertEqual(request("POST", "/api/regressions", project, token)[0], 202)
                self.assertEqual(start.call_count, 1)
            self.assertEqual(request("GET", "/api/regressions/../../project.json")[0], 404)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)

    def test_navigation_missing_dependency_does_not_affect_old_dashboard(self):
        with patch("worldfuzz.regression_dashboard.find_spec", return_value=None):
            self.assertTrue(self.app.bootstrap()["recordings"])
            with self.assertRaises(ImportError):
                self.facade.start(regression.default_project())
        self.assertEqual(self.facade.listing()["runs"], [])


if __name__ == "__main__":
    unittest.main()
