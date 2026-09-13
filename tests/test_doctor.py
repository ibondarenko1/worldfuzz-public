"""Offline regressions for configuration claims, redaction and pre-call output gates."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

from worldfuzz.cli import main
from worldfuzz.doctor import diagnose


MOCK_KEY = "mock-private-key-doctor-never-real"
MOCK_MODEL = "nvidia/Nemotron-MOCK-only"


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "NEBIUS_API_KEY": MOCK_KEY,
            "NEBIUS_MODEL": MOCK_MODEL,
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

        # Exercise the actual planner/doctor without depending on a private store
        # or the optional encrypted-transfer dependency on the test machine.
        connection = ModuleType("worldfuzz.connection")
        connection.load_credentials = MagicMock(side_effect=lambda: (
            os.environ.get("NEBIUS_API_KEY", ""),
            os.environ.get("NEBIUS_MODEL", ""),
        ))
        self.load_credentials = connection.load_credentials
        self.module = patch.dict(sys.modules, {"worldfuzz.connection": connection})
        self.module.start()
        self.addCleanup(self.module.stop)

        self.transport = patch("worldfuzz.nebius.build_opener")
        self.open = self.transport.start().return_value.open
        self.addCleanup(self.transport.stop)

    def catalog_response(self, model=MOCK_MODEL):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read.return_value = json.dumps({"data": [
            {"id": model, "owned_by": "system"},
            {"id": "other/Unrelated-MOCK", "owned_by": "system"},
        ]}).encode()
        self.open.return_value = response

    def invoke(self, arguments):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_local_diagnose_never_calls_network_or_claims_inference(self):
        report = diagnose()
        self.assertEqual(report["status"], "configured")
        self.assertTrue(report["configuration_present"])
        self.assertFalse(report["network_requested"])
        self.assertFalse(report["catalog_verified"])
        self.assertFalse(report["inference_verified"])
        self.assertEqual(report["credit_balance"], "unverified")
        self.open.assert_not_called()

    def test_catalog_diagnose_only_sends_get_and_never_claims_inference(self):
        self.catalog_response()
        report = diagnose(catalog=True)
        self.assertEqual(report["status"], "catalog_verified")
        self.assertTrue(report["catalog_accessible"])
        self.assertTrue(report["catalog_verified"])
        self.assertFalse(report["inference_verified"])
        self.assertEqual(report["eligible_models"], [MOCK_MODEL])
        self.open.assert_called_once()
        request = self.open.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertTrue(request.full_url.endswith("/models"))
        self.assertIsNone(request.data)

    def test_missing_model_still_lists_eligible_models_without_completion(self):
        os.environ.pop("NEBIUS_MODEL")
        self.catalog_response()
        report = diagnose(catalog=True)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["catalog_accessible"])
        self.assertEqual(report["eligible_models"], [MOCK_MODEL])
        self.assertFalse(report["configuration_present"])
        self.assertFalse(report["catalog_verified"])
        self.assertFalse(report["inference_verified"])
        self.open.assert_called_once()
        self.assertEqual(self.open.call_args.args[0].method, "GET")

    def test_missing_key_prevents_network_even_when_catalog_requested(self):
        os.environ.pop("NEBIUS_API_KEY")
        report = diagnose(catalog=True)
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["credential_present"])
        self.assertFalse(report["catalog_verified"])
        self.assertFalse(report["inference_verified"])
        self.assertEqual(report["error_code"], "missing_configuration")
        self.open.assert_not_called()

    def test_unavailable_selected_model_does_not_pass_catalog_gate(self):
        self.catalog_response("nvidia/Nemotron-DIFFERENT-MOCK")
        report = diagnose(catalog=True)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["configuration_present"])
        self.assertTrue(report["catalog_accessible"])
        self.assertFalse(report["catalog_verified"])
        self.assertFalse(report["inference_verified"])

    def test_report_does_not_disclose_key_and_names_configuration_source(self):
        report = diagnose()
        self.assertEqual(report["credential_source"], "process_environment")
        self.assertNotIn(MOCK_KEY, json.dumps(report))

        os.environ.pop("NEBIUS_API_KEY")
        self.load_credentials.side_effect = None
        self.load_credentials.return_value = (MOCK_KEY, MOCK_MODEL)
        report = diagnose()
        self.assertEqual(report["status"], "configured")
        self.assertEqual(report["credential_source"], "private_runtime_store")
        self.assertNotIn(MOCK_KEY, json.dumps(report))
        self.open.assert_not_called()

    def test_storage_exception_does_not_disclose_secret_details(self):
        self.load_credentials.side_effect = OSError("Cannot read " + MOCK_KEY)
        report = diagnose(catalog=True)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["error_code"], "configuration_unavailable")
        self.assertNotIn(MOCK_KEY, json.dumps(report))
        self.open.assert_not_called()

    def test_existing_report_aborts_before_live_preflight_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing-report.json"
            original = '{"original_evidence":true}\n'
            output.write_text(original, encoding="utf-8")
            with patch("worldfuzz.nebius.preflight") as preflight:
                code, stdout, stderr = self.invoke([
                    "preflight", "--live", "--out", str(output),
                ])
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertEqual(json.loads(stderr)["status"], "ERROR")
            self.assertEqual(output.read_text(encoding="utf-8"), original)
            preflight.assert_not_called()
            self.open.assert_not_called()

    def test_cli_saves_same_sanitized_report_as_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reports" / "doctor.json"
            code, stdout, stderr = self.invoke(["doctor", "--out", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            saved = output.read_text(encoding="utf-8")
            self.assertEqual(json.loads(saved), json.loads(stdout))
            self.assertEqual(json.loads(saved)["status"], "configured")
            self.assertNotIn(MOCK_KEY, saved + stdout)
            self.open.assert_not_called()

    def test_cli_blocked_report_has_nonzero_status_and_is_saved(self):
        os.environ.pop("NEBIUS_API_KEY")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "blocked.json"
            code, stdout, stderr = self.invoke(["doctor", "--out", str(output)])
            self.assertEqual(code, 2)
            self.assertEqual(stderr, "")
            self.assertEqual(json.loads(stdout)["status"], "blocked")
            self.assertEqual(json.loads(output.read_text()), json.loads(stdout))
            self.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
