from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from worldfuzz.engine import (create_capsule, has_violation, minimize, mission_feasibility,
                             nominal_control_scenario, search, verify_capsule, write_json)
from worldfuzz.schema import ScenarioSpec
from worldfuzz.simulator import simulate


def fixture_result(scenario=None):
    if scenario is None:
        scenario = ScenarioSpec(seed=42, human_crossing_time_s=4, robot_speed_mps=1, sensor_delay_ms=1200)
    run = simulate(scenario, "baseline")
    return {"strategy": "test_fixture", "target": "human_distance", "seed": 42,
            "budget": 1, "executed_runs": 1,
            "history": [{"index": 1, "scenario": scenario.as_dict(), "summary": run["summary"]}],
            "elapsed_seconds": None, "model_calls": [], "found": run}


class EngineTests(unittest.TestCase):
    def test_nominal_control_preserves_requested_mission_parameters(self):
        scenario = ScenarioSpec(seed=42, robot_speed_mps=1, human_crossing_time_s=9,
                                human_speed_mps=.6, sensor_delay_ms=900, occlusion=True,
                                sensor_conflict=True, command_source="guest",
                                command_priority="override")
        control = nominal_control_scenario(scenario)
        for name in ("seed", "robot_speed_mps", "human_crossing_time_s",
                     "human_speed_mps", "safety_distance_m"):
            self.assertEqual(getattr(control, name), getattr(scenario, name), name)
        self.assertFalse(control.human_present)
        self.assertFalse(control.occlusion)
        self.assertFalse(control.sensor_conflict)
        self.assertEqual(control.sensor_delay_ms, 0)
        self.assertEqual(control.command_source, "operator")
        self.assertEqual(control.command_priority, "normal")

    def test_feasibility_distinguishes_nominal_timeout_and_exact_boundary(self):
        self.assertEqual(mission_feasibility(ScenarioSpec(robot_speed_mps=.3)),
                         {"nominal_travel_time_s": 40.0, "horizon_s": 20.0,
                          "nominal_completion_possible": False})
        boundary = ScenarioSpec(robot_speed_mps=.6)
        self.assertTrue(mission_feasibility(boundary)["nominal_completion_possible"])
        for policy in ("baseline", "hardened"):
            self.assertTrue(simulate(nominal_control_scenario(boundary), policy)["summary"]["mission_completed"])

    def test_schema_rejects_unsafe_or_invalid_parameter_shapes(self):
        for payload in ({"seed": True}, {"sensor_delay_ms": 2000}, {"robot_speed_mps": float("nan")},
                        {"occlusion": "false"}, {"shell": "arbitrary"}, {"safety_distance_m": .1}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ScenarioSpec.from_dict(payload)

    def test_search_is_repeatable_without_nvidia_claim(self):
        a, b = search(42, 24), search(42, 24)
        self.assertEqual(a["history"], b["history"])
        self.assertEqual(a["model_calls"], [])
        self.assertEqual(a["strategy"], "random")
        self.assertLessEqual(a["executed_runs"], 24)
        self.assertIsNotNone(a["found"])

    def test_no_finding_budget_is_not_pass(self):
        class BenignPlanner:
            def propose(self, history, count, seed):
                return [ScenarioSpec(human_crossing_time_s=1, sensor_delay_ms=0)] * count, {"mock": True}
        result = search(1, 2, BenignPlanner())
        self.assertEqual(result["executed_runs"], 2)
        self.assertIsNone(result["found"])
        with tempfile.TemporaryDirectory() as directory:
            output = create_capsule(directory, result)
            self.assertEqual(output["status"], "NO_COUNTEREXAMPLE_FOUND")

    def test_reduction_preserves_target_and_is_single_factor_minimal(self):
        scenario = ScenarioSpec.from_dict(fixture_result()["found"]["scenario"])
        reduced, record = minimize(scenario)
        self.assertTrue(has_violation(simulate(reduced)))
        for name in record["remaining_factors"]:
            candidate = replace(reduced, **{name: record["resets"][name]})
            self.assertFalse(has_violation(simulate(candidate)), name)

    def test_capsule_verifies_and_detects_edited_trace(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            result = create_capsule(directory, fixture_result())
            self.assertTrue(result["positive_controls_mission_preserved"])
            self.assertTrue(result["mission_feasibility"]["nominal_completion_possible"])
            self.assertEqual(json.loads((directory / "manifest.json").read_text())["schema_version"], 2)
            for name in ("control.json", "control_baseline.json"):
                self.assertEqual(json.loads((directory / name).read_text())["scenario"]["robot_speed_mps"], 1)
            self.assertEqual(verify_capsule(directory)["status"], "VERIFIED")
            baseline = json.loads((directory / "baseline.json").read_text())
            baseline["trace"][20]["robot_x"] += .3
            write_json(directory / "baseline.json", baseline)
            with self.assertRaisesRegex(ValueError, "integrity mismatch"):
                verify_capsule(directory)

    def test_verifier_does_not_trust_rehashed_false_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            create_capsule(directory, fixture_result())
            baseline = json.loads((directory / "baseline.json").read_text())
            baseline["summary"]["violations"] = []
            write_json(directory / "baseline.json", baseline)
            manifest = json.loads((directory / "manifest.json").read_text())
            manifest["files"]["baseline.json"] = hashlib.sha256((directory / "baseline.json").read_bytes()).hexdigest()
            write_json(directory / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "Monitor recomputation mismatch"):
                verify_capsule(directory)

    def test_rejects_existing_evidence_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            create_capsule(directory, fixture_result())
            with self.assertRaisesRegex(ValueError, "not overwritten"):
                create_capsule(directory, fixture_result())

    def test_hardened_mission_timeout_is_not_full_fix(self):
        with tempfile.TemporaryDirectory() as directory:
            result = create_capsule(directory, search(42, 24))
            self.assertEqual(result["status"], "SAFETY_BLOCKED_MISSION_INCOMPLETE")
            self.assertFalse(result["hardened_scenario_mission_completed"])
            self.assertEqual(verify_capsule(directory)["result"], result["status"])

    def test_infeasible_mission_preserves_safety_evidence_without_fix_claim(self):
        scenario = ScenarioSpec(seed=42, robot_speed_mps=.3, human_crossing_time_s=12)
        with tempfile.TemporaryDirectory() as directory:
            result = create_capsule(directory, fixture_result(scenario))
            self.assertEqual(result["status"], "MISSION_INFEASIBLE_AT_REQUESTED_SPEED")
            self.assertFalse(result["mission_feasibility"]["nominal_completion_possible"])
            self.assertTrue(result["baseline_confirmed"])
            self.assertTrue(result["replay_confirmed"])
            self.assertTrue(result["hardened_blocks_all_monitored_violations"])
            self.assertFalse(result["positive_controls_mission_preserved"])
            self.assertFalse(result["hardened_scenario_mission_completed"])
            self.assertEqual(verify_capsule(directory)["result"], result["status"])

    def test_verifier_recomputes_rehashed_feasibility(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            create_capsule(directory, fixture_result())
            record = json.loads((directory / "comparison.json").read_text())
            record["mission_feasibility"]["nominal_travel_time_s"] = 1.0
            write_json(directory / "comparison.json", record)
            manifest = json.loads((directory / "manifest.json").read_text())
            manifest["files"]["comparison.json"] = hashlib.sha256((directory / "comparison.json").read_bytes()).hexdigest()
            write_json(directory / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "Comparison mismatch: mission_feasibility"):
                verify_capsule(directory)

    def test_existing_v1_capsules_retain_their_original_verdicts(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        for name, expected in (("demo-capsule", "FIX_CONFIRMED_FOR_THIS_SCENARIO"),
                               ("random-capsule", "SAFETY_BLOCKED_MISSION_INCOMPLETE")):
            with self.subTest(capsule=name):
                directory = examples / name
                before = {path.name: path.read_bytes() for path in directory.glob("*.json")}
                self.assertEqual(json.loads(before["manifest.json"])["schema_version"], 1)
                verified = verify_capsule(directory)
                self.assertEqual(verified["status"], "VERIFIED")
                self.assertEqual(verified["result"], expected)
                self.assertEqual(before, {path.name: path.read_bytes() for path in directory.glob("*.json")})

    def test_verifier_rejects_rehashed_false_reduction(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            create_capsule(directory, fixture_result())
            record = json.loads((directory / "minimization.json").read_text())
            record["remaining_factors"] = ["invented"]
            write_json(directory / "minimization.json", record)
            manifest = json.loads((directory / "manifest.json").read_text())
            manifest["files"]["minimization.json"] = hashlib.sha256((directory / "minimization.json").read_bytes()).hexdigest()
            write_json(directory / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "Minimization replay mismatch"):
                verify_capsule(directory)


if __name__ == "__main__":
    unittest.main()
