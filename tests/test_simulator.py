"""Held toy cases and adversarial monitor checks; no physical safety claims."""

from dataclasses import replace
import copy
import unittest

from worldfuzz.schema import ScenarioSpec
from worldfuzz.simulator import (
    _Measurement, _Observation, _decide, evaluate_trace, simulate, trace_digest,
)


class SimulatorTests(unittest.TestCase):
    def scenario(self, **overrides):
        return replace(ScenarioSpec(seed=42), **overrides)

    def test_repeated_runs_have_identical_evidence(self):
        scenario = self.scenario(sensor_delay_ms=350, occlusion=True)
        first, second = simulate(scenario), simulate(scenario)
        self.assertEqual(first, second)
        self.assertEqual(first["trace_sha256"], trace_digest(first["trace"]))
        self.assertEqual(first["summary"]["steps"], 400)
        self.assertEqual(first["summary"]["duration_s"], 20.0)

    def test_seed_changes_visible_geometry(self):
        first, second = simulate(self.scenario(seed=11)), simulate(self.scenario(seed=12))
        self.assertNotEqual(first["world"]["human_crossing_x_m"], second["world"]["human_crossing_x_m"])

    def test_nominal_mission_completes_for_both_policies(self):
        for human_present in (False, True):
            for policy in ("baseline", "hardened"):
                with self.subTest(human_present=human_present, policy=policy):
                    result = simulate(self.scenario(human_present=human_present), policy)
                    self.assertTrue(result["summary"]["mission_completed"])
                    self.assertEqual(result["summary"]["violations"], [])

    def test_occlusion_case_stops_then_resumes_and_completes(self):
        scenario = self.scenario(human_crossing_time_s=5.0, occlusion=True)
        baseline, hardened = simulate(scenario), simulate(scenario, "hardened")
        self.assertIn("human_distance", {v["monitor"] for v in baseline["summary"]["violations"]})
        self.assertEqual(hardened["summary"]["violations"], [])
        self.assertTrue(hardened["summary"]["mission_completed"])
        self.assertTrue(any(row["speed"] == 0 and row["sensor_status"] == "occluded" for row in hardened["trace"]))
        self.assertTrue(any(row["speed"] > 0 and row["t"] > 6 for row in hardened["trace"]))

    def test_conflicting_sensor_movement_is_monitored(self):
        scenario = self.scenario(human_present=False, sensor_conflict=True)
        baseline, hardened = simulate(scenario), simulate(scenario, "hardened")
        self.assertIn("sensor_conflict", {v["monitor"] for v in baseline["summary"]["violations"]})
        self.assertEqual(hardened["summary"]["violations"], [])
        self.assertTrue(hardened["summary"]["mission_completed"])

    def test_delayed_location_case_preserves_route_completion(self):
        scenario = self.scenario(
            human_crossing_time_s=4.0, robot_speed_mps=1.0, sensor_delay_ms=1200,
        )
        baseline, hardened = simulate(scenario), simulate(scenario, "hardened")
        self.assertIn("human_distance", {v["monitor"] for v in baseline["summary"]["violations"]})
        self.assertLess(baseline["summary"]["minimum_distance_m"], 0.1)
        self.assertEqual(hardened["summary"]["violations"], [])
        self.assertTrue(hardened["summary"]["mission_completed"])
        self.assertTrue(any(row["action_reason"] == "stale_observation_protective_stop" for row in hardened["trace"]))

    def test_guest_override_is_rejected_without_disabling_nominal_mission(self):
        scenario = self.scenario(human_present=False, command_source="guest", command_priority="override")
        baseline, hardened = simulate(scenario), simulate(scenario, "hardened")
        self.assertIn("unauthorized_command", {v["monitor"] for v in baseline["summary"]["violations"]})
        self.assertTrue(all(not row["override_accepted"] for row in hardened["trace"]))
        self.assertTrue(hardened["summary"]["mission_completed"])
        self.assertEqual(hardened["summary"]["violations"], [])

    def test_swept_monitor_catches_collision_between_safe_endpoints(self):
        trace = copy.deepcopy(simulate(self.scenario())["trace"][:2])
        for index, row in enumerate(trace):
            row.update({
                "t": float(index), "robot_x": 0.0, "robot_y": 0.0,
                "human_x": -1.0 if index == 0 else 1.0, "human_y": 0.0,
                "human_present": True, "distance_m": 999.0, "speed": 0.0,
            })
        summary = evaluate_trace(trace)
        self.assertEqual(summary["minimum_distance_m"], 0.0)
        violation = summary["violations"][0]
        self.assertEqual(violation["monitor"], "human_distance")
        self.assertEqual(violation["time_s"], 0.5)
        self.assertFalse(summary["mission_completed"])

    def test_unavailable_and_conflicting_observations_stop_without_hidden_coordinates(self):
        measurement = _Measurement(0.0, True, 50.0, 50.0)
        for observation in (
            _Observation("occluded", 0.5, measurement, False),
            _Observation("warming_up", None, None, False),
            _Observation("fresh", 0.0, measurement, True),
        ):
            speed, _, _ = _decide("hardened", observation, 0.0, 0.8, "operator", "normal", 0.75)
            self.assertEqual(speed, 0.0)

    def test_noncompletion_is_independent_from_safety_violations(self):
        result = simulate(self.scenario(human_present=False, sensor_delay_ms=1500), "hardened")
        self.assertFalse(result["summary"]["mission_completed"])
        self.assertEqual(result["summary"]["violations"], [])

    def test_pedestrian_does_not_walk_into_robot_stopped_inside_crossing(self):
        # Discovered by bounded random search, seed 42, candidate 5. The earlier
        # Euclidean-only hardened policy stopped inside the future crossing.
        scenario = self.scenario(
            seed=1625792787, human_crossing_time_s=7.072,
            human_speed_mps=1.295, robot_speed_mps=0.625,
        )
        baseline, hardened = simulate(scenario), simulate(scenario, "hardened")
        self.assertIn("human_distance", {v["monitor"] for v in baseline["summary"]["violations"]})
        self.assertEqual(hardened["summary"]["violations"], [])
        self.assertGreaterEqual(hardened["summary"]["minimum_distance_m"], 0.95)
        self.assertTrue(any(row["action_reason"] == "reserved_crossing_stop" for row in hardened["trace"]))
        self.assertFalse(hardened["summary"]["mission_completed"])

    def test_rejects_invalid_policy_and_trace_time(self):
        with self.assertRaises(ValueError):
            simulate(self.scenario(), "unrecognized")
        trace = simulate(self.scenario())["trace"][:2]
        trace[1]["t"] = trace[0]["t"]
        with self.assertRaises(ValueError):
            evaluate_trace(trace)


if __name__ == "__main__":
    unittest.main()
