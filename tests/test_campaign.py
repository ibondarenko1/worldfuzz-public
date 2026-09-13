"""Offline campaign regressions. Planners and provider usage are MOCK fixtures."""
from copy import deepcopy
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worldfuzz.cli import _checkpoint_writer, main
from worldfuzz.engine import create_capsule, search, verify_capsule, write_json
from worldfuzz.nebius import NebiusError
from worldfuzz.schema import ScenarioSpec


def offline_provenance(prompt=20, completion=10):
    return {
        "source": "offline_mock",
        "model": "nvidia/Nemotron-MOCK-only",
        "validation_state": "validated",
        "request_state": "response_received",
        "usage_state": "known",
        "completion_request_attempted": True,
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


class BenignOfflinePlanner:
    """Always misses the distance target; never opens a network connection."""

    def __init__(self, second_error=None):
        self.calls = []
        self.second_error = second_error

    def propose(self, history, count, seed, **kwargs):
        self.calls.append({"history": deepcopy(history), "count": count, "seed": seed})
        if len(self.calls) == 2 and self.second_error is not None:
            raise self.second_error
        scenarios = [ScenarioSpec(seed=seed + index, human_crossing_time_s=1,
                                  sensor_delay_ms=0) for index in range(count)]
        return scenarios, offline_provenance()


def rewrite_hashed_file(directory, name, mutation):
    """Change semantic evidence and refresh its digest to exercise the verifier."""
    path = directory / name
    document = json.loads(path.read_text())
    mutation(document)
    write_json(path, document)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(directory / "manifest.json", manifest)


class CampaignOfflineTests(unittest.TestCase):
    def test_capsule_write_interruption_preserves_completed_search_journal(self):
        class FindingOfflinePlanner:
            calls = 0

            def propose(self, history, count, seed):
                self.calls += 1
                return [ScenarioSpec(seed=42, human_crossing_time_s=4,
                                     robot_speed_mps=1, sensor_delay_ms=1200)], offline_provenance()

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            planner = FindingOfflinePlanner()
            result = search(seed=42, budget=1, planner=planner,
                            checkpoint=_checkpoint_writer(directory))
            self.assertEqual(result["status"], "FOUND")
            journal = (directory / "campaign.json").read_bytes()
            real_replace = os.replace
            artifact_replacements = []

            def interrupt_manifest(source, destination):
                name = Path(destination).name
                artifact_replacements.append(name)
                if name == "manifest.json":
                    raise OSError("Offline interrupted artifact commit.")
                return real_replace(source, destination)

            with patch("worldfuzz.engine.os.replace", side_effect=interrupt_manifest), \
                    patch("worldfuzz.nebius.build_opener") as transport:
                with self.assertRaisesRegex(OSError, "interrupted artifact commit"):
                    create_capsule(directory, result, allow_campaign_journal=True)
            transport.assert_not_called()
            self.assertEqual(planner.calls, 1)
            self.assertNotIn("campaign.json", artifact_replacements)
            self.assertEqual((directory / "campaign.json").read_bytes(), journal)
            self.assertFalse((directory / "manifest.json").exists())
            checked = verify_capsule(directory)
            self.assertEqual(checked["status"], "VERIFIED")
            self.assertEqual(checked["result"], "INCOMPLETE")
            self.assertEqual(checked["campaign_result"], "FOUND")

    def test_no_finding_retains_trials_and_verifies_without_safety_claim(self):
        result = search(seed=1, budget=2, planner=BenignOfflinePlanner())
        self.assertIsNone(result["found"])
        self.assertEqual(result["status"], "NO_COUNTEREXAMPLE_FOUND")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            report = create_capsule(directory, result)
            self.assertEqual(report["status"], "NO_COUNTEREXAMPLE_FOUND")
            record = json.loads((directory / "campaign.json").read_text())
            self.assertEqual(len(record["history"]), 2)
            self.assertEqual(record["executed_runs"], 2)
            self.assertEqual(record["budget"], 2)
            verified = verify_capsule(directory)
            self.assertEqual(verified["status"], "VERIFIED")
            self.assertEqual(verified["result"], "NO_COUNTEREXAMPLE_FOUND")
            self.assertFalse(verified["model_provenance_authenticated"])

    def test_rehashed_false_no_finding_evidence_cannot_pass_verification(self):
        def falsify_summary(record):
            record["history"][0]["summary"]["minimum_distance_m"] = 1000.0

        def falsify_usage(record):
            record["usage"]["known_tokens"]["total_tokens"] = 0

        mutations = {
            "summary": falsify_summary,
            "usage": falsify_usage,
            "count": lambda record: record.update(executed_runs=0),
            "status": lambda record: record.update(status="FOUND"),
        }
        result = search(seed=1, budget=1, planner=BenignOfflinePlanner())
        for field, mutation in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                create_capsule(directory, result)
                rewrite_hashed_file(directory, "campaign.json", mutation)
                manifest = json.loads((directory / "manifest.json").read_text())
                if "search.json" in manifest["files"]:
                    rewrite_hashed_file(directory, "search.json", mutation)
                with self.assertRaises(ValueError):
                    verify_capsule(directory)

    def test_rejected_second_call_preserves_first_trials_and_all_known_usage(self):
        rejected = offline_provenance(prompt=7, completion=5)
        rejected["validation_state"] = "rejected"
        planner = BenignOfflinePlanner(NebiusError(
            "schema_mismatch", "Offline invalid proposal fixture.", rejected))
        result = search(seed=1, budget=9, planner=planner)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["executed_runs"], 8)
        self.assertEqual(len(result["history"]), 8)
        self.assertEqual(len(planner.calls), 2)
        self.assertEqual(planner.calls[1]["history"], result["history"])
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(result["attempts"][0]["status"], "succeeded")
        self.assertIn(result["attempts"][1]["status"], ("failed", "rejected"))
        self.assertEqual(result["usage"]["known_tokens"], {
            "prompt_tokens": 27, "completion_tokens": 15, "total_tokens": 42})
        self.assertEqual(result["usage"]["known_usage_attempts"], 2)
        self.assertEqual(result["usage"]["unknown_usage_attempts"], 0)
        with tempfile.TemporaryDirectory() as temp:
            create_capsule(temp, result)
            self.assertEqual(verify_capsule(temp)["result"], "FAILED")

    def test_unknown_second_call_usage_never_erases_known_prior_tokens(self):
        planner = BenignOfflinePlanner(NebiusError("transport_error", "Offline timeout fixture.",
            {"source": "offline_mock", "validation_state": "rejected",
             "request_state": "possibly_sent", "usage_state": "unknown",
             "completion_request_attempted": True}))
        result = search(seed=1, budget=9, planner=planner)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["executed_runs"], 8)
        self.assertEqual(len(planner.calls), 2)
        self.assertEqual(result["usage"]["known_tokens"]["total_tokens"], 30)
        self.assertEqual(result["usage"]["known_usage_attempts"], 1)
        self.assertEqual(result["usage"]["unknown_usage_attempts"], 1)

    def test_model_limit_after_first_batch_preserves_trials_without_retry(self):
        planner = BenignOfflinePlanner()
        result = search(seed=1, budget=9, planner=planner, max_model_calls=1)
        self.assertEqual(result["status"], "MODEL_BUDGET_EXHAUSTED")
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(result["executed_runs"], 8)
        self.assertEqual(result["usage"]["known_tokens"]["total_tokens"], 30)

    def test_cancellation_during_proposal_preserves_pending_uncertain_attempt(self):
        class CancelledOfflinePlanner:
            def propose(self, history, count, seed, **kwargs):
                raise KeyboardInterrupt

        snapshots = []
        result = search(seed=1, budget=1, planner=CancelledOfflinePlanner(),
                        checkpoint=lambda record: snapshots.append(deepcopy(record)))
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["executed_runs"], 0)
        self.assertEqual(result["usage"]["unknown_usage_attempts"], 1)
        pending = next(record for record in snapshots if record["attempts"] and
                       record["attempts"][-1]["status"] == "pending")
        self.assertEqual(pending["status"], "RUNNING")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            write_json(directory / "campaign.json", pending)
            verified = verify_capsule(directory)
            self.assertEqual(verified["status"], "VERIFIED")
            self.assertEqual(verified["result"], "INCOMPLETE")
            pending["status"] = "NO_COUNTEREXAMPLE_FOUND"
            write_json(directory / "campaign.json", pending)
            with self.assertRaises(ValueError):
                verify_capsule(directory)
        with tempfile.TemporaryDirectory() as temp:
            create_capsule(temp, result)
            self.assertEqual(verify_capsule(temp)["result"], "CANCELLED")

    def test_zero_model_limit_makes_no_attempt_and_is_not_no_finding(self):
        planner = BenignOfflinePlanner()
        with patch("worldfuzz.engine.simulate") as simulator:
            result = search(seed=1, budget=2, planner=planner, max_model_calls=0)
        self.assertEqual(planner.calls, [])
        simulator.assert_not_called()
        self.assertEqual(result["executed_runs"], 0)
        self.assertEqual(result["status"], "MODEL_BUDGET_EXHAUSTED")

    def test_whole_malformed_batch_is_rejected_before_any_simulation(self):
        benign = ScenarioSpec(human_crossing_time_s=1)
        batches = {"empty": [], "wrong_count": [benign],
                   "partial_dict": [benign, {"seed": 1}],
                   "weakened": [benign, ScenarioSpec(human_present=False)]}
        for kind, batch in batches.items():
            class MalformedOfflinePlanner:
                def propose(self, history, count, seed, **kwargs):
                    return batch, offline_provenance()

            with self.subTest(kind=kind), patch("worldfuzz.engine.simulate") as simulator:
                result = search(seed=1, budget=2, planner=MalformedOfflinePlanner())
                simulator.assert_not_called()
                self.assertEqual(result["executed_runs"], 0)
                self.assertEqual(result["history"], [])
                self.assertEqual(result["status"], "FAILED")

    def test_cli_rejects_occupied_output_without_model_or_network(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            previous = directory / "prior-evidence.txt"
            previous.write_text("Keep prior evidence intact.\n")
            with patch("worldfuzz.nebius.NemotronPlanner") as planner, \
                    patch("worldfuzz.nebius.build_opener") as transport, \
                    patch("sys.stdout", new_callable=StringIO), \
                    patch("sys.stderr", new_callable=StringIO):
                status = main(["search", "--strategy", "nemotron", "--budget", "1",
                               "--out", str(directory)])
            self.assertNotEqual(status, 0)
            planner.return_value.propose.assert_not_called()
            transport.assert_not_called()
            self.assertEqual(previous.read_text(), "Keep prior evidence intact.\n")

    def test_cli_checkpoint_failure_prevents_unrecorded_model_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "campaign"
            with patch("worldfuzz.nebius.NemotronPlanner") as planner, \
                    patch("worldfuzz.nebius.build_opener") as transport, \
                    patch("worldfuzz.cli.os.replace", side_effect=OSError("offline simulated disk failure")), \
                    patch("sys.stdout", new_callable=StringIO), \
                    patch("sys.stderr", new_callable=StringIO):
                status = main(["search", "--strategy", "nemotron", "--budget", "1",
                               "--out", str(directory)])
            self.assertEqual(status, 1)
            planner.return_value.propose.assert_not_called()
            transport.assert_not_called()
            journal = json.loads((directory / "campaign.json").read_text())
            self.assertEqual(journal["executed_runs"], 0)
            self.assertNotEqual(journal["status"], "NO_COUNTEREXAMPLE_FOUND")
            self.assertFalse((directory / "manifest.json").exists())

    def test_cli_later_call_failure_saves_partial_evidence_and_returns_error(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "campaign"
            snapshots_seen_by_planner = []

            class WitnessOfflinePlanner(BenignOfflinePlanner):
                def propose(self, history, count, seed, **kwargs):
                    snapshots_seen_by_planner.append(json.loads(
                        (directory / "campaign.json").read_text()))
                    return super().propose(history, count, seed, **kwargs)

            planner = WitnessOfflinePlanner(NebiusError("transport_error", "Offline network failure.",
                {"source": "offline_mock", "validation_state": "rejected",
                 "request_state": "possibly_sent", "usage_state": "unknown",
                 "completion_request_attempted": True}))
            with patch("worldfuzz.nebius.NemotronPlanner", return_value=planner), \
                    patch("worldfuzz.nebius.build_opener") as transport, \
                    patch("sys.stdout", new_callable=StringIO), \
                    patch("sys.stderr", new_callable=StringIO):
                status = main(["search", "--strategy", "nemotron", "--budget", "9",
                               "--out", str(directory)])
            transport.assert_not_called()
            self.assertEqual(status, 2)
            self.assertEqual(len(snapshots_seen_by_planner), 2)
            self.assertEqual([snapshot["executed_runs"] for snapshot in snapshots_seen_by_planner], [0, 8])
            self.assertTrue(all(snapshot["attempts"][-1]["status"] == "pending"
                                for snapshot in snapshots_seen_by_planner))
            record = json.loads((directory / "campaign.json").read_text())
            self.assertEqual(record["status"], "FAILED")
            self.assertEqual(record["executed_runs"], 8)
            self.assertEqual(len(record["history"]), 8)
            self.assertEqual(record["usage"]["known_tokens"]["total_tokens"], 30)
            self.assertEqual(record["usage"]["unknown_usage_attempts"], 1)
            self.assertTrue((directory / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
