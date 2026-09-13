"""Offline adapter tests. All provider identities/responses here are MOCK fixtures."""
from io import BytesIO
import json
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from worldfuzz.nebius import (DEFAULT_BASE_URL, MAX_RESPONSE_BYTES, NebiusError,
                             NemotronPlanner, TIMEOUT_SECONDS, _NoRedirect, preflight)
from worldfuzz.schema import ScenarioSpec


MOCK_MODEL = "nvidia/Nemotron-MOCK-only"
MOCK_KEY = "mock-key-never-a-real-credential"


def mock_response(document):
    response = MagicMock()
    response.__enter__.return_value = response
    response.headers = {"x-request-id": "mock-request-001"}
    raw = document if isinstance(document, bytes) else json.dumps(document).encode()
    response.read.return_value = raw
    return response


def mock_catalog(model=MOCK_MODEL):
    return mock_response({"data": [{"id": model, "owned_by": "system"}], "object": "list"})


def mock_completion(output, **changes):
    data = {"id": "mock-completion-001", "object": "chat.completion", "model": MOCK_MODEL,
            "choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": output if isinstance(output, str) else json.dumps(output)}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
    data.update(changes)
    return mock_response(data)


class NebiusOfflineMockTests(unittest.TestCase):
    def setUp(self):
        # Tests must stay isolated even after a real runtime key is provisioned.
        self.private_state = TemporaryDirectory(prefix="worldfuzz-nebius-mock-")
        self.addCleanup(self.private_state.cleanup)
        self.environment = patch.dict(os.environ, {"WORLDFUZZ_CONFIG_DIR": self.private_state.name}, clear=True)
        self.environment.start()
        self.transport = patch("worldfuzz.nebius.build_opener")
        self.open = self.transport.start().return_value.open
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.transport.stop)
        self.planner = NemotronPlanner(model=MOCK_MODEL, api_key=MOCK_KEY)

    def prepare_completion(self, output, **changes):
        self.open.side_effect = [mock_catalog(), mock_completion(output, **changes)]

    def test_missing_configuration_never_calls_network(self):
        result = preflight(live=True)
        self.assertEqual(result["status"], "missing_configuration")
        self.assertFalse(result["inference_verified"])
        self.open.assert_not_called()

    def test_local_configuration_is_not_inference_success(self):
        result = self.planner.preflight()
        self.assertEqual(result["status"], "configuration_present")
        self.assertFalse(result["inference_verified"])
        self.open.assert_not_called()

    def test_pending_campaign_configuration_and_token_cap_require_no_network(self):
        configuration = self.planner.campaign_configuration()
        self.assertEqual(configuration, {"model": MOCK_MODEL, "timeout_seconds": TIMEOUT_SECONDS,
                                         "max_completion_tokens": 4096, "automatic_retries": 0})
        self.assertNotIn(MOCK_KEY, json.dumps(configuration))
        single_cap = self.planner.proposal_token_limit(1)
        self.assertEqual(single_cap, 896)
        self.assertLessEqual(self.planner.proposal_token_limit(8), configuration["max_completion_tokens"])
        with self.assertRaises(NebiusError):
            self.planner.proposal_token_limit(True)
        self.open.assert_not_called()
        self.prepare_completion({"scenarios": [ScenarioSpec().as_dict()]})
        _, provenance = self.planner.propose([], 1, 7)
        payload = json.loads(self.open.call_args.args[0].data)
        self.assertEqual(payload["max_completion_tokens"], single_cap)
        self.assertEqual(provenance["max_completion_tokens"], single_cap)

    def test_non_nvidia_model_rejected_before_network(self):
        planner = NemotronPlanner(model="other/Nemotron-MOCK", api_key=MOCK_KEY)
        self.assertEqual(planner.preflight(live=True)["error_code"], "invalid_model")
        self.open.assert_not_called()

    def test_custom_endpoint_cannot_receive_bearer_secret(self):
        planner = NemotronPlanner(model=MOCK_MODEL, api_key=MOCK_KEY, base_url="https://example.test/v1")
        self.assertEqual(planner.preflight(live=True)["status"], "failed")
        self.open.assert_not_called()

    def test_catalog_lookup_needs_key_but_no_selected_model(self):
        self.open.return_value = mock_catalog()
        result = NemotronPlanner(api_key=MOCK_KEY).list_models()
        self.assertEqual(result, [{"id": MOCK_MODEL, "owned_by": "system", "nvidia_nemotron": True}])
        request = self.open.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.full_url, DEFAULT_BASE_URL + "/models")

    def test_unavailable_model_causes_no_completion(self):
        self.open.return_value = mock_catalog("nvidia/Nemotron-DIFFERENT-MOCK")
        result = self.planner.preflight(live=True)
        self.assertEqual(result["error_code"], "model_unavailable")
        self.assertEqual(self.open.call_count, 1)

    def test_mock_live_preflight_requires_catalog_schema_usage(self):
        self.prepare_completion({"ok": True})
        result = self.planner.preflight(live=True)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["inference_verified"])
        provenance = result["provenance"]
        self.assertEqual(provenance["returned_model"], MOCK_MODEL)
        self.assertEqual(provenance["usage"]["total_tokens"], 30)
        self.assertEqual(provenance["request_id"], "mock-request-001")
        self.assertEqual(provenance["response_id"], "mock-completion-001")
        self.assertEqual(len(provenance["prompt_sha256"]), 64)
        self.assertNotIn(MOCK_KEY, json.dumps(result))
        request = self.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, DEFAULT_BASE_URL + "/chat/completions")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(payload["max_completion_tokens"], 128)
        self.assertFalse(payload["store"])
        self.assertNotIn("tools", payload)
        self.assertEqual(self.open.call_args.kwargs["timeout"], TIMEOUT_SECONDS)

    def test_wrong_preflight_schema_is_not_pass(self):
        self.prepare_completion({"ok": True, "unexpected": "ignored?"})
        result = self.planner.preflight(live=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["provenance"]["validation_state"], "rejected")

    def test_missing_usage_is_not_verified(self):
        self.prepare_completion({"ok": True}, usage=None)
        self.assertEqual(self.planner.preflight(live=True)["error_code"], "missing_usage")

    def test_usage_budget_violation_is_not_verified(self):
        self.prepare_completion({"ok": True}, usage={"prompt_tokens": 20, "completion_tokens": 129, "total_tokens": 149})
        self.assertEqual(self.planner.preflight(live=True)["error_code"], "budget_exceeded")

    def test_model_substitution_is_not_verified(self):
        self.prepare_completion({"ok": True}, model="other/Substitute-MOCK")
        self.assertEqual(self.planner.preflight(live=True)["error_code"], "model_mismatch")

    def test_rejected_answer_preserves_known_usage_before_content_validation(self):
        cases = (
            ({"ok": True}, {"model": "other/Substitute-MOCK"}, "model_mismatch", 10),
            ({"ok": False}, {}, "schema_mismatch", 10),
            ('{"ok": true, "ok": false}', {}, "invalid_completion", 10),
            ({"ok": True}, {"usage": {"prompt_tokens": 20, "completion_tokens": 129,
                                      "total_tokens": 149}}, "budget_exceeded", 129),
            ({"ok": True}, {"usage": {"prompt_tokens": 20, "completion_tokens": 0,
                                      "total_tokens": 20}}, "invalid_usage", 0),
        )
        for output, changes, error, completion_tokens in cases:
            with self.subTest(error=error):
                self.prepare_completion(output, **changes)
                result = NemotronPlanner(model=MOCK_MODEL, api_key=MOCK_KEY).preflight(live=True)
                self.assertEqual(result["error_code"], error)
                provenance = result["provenance"]
                self.assertTrue(provenance["completion_request_attempted"])
                self.assertEqual(provenance["request_state"], "response_received")
                self.assertEqual(provenance["usage_state"], "known")
                self.assertEqual(provenance["usage"], {"prompt_tokens": 20,
                    "completion_tokens": completion_tokens, "total_tokens": 20 + completion_tokens})
                self.assertEqual(provenance["validation_state"], "rejected")
                self.assertNotIn(MOCK_KEY, json.dumps(result))

    def test_invalid_usage_retains_only_sanitized_counters_without_claiming_totals(self):
        for usage in (None, {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 31},
                      {"prompt_tokens": True, "completion_tokens": MOCK_KEY, "total_tokens": 30,
                       "provider_debug": MOCK_KEY}):
            with self.subTest(usage=usage):
                self.prepare_completion({"ok": True}, usage=usage)
                result = NemotronPlanner(model=MOCK_MODEL, api_key=MOCK_KEY).preflight(live=True)
                provenance = result["provenance"]
                self.assertEqual(provenance["request_state"], "response_received")
                self.assertEqual(provenance["usage_state"], "unknown")
                self.assertNotIn("usage", provenance)
                if isinstance(usage, dict):
                    self.assertEqual(provenance["reported_usage"], {
                        key: value for key, value in usage.items()
                        if key in ("prompt_tokens", "completion_tokens", "total_tokens")
                        and type(value) is int and value >= 0})
                self.assertNotIn(MOCK_KEY, json.dumps(result))

    def test_configuration_and_catalog_failures_do_not_claim_completion_attempt(self):
        self.open.side_effect = URLError(MOCK_KEY)
        results = [preflight(live=True), self.planner.preflight(live=True)]
        for result in results:
            provenance = result["provenance"]
            self.assertFalse(provenance["completion_request_attempted"])
            self.assertEqual(provenance["request_state"], "not_sent")
            self.assertEqual(provenance["usage_state"], "not_applicable")
            self.assertNotIn("usage", provenance)
        self.assertEqual(self.open.call_count, 1)

    def test_completion_transport_failure_keeps_usage_unknown_and_is_not_retried(self):
        self.open.side_effect = [mock_catalog(), URLError(MOCK_KEY)]
        result = self.planner.preflight(live=True)
        provenance = result["provenance"]
        self.assertEqual(result["error_code"], "transport_error")
        self.assertTrue(provenance["completion_request_attempted"])
        self.assertEqual(provenance["request_state"], "possibly_sent")
        self.assertEqual(provenance["usage_state"], "unknown")
        self.assertNotIn("usage", provenance)
        self.assertIsInstance(provenance["elapsed_ms"], (int, float))
        self.assertEqual(len(provenance["request_sha256"]), 64)
        self.assertNotIn(MOCK_KEY, json.dumps(result))
        self.assertEqual(self.open.call_count, 2)

    def test_completion_read_and_parse_failures_are_received_with_unknown_usage(self):
        failed_read = mock_response({})
        failed_read.read.side_effect = TimeoutError(MOCK_KEY)
        for response, code in ((failed_read, "transport_error"),
                               (mock_response(b"{invalid-json"), "invalid_response"),
                               (mock_response(b"x" * (MAX_RESPONSE_BYTES + 1)), "response_too_large")):
            with self.subTest(code=code):
                self.open.side_effect = [mock_catalog(), response]
                result = NemotronPlanner(model=MOCK_MODEL, api_key=MOCK_KEY).preflight(live=True)
                self.assertEqual(result["error_code"], code)
                provenance = result["provenance"]
                self.assertTrue(provenance["completion_request_attempted"])
                self.assertEqual(provenance["request_state"], "response_received")
                self.assertEqual(provenance["usage_state"], "unknown")
                self.assertNotIn("usage", provenance)
                self.assertIsInstance(provenance["elapsed_ms"], (int, float))
                self.assertNotIn(MOCK_KEY, json.dumps(result))

    def test_interruption_preserves_delivery_state_and_reraises_for_cancellation(self):
        interrupted_response = mock_response({})
        interrupted_response.read.side_effect = KeyboardInterrupt()
        cases = (([KeyboardInterrupt()], "not_sent", "not_applicable", 1),
                 ([mock_catalog(), KeyboardInterrupt()], "possibly_sent", "unknown", 2),
                 ([mock_catalog(), interrupted_response], "response_received", "unknown", 2))
        for responses, request_state, usage_state, calls in cases:
            with self.subTest(request_state=request_state):
                self.open.reset_mock()
                self.open.side_effect = responses
                planner = NemotronPlanner(model=MOCK_MODEL, api_key=MOCK_KEY)
                with self.assertRaises(KeyboardInterrupt) as raised:
                    planner.propose([], 1, 1)
                provenance = raised.exception.provenance
                self.assertEqual(provenance["request_state"], request_state)
                self.assertEqual(provenance["usage_state"], usage_state)
                self.assertEqual(provenance["validation_state"], "interrupted")
                self.assertIsInstance(provenance["elapsed_ms"], (int, float))
                self.assertIn("timestamp_utc", provenance)
                self.assertEqual(self.open.call_count, calls)

    def test_interruption_after_response_preserves_known_usage(self):
        self.prepare_completion({"scenarios": [ScenarioSpec().as_dict()]})
        with patch("worldfuzz.nebius._scenario", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.planner.propose([], 1, 1)
        provenance = raised.exception.provenance
        self.assertEqual(provenance["usage_state"], "known")
        self.assertEqual(provenance["usage"]["total_tokens"], 30)
        self.assertEqual(provenance["validation_state"], "interrupted")

    def test_truncation_and_tool_calls_are_rejected(self):
        for finish_reason, extra in (("length", {}), ("stop", {"tool_calls": [{"id": "mock"}]}),
                                      ("stop", {"refusal": "mock refusal"})):
            with self.subTest(finish_reason=finish_reason, extra=extra):
                planner = NemotronPlanner(model=MOCK_MODEL, api_key=MOCK_KEY)
                self.prepare_completion({}, choices=[{"finish_reason": finish_reason,
                    "message": {"content": '{"ok":true}', **extra}}])
                self.assertEqual(planner.preflight(live=True)["status"], "failed")

    def test_http_failure_is_not_retried_and_does_not_leak_body(self):
        self.open.side_effect = [mock_catalog(), HTTPError(DEFAULT_BASE_URL, 429, MOCK_KEY,
            {"x-request-id": "mock-limit-001"}, BytesIO(MOCK_KEY.encode()))]
        result = self.planner.preflight(live=True)
        self.assertEqual(result["error_code"], "http_error")
        self.assertEqual(result["provenance"]["http_status"], 429)
        self.assertEqual(result["provenance"]["request_state"], "response_received")
        self.assertEqual(result["provenance"]["usage_state"], "unknown")
        self.assertNotIn(MOCK_KEY, json.dumps(result))
        self.assertEqual(self.open.call_count, 2)

    def test_transport_failure_is_not_retried(self):
        self.open.side_effect = URLError(MOCK_KEY)
        result = self.planner.preflight(live=True)
        self.assertEqual(result["error_code"], "transport_error")
        self.assertNotIn(MOCK_KEY, json.dumps(result))
        self.assertEqual(self.open.call_count, 1)

    def test_redirects_are_not_followed(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 307, "", {}, "https://example.test"))

    def test_oversized_response_is_rejected(self):
        self.open.return_value = mock_response(b"x" * (MAX_RESPONSE_BYTES + 1))
        result = self.planner.preflight(live=True)
        self.assertEqual(result["error_code"], "response_too_large")

    def test_duplicate_and_nonfinite_json_are_rejected(self):
        for content in ('{"ok":true,"ok":true}', '{"ok":NaN}', '{"ok":1e400}'):
            with self.subTest(content=content):
                self.prepare_completion(content)
                planner = NemotronPlanner(model=MOCK_MODEL, api_key=MOCK_KEY)
                self.assertEqual(planner.preflight(live=True)["status"], "failed")

    def test_propose_returns_typed_scenario_and_redacted_history(self):
        scenario = ScenarioSpec().as_dict()
        self.prepare_completion({"scenarios": [scenario]})
        output, provenance = self.planner.propose([{"scenario": scenario, "score": 0.3,
            "policy_source": "PRIVATE_POLICY_MARKER", "failure_config": "PRIVATE_CONFIG_MARKER",
            "arbitrary_text": "IGNORE_ALL_PREVIOUS_RULES", "violations": ["human_distance", "secret"]}], 1, 7)
        self.assertIsInstance(output[0], ScenarioSpec)
        self.assertEqual(provenance["proposal_count"], 1)
        payload = json.loads(self.open.call_args.args[0].data)
        prompt = json.dumps(payload["messages"])
        for marker in ("PRIVATE_POLICY_MARKER", "PRIVATE_CONFIG_MARKER", "IGNORE_ALL_PREVIOUS_RULES", "secret"):
            self.assertNotIn(marker, prompt)
        self.assertIn("human_present must always be true", prompt)
        self.assertIn("safety_distance_m must always be 0.75", prompt)
        self.assertEqual(payload["response_format"]["json_schema"]["schema"]["properties"]["scenarios"]["maxItems"], 1)

    def test_propose_rejects_weakening_unknown_fields_and_invalid_values(self):
        changes = ({"human_present": False}, {"safety_distance_m": 0.1}, {"safety_distance_m": True},
                   {"unexpected_code": "print(1)"}, {"robot_speed_mps": 50}, {"seed": True},
                   {"sensor_delay_ms": 1.5})
        for change in changes:
            with self.subTest(change=change):
                self.prepare_completion({"scenarios": [{**ScenarioSpec().as_dict(), **change}]})
                planner = NemotronPlanner(model=MOCK_MODEL, api_key=MOCK_KEY)
                with self.assertRaises(NebiusError) as raised:
                    planner.propose([], 1, 1)
                self.assertEqual(raised.exception.provenance["validation_state"], "rejected")
                self.assertEqual(raised.exception.provenance["usage_state"], "known")
                self.assertEqual(raised.exception.provenance["usage"]["total_tokens"], 30)

    def test_engine_nested_feedback_reaches_model_without_private_fields(self):
        scenario = ScenarioSpec().as_dict()
        self.prepare_completion({"scenarios": [scenario]})
        history = [{"index": 1, "scenario": scenario, "duplicate": False, "summary": {
            "minimum_distance_m": 0.42, "mission_completed": False, "steps": 50, "duration_s": 5,
            "violations": [{"monitor": "human_distance", "private_context": "PRIVATE_VIOLATION_MARKER"},
                           {"monitor": "sensor_conflict"}, {"monitor": "PRIVATE_MONITOR_MARKER"}],
            "policy_source": "PRIVATE_SOURCE_MARKER", "failure_configuration": "PRIVATE_CONFIG_MARKER"}}]
        self.planner.propose(history, 1, 7)
        payload = json.loads(self.open.call_args.args[0].data)
        projected = json.loads(payload["messages"][1]["content"])["history"][0]
        self.assertEqual(projected["min_distance_m"], 0.42)
        self.assertFalse(projected["completed"])
        self.assertEqual(projected["step_count"], 50)
        self.assertEqual(projected["elapsed_s"], 5)
        self.assertFalse(projected["duplicate"])
        self.assertEqual(projected["violations"], ["human_distance", "sensor_conflict"])
        self.assertNotIn("PRIVATE_", json.dumps(payload))

    def test_model_must_supply_all_scenario_fields(self):
        self.prepare_completion({"scenarios": [{"seed": 1}]})
        with self.assertRaises(NebiusError):
            self.planner.propose([], 1, 1)

    def test_invalid_count_or_seed_never_calls_provider(self):
        for count, seed in ((0, 1), (9, 1), (True, 1), (1, -1), (1, 2**32), (1, True)):
            with self.subTest(count=count, seed=seed), self.assertRaises(NebiusError):
                self.planner.propose([], count, seed)
        self.open.assert_not_called()

    def test_compile_claims_returns_fixed_templates_for_review(self):
        self.prepare_completion({"claims": [{"monitor": "human_distance", "threshold_m": 0.75},
            {"monitor": "mission_completion", "threshold_m": None}], "unsupported": False})
        claims, provenance = self.planner.compile_claims("Keep 0.75 m from people and complete the mission.")
        self.assertEqual(claims[0]["threshold_m"], 0.75)
        self.assertTrue(provenance["requires_review"])

    def test_compile_claims_rejects_unsupported_monitor_and_threshold(self):
        for claim in ({"monitor": "arbitrary_python", "threshold_m": None},
                      {"monitor": "human_distance", "threshold_m": 0.25}):
            with self.subTest(claim=claim):
                self.prepare_completion({"claims": [claim], "unsupported": False})
                planner = NemotronPlanner(model=MOCK_MODEL, api_key=MOCK_KEY)
                with self.assertRaises(NebiusError):
                    planner.compile_claims("Keep distance.")

    def test_unsupported_rules_are_not_silently_dropped(self):
        self.prepare_completion({"claims": [], "unsupported": True})
        with self.assertRaises(NebiusError) as raised:
            self.planner.compile_claims("Check an unsupported custom policy.")
        self.assertEqual(raised.exception.code, "unsupported_claim")


if __name__ == "__main__":
    unittest.main()
