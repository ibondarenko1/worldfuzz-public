"""Compatibility and observation-only checks for the offline tuning extension."""

from dataclasses import replace
import json
from pathlib import Path
import unittest

from worldfuzz.schema import ScenarioSpec
from worldfuzz.simulator import _Measurement, _Observation, _decide, simulate


class PilotSimulatorTests(unittest.TestCase):
    def test_default_geometry_preserves_archived_results_exactly(self):
        archive = Path(__file__).resolve().parents[1] / "examples" / "first-live-20260909"
        for relative in (
            "original-baseline.json", "original-hardened.json",
            "capsule/baseline.json", "capsule/hardened.json",
        ):
            with self.subTest(archive=relative):
                recorded = json.loads((archive / relative).read_text(encoding="utf-8"))
                actual = simulate(ScenarioSpec(**recorded["scenario"]), recorded["policy"])
                self.assertEqual(actual, recorded)

    def test_explicit_geometry_is_fixed_across_world_seeds(self):
        scenario = ScenarioSpec(seed=11)
        first = simulate(scenario, crossing_x_m=7.25)
        second = simulate(replace(scenario, seed=12), crossing_x_m=7.25)
        self.assertEqual(first["world"]["human_crossing_x_m"], 7.25)
        self.assertEqual(first["trace"], second["trace"])
        self.assertTrue(all(row["human_x"] == 7.25 for row in first["trace"]))
        self.assertTrue(all(row["observed_human_x"] == 7.25 for row in first["trace"]))

    def test_explicit_geometry_rejects_invalid_values(self):
        for value in (True, False, "4", 1.99, 10.01, float("nan"), float("inf"),
                      -float("inf"), 10 ** 500, [], 4 + 0j):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    simulate(ScenarioSpec(seed=42), crossing_x_m=value)

    def test_policy_ablation_removes_only_reserved_crossing_guard(self):
        # A measured pedestrian is far away but approaching a crossing just
        # ahead. No scenario geometry, crossing time or ground truth is supplied.
        observation = _Observation("fresh", 0.0, _Measurement(0.0, True, 4.0, -3.0), False)
        args = (observation, 3.1, 0.9, "operator", "normal", 0.75)
        self.assertEqual(_decide("hardened", *args), (0.0, "reserved_crossing_stop", False))
        self.assertEqual(_decide("protective_distance", *args), (0.9, "route_clear", False))

        near = _Measurement(0.0, True, 3.0, 0.0)
        absent = _Measurement(0.0, False, None, None)
        far = _Measurement(0.0, True, 4.0, 2.5)
        for observation, source, priority in (
            (_Observation("fresh", 0.0, near, False), "operator", "normal"),
            (_Observation("stale", 1.0, far, False), "operator", "normal"),
            (_Observation("stale", 1.0, absent, False), "operator", "normal"),
            (_Observation("occluded", 0.5, far, False), "operator", "normal"),
            (_Observation("warming_up", None, None, False), "operator", "normal"),
            (_Observation("fresh", 0.0, far, True), "operator", "normal"),
            (_Observation("fresh", 0.0, far, False), "guest", "override"),
        ):
            with self.subTest(observation=observation, source=source):
                args = (observation, 3.1, 0.9, source, priority, 0.75)
                self.assertEqual(_decide("protective_distance", *args), _decide("hardened", *args))

    def test_ablation_completes_empty_route_at_geometry_bounds(self):
        scenario = ScenarioSpec(seed=42, human_present=False, robot_speed_mps=0.9)
        for crossing_x in (2, 10):
            with self.subTest(crossing_x=crossing_x):
                result = simulate(scenario, "protective_distance", crossing_x_m=crossing_x)
                self.assertTrue(result["summary"]["mission_completed"])
                self.assertEqual(result["summary"]["violations"], [])
                self.assertEqual(result["world"]["human_crossing_x_m"], crossing_x)


if __name__ == "__main__":
    unittest.main()
