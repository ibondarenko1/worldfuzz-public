"""Pilot contract checks. Runner failures use mocks, never a difficulty sweep."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worldfuzz.cases import CaseSpec, TUNING_CASES
from worldfuzz import pilot


class CaseContractTests(unittest.TestCase):
    def test_search_proposals_cannot_change_the_task_or_weaken_requirements(self):
        case = TUNING_CASES[0]
        for changes in ({"seed": 42}, {"world_seed": 42}, {"human_present": False},
                        {"safety_distance_m": .1}, {"policy": "baseline"},
                        {"crossing_x_m": 8}, {"sensor_conflict": True},
                        {"command_source": "guest"}, {"command_priority": "override"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                case.scenario(**changes)
        original = case.scenario()
        for changes in ({"seed": 42}, {"human_present": False},
                        {"sensor_conflict": True}, {"command_source": "guest"},
                        {"command_priority": "override"}, {"robot_speed_mps": .3},
                        {"sensor_delay_ms": 1}, {"occlusion": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                case.validate_scenario(replace(original, **changes))
        self.assertEqual(original.seed, case.world_seed)
        self.assertTrue(original.human_present)
        self.assertEqual(original.safety_distance_m, .75)
        with self.assertRaises(FrozenInstanceError):
            case.policy = "baseline"

    def test_case_records_reject_invalid_domains_and_incomplete_schema(self):
        record = TUNING_CASES[1].as_dict()
        self.assertEqual(CaseSpec.from_dict(json.loads(json.dumps(record))).as_dict(), record)
        for field in record:
            incomplete = dict(record)
            del incomplete[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                CaseSpec.from_dict(incomplete)
        invalid = (
            {"world_seed": True}, {"world_seed": -1}, {"world_seed": 2**32},
            {"crossing_x_m": float("nan")}, {"crossing_x_m": float("inf")},
            {"crossing_x_m": True}, {"crossing_x_m": 11},
            {"crossing_time_bounds": [8, 2]}, {"crossing_time_bounds": [1]},
            {"human_speed_bounds": [.1, 1]}, {"robot_speed_bounds": [.3, 1]},
            {"robot_speed_bounds": [.8, True]}, {"sensor_delays_ms": [True]},
            {"sensor_delays_ms": [0, 0]}, {"sensor_delays_ms": [1501]},
            {"sensor_delays_ms": []}, {"occlusion_choices": [0]},
            {"occlusion_choices": [False, False]}, {"schema_version": True},
            {"split": "unrecorded"}, {"policy": "unknown"}, {"extra": "ignored"},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                CaseSpec.from_dict({**record, **changes})


class PilotContractTests(unittest.TestCase):
    def test_methods_are_deterministic_and_share_the_fixed_case_domain(self):
        with patch.object(pilot, "simulate", side_effect=AssertionError("No simulation in candidate generation")):
            for case in TUNING_CASES:
                for method in ("random", "crossing_time"):
                    with self.subTest(case=case.case_id, method=method):
                        proposals = pilot.candidates(case, method)
                        self.assertEqual(len(proposals), 48)
                        self.assertEqual(proposals, pilot.candidates(case, method))
                        for scenario in proposals:
                            case.validate_scenario(scenario)
                            self.assertEqual(scenario.seed, case.world_seed)
                            self.assertTrue(scenario.human_present)
                            self.assertEqual(scenario.safety_distance_m, .75)

    def test_heldout_case_cannot_be_evaluated_as_pilot_tuning(self):
        heldout = replace(TUNING_CASES[0], split="heldout")
        with patch.object(pilot, "simulate") as simulator:
            for method in ("random", "crossing_time"):
                with self.subTest(method=method), self.assertRaises(ValueError):
                    pilot.candidates(heldout, method)
        simulator.assert_not_called()

    def test_nonempty_output_is_rejected_before_any_simulation(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            existing = output / "existing-evidence.txt"
            existing.write_text("preserve this evidence", encoding="utf-8")
            with patch.object(pilot, "simulate") as simulator, self.assertRaises((ValueError, FileExistsError)):
                pilot.run_pilot(output)
            simulator.assert_not_called()
            self.assertEqual(existing.read_text(encoding="utf-8"), "preserve this evidence")
            self.assertEqual(list(output.iterdir()), [existing])

    def test_mocked_interruption_records_pending_attempt_and_incomplete_outcome(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)

            def interrupt_before_simulation(*args, **kwargs):
                journal = json.loads((output / "pilot.json").read_text(encoding="utf-8"))
                self.assertEqual(journal["status"], "RUNNING")
                self.assertEqual(len(journal["operations"]), 1)
                self.assertEqual(journal["operations"][0]["status"], "pending")
                self.assertIsNone(journal["operations"][0]["finished_at"])
                raise KeyboardInterrupt("mocked interruption before any simulator work")

            with patch.object(pilot, "simulate", side_effect=interrupt_before_simulation) as simulator:
                result = pilot.run_pilot(output)
                self.assertEqual(result["status"], "CANCELLED")
                self.assertEqual(result["counts"]["attempted_simulations"], 1)
                self.assertEqual(result["counts"]["completed_simulations"], 0)
                self.assertEqual(result["counts"]["uncertain_simulation_attempts"], 1)
                self.assertEqual(result["counts"]["provider_requests"], 0)
                self.assertEqual(result["counts"]["cloud_jobs"], 0)
                for aggregate in result["aggregates"]:
                    for metrics in aggregate["budgets"].values():
                        self.assertEqual(metrics["finding_status"], "INCOMPLETE")
                journal = json.loads((output / "pilot.json").read_text(encoding="utf-8"))
                self.assertEqual(journal["operations"][0]["status"], "cancelled")
                self.assertEqual(journal["error"]["code"], "interrupted")
                self.assertNotIn("mocked interruption", json.dumps(journal))
                verification = pilot.verify_pilot(output)
                self.assertEqual(verification["status"], "VERIFIED_INCOMPLETE")
                self.assertEqual(verification["replayed_simulations"], 0)
                self.assertEqual(verification["uncertain_simulation_attempts"], 1)
                simulator.assert_called_once()

    def test_partial_evidence_cannot_claim_unexecuted_work(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with patch.object(pilot, "simulate", side_effect=KeyboardInterrupt):
                pilot.run_pilot(output)
            result_path = output / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["counts"]["completed_simulations"] = 1
            result_path.write_text(json.dumps(result), encoding="utf-8")
            with patch.object(pilot, "simulate") as simulator:
                with self.assertRaisesRegex(ValueError, "aggregate|result differs"):
                    pilot.verify_pilot(output)
            simulator.assert_not_called()

    def test_mocked_optional_copy_write_failure_preserves_completed_primary_evidence(self):
        mocked_run = {"summary": {"minimum_distance_m": 1.0, "mission_completed": True,
                                  "violations": []}, "trace_sha256": "0" * 64}
        original_write = pilot.write_json
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)

            def fail_optional_copy(path, value):
                if Path(path).name.startswith("representative-"):
                    original_write(path, {"mocked_stale_copy": True})
                    raise OSError("mocked optional copy write failure")
                return original_write(path, value)

            with patch.object(pilot, "simulate", return_value=mocked_run) as simulator:
                with patch.object(pilot, "write_json", side_effect=fail_optional_copy):
                    result = pilot.run_pilot(output)
                self.assertEqual(result["status"], "FAILED")
                self.assertEqual(result["counts"]["completed_simulations"], 2)
                self.assertEqual(result["counts"]["uncertain_simulation_attempts"], 0)
                self.assertEqual(simulator.call_count, 2)
                self.assertFalse((output / "manifest.json").exists())
                self.assertEqual(len(list(output.glob("representative-*.json"))), 1)
                verification = pilot.verify_pilot(output)
                self.assertEqual(verification["status"], "VERIFIED_INCOMPLETE")
                self.assertEqual(verification["replayed_simulations"], 2)
                self.assertEqual(verification["representatives_checked"], 0)
                self.assertEqual(verification["representative_copies_pending"], 1)
                self.assertEqual(simulator.call_count, 4)


if __name__ == "__main__":
    unittest.main()
