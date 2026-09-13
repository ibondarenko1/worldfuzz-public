"""External planner contracts, independent of historical WorldFuzz pilots."""

import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from worldfuzz.navigation import (SOURCE_SHA256, default_configuration, default_scenario,
                                 simulate_navigation, validate_configuration, validate_scenario)


class NavigationValidationTests(unittest.TestCase):
    def test_reject_unsupported_inputs_without_loading_planner(self):
        with patch("worldfuzz.navigation._planner", side_effect=AssertionError("planner loaded")):
            for key, value in (("unsupported_sensor", True), ("schema_version", True),
                               ("horizon_s", 30.1), ("dt_s", .001),
                               ("robot_radius_m", float("nan")), ("seed", -1),
                               ("goal", [0, float("inf")]), ("start", [0, 0, 4]),
                               ("obstacles", [[0, 0]] * 41)):
                with self.subTest(field=key):
                    scenario = default_scenario()
                    scenario[key] = value
                    with self.assertRaises(ValueError):
                        simulate_navigation(scenario, default_configuration())
            for value in (True, "1", 10**500, float("nan"), -1):
                configuration = default_configuration()
                configuration["parameters"]["planning_radius_m"] = value
                with self.subTest(value=repr(value)):
                    with self.assertRaises(ValueError):
                        simulate_navigation(default_scenario(), configuration)

    def test_bounds_and_no_silent_config_defaults(self):
        scenario = default_scenario()
        scenario.update(horizon_s=30, dt_s=.1, obstacles=[], seed=2**32 - 1)
        self.assertEqual(validate_scenario(scenario)["obstacles"], [])
        scenario["dt_s"] = .05
        with self.assertRaisesRegex(ValueError, "300"):
            validate_scenario(scenario)
        configuration = default_configuration()
        del configuration["parameters"]["planning_radius_m"]
        with self.assertRaises(ValueError):
            validate_configuration(configuration)
        configuration = default_configuration()
        configuration["controller"] = "external-command"
        with self.assertRaises(ValueError):
            validate_configuration(configuration)

    def test_normalization_preserves_independent_physical_and_planner_radii(self):
        scenario = default_scenario()
        configuration = default_configuration()
        configuration["parameters"]["planning_radius_m"] = .05
        normalized = validate_scenario(scenario)
        normalized["obstacles"].clear()
        self.assertEqual(len(scenario["obstacles"]), 2)
        self.assertEqual(validate_scenario(scenario)["robot_radius_m"], .3)
        self.assertEqual(validate_configuration(configuration)["parameters"]["planning_radius_m"], .05)

    def test_vendored_source_and_license_match_pinned_provenance(self):
        vendor = Path(__file__).resolve().parents[1] / "worldfuzz" / "vendor"
        provenance = json.loads((vendor / "PYTHONROBOTICS_PROVENANCE.json").read_text())
        self.assertEqual(hashlib.sha256((vendor / "pythonrobotics_dwa.py").read_bytes()).hexdigest(), SOURCE_SHA256)
        self.assertEqual(hashlib.sha256((vendor / "PYTHONROBOTICS_LICENSE").read_bytes()).hexdigest(),
                         provenance["license_sha256"])


@unittest.skipUnless(importlib.util.find_spec("numpy") and importlib.util.find_spec("matplotlib"),
                     "optional navigation dependencies unavailable")
class NavigationExecutionTests(unittest.TestCase):
    def test_real_upstream_controller_repeatable_and_separate_mission_outcome(self):
        scenario = default_scenario()
        reference = default_configuration()
        candidate = copy.deepcopy(reference)
        candidate["label"] = "Increased planning radius, tuning example"
        candidate["parameters"]["planning_radius_m"] = .8
        with patch("socket.socket.connect", side_effect=AssertionError("network access")), \
             patch("worldfuzz.connection.load_credentials", side_effect=AssertionError("credentials")):
            first = simulate_navigation(scenario, reference)
            replay = simulate_navigation(scenario, reference)
            second = simulate_navigation(scenario, candidate)
        self.assertEqual(first, replay)
        self.assertEqual(first["status"], "goal_reached")
        self.assertEqual(second["status"], "horizon_reached")
        self.assertNotEqual(first["controller"]["configuration_sha256"], second["controller"]["configuration_sha256"])
        self.assertEqual(first["scenario_sha256"], second["scenario_sha256"])
        for result in (first, second):
            # Independent sampled position check; production monitor additionally
            # measures swept segments and retains its own evidence/limits.
            self.assertTrue(all(math.hypot(row["x"] - point[0], row["y"] - point[1]) > scenario["robot_radius_m"]
                                for row in result["trace"] for point in scenario["obstacles"]))
            self.assertEqual(set(result["trace"][0]), {"t", "x", "y", "yaw", "v", "omega"})
            self.assertEqual(result["controller"]["source_sha256"], SOURCE_SHA256)

    def test_empty_world_and_initial_goal_termination(self):
        scenario = default_scenario()
        scenario.update(obstacles=[], goal=[.5, 0], horizon_s=3)
        result = simulate_navigation(scenario, default_configuration())
        self.assertEqual(result["status"], "goal_reached")
        self.assertGreater(result["steps_completed"], 0)
        scenario["goal"] = [0, 0]
        result = simulate_navigation(scenario, default_configuration())
        self.assertEqual(result["steps_completed"], 0)

    def test_cooperative_cancel_keeps_partial_trace(self):
        checks = []
        def cancelled():
            checks.append(True)
            return len(checks) == 4
        result = simulate_navigation(default_scenario(), default_configuration(), cancelled=cancelled)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["steps_completed"], 3)
        self.assertEqual(result["trace"][-1]["t"], .3)


if __name__ == "__main__":
    unittest.main()
