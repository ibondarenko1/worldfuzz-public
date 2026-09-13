"""Independent evidence-contract review using mocked controller traces only."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from worldfuzz import regression
from worldfuzz.engine import write_json
from worldfuzz.experiment import digest, read_json
from worldfuzz.navigation import _digest, controller_provenance


def mock_navigation(scenario, configuration, **_kwargs):
    """Deterministic fixture with the full adapter envelope, never a live run."""
    trace = [{"t": round(i * scenario["dt_s"], 10), "x": 0.0, "y": 0.0,
              "yaw": 0.0, "v": 0.0, "omega": 0.0}
             for i in range(round(scenario["horizon_s"] / scenario["dt_s"]) + 1)]
    return {"schema_version": 1, "trace": trace, "status": "horizon_reached",
            "controller": controller_provenance(configuration),
            "scenario_sha256": _digest(scenario), "trace_sha256": _digest(trace),
            "steps_completed": len(trace) - 1}


class RegressionEvidenceReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name) / "run"
        self.project = regression.default_project()
        self.project["scenario"]["horizon_s"] = .2
        self.project["search"]["budget"] = 2
        self.project["reduction"]["budget"] = 0
        self.fixture = patch("worldfuzz.regression.simulate_navigation", side_effect=mock_navigation)
        self.mock = self.fixture.start()
        self.addCleanup(self.fixture.stop)

    def create(self, side_effect=None):
        if side_effect is not None:
            self.mock.side_effect = side_effect
        regression.new_regression(self.out, self.project)
        return regression.run_regression(self.out)

    def reseal(self, journal):
        journal["counts"] = regression._counts(journal)
        write_json(self.out / "regression.json", journal)
        regression._manifest(self.out, journal)

    def assert_rejected_before_replay(self):
        result = regression.verify_regression(self.out)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["started_simulations"], 0)

    def test_no_finding_validates_all_monitors_but_replays_only_first_pair(self):
        journal = self.create()
        self.assertEqual(journal["status"], "COMPLETED")
        self.assertEqual(journal["counts"]["regression_pairs"], 0)
        result = regression.verify_regression(self.out)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["completed_simulations"], 2)
        self.assertEqual(result["regressions"], [])

    def test_nonselected_run_is_bound_to_operation_scenario_and_configuration(self):
        journal = self.create()
        op = journal["operations"][3]  # The second pair is not replayed.
        original = read_json(self.out / op["run_file"])
        mutations = {
            "scenario_hash": lambda r: r.update(scenario_sha256="0" * 64),
            "trace_hash": lambda r: r.update(trace_sha256="0" * 64),
            "configuration_hash": lambda r: r["controller"].update(configuration_sha256="0" * 64),
            "planner_source": lambda r: r["controller"].update(source_sha256="0" * 64),
            "step_count": lambda r: r.update(steps_completed=999),
            "schema_type": lambda r: r.update(schema_version=True),
            "contradictory_goal_status": lambda r: r.update(status="goal_reached"),
        }
        for name, mutate in mutations.items():
            with self.subTest(field=name):
                run = deepcopy(original)
                mutate(run)
                write_json(self.out / op["run_file"], run)
                op["run_sha256"] = digest(run)
                self.reseal(journal)
                self.assert_rejected_before_replay()

    def test_partial_pair_preserves_baseline_then_candidate_role_order(self):
        baseline = mock_navigation(self.project["scenario"], self.project["baseline"])
        journal = self.create([baseline, RuntimeError("mock controller failure")])
        self.assertEqual(journal["status"], "FAILED")
        journal["operations"][1]["role"] = "baseline"
        self.reseal(journal)
        self.assert_rejected_before_replay()

    def test_failed_first_operation_still_belongs_to_declared_search_schedule(self):
        journal = self.create(RuntimeError("mock controller failure"))
        journal["operations"][0]["scenario"]["obstacles"] = [[19.0, 19.0]]
        self.reseal(journal)
        self.assert_rejected_before_replay()

    def test_boolean_operation_index_is_not_an_integer_identity(self):
        journal = self.create()
        journal["operations"][1]["index"] = True
        self.reseal(journal)
        self.assert_rejected_before_replay()

    def test_uncertain_intent_preserved_when_controller_process_is_interrupted(self):
        journal = self.create(KeyboardInterrupt())
        self.assertEqual(journal["status"], "CANCELLED")
        self.assertEqual(journal["counts"]["started_simulations"], 1)
        self.assertEqual(journal["counts"]["completed_simulations"], 0)
        self.assertEqual(journal["counts"]["unconfirmed_simulations"], 1)
        result = regression.verify_regression(self.out)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["scope"], "journal_without_completed_pair")
        self.assertEqual(result["started_simulations"], 0)

    def test_reduction_keeps_regression_and_fixed_physical_requirements(self):
        self.project["scenario"]["goal"] = [.2, 0.0]
        self.project["scenario"]["goal_tolerance_m"] = .05
        self.project["search"]["budget"] = 1
        self.project["reduction"]["budget"] = 2

        def motion(scenario, configuration, **kwargs):
            result = mock_navigation(scenario, configuration, **kwargs)
            # One retained obstacle preserves the mission regression. Removing
            # the last one removes the failure and must be rejected by reduction.
            completes = configuration["label"] == "Reference" or not scenario["obstacles"]
            if completes:
                for i, row in enumerate(result["trace"]):
                    row["x"] = i * .1
                    row["v"] = 1.0 if i else 0.0
                result["status"] = "goal_reached"
                result["trace_sha256"] = _digest(result["trace"])
            return result

        journal = self.create(motion)
        self.assertEqual(journal["status"], "COMPLETED")
        self.assertEqual(journal["selected_pair"], 1)
        selected = journal["pairs"][1]
        self.assertEqual(selected["regressions"], ["NEW_MISSION_FAILURE"])
        self.assertEqual(len(selected["scenario"]["obstacles"]), 1)
        self.assertEqual(journal["pairs"][2]["regressions"], [])
        for pair in journal["pairs"]:
            self.assertEqual({k: v for k, v in pair["scenario"].items() if k != "obstacles"},
                             {k: v for k, v in self.project["scenario"].items() if k != "obstacles"})
        result = regression.verify_regression(self.out)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["regressions"], ["NEW_MISSION_FAILURE"])
        journal["selected_pair"] = 2
        self.reseal(journal)
        self.assert_rejected_before_replay()


if __name__ == "__main__":
    unittest.main()
