"""Protect the distinction between observed manual effort and wrapper execution."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('wp31_manual_actions', Path(__file__).parents[1] / 'evaluation/workflows/manual_actions.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ObservationTests(unittest.TestCase):
    def test_rework_counts_but_instrumentation_and_scripted_calls_are_not_human_actions(self):
        session = {'actor': 'human', 'status': 'completed', 'events': [
            {'event': 'start', 'phase': 'setup', 'elapsed_seconds': 0},
            {'event': 'action', 'kind': 'command', 'phase': 'setup', 'elapsed_seconds': 1},
            {'event': 'observer_bookmark', 'phase': 'setup', 'elapsed_seconds': 2},
            {'event': 'action', 'kind': 'edit', 'phase': 'configuration', 'elapsed_seconds': 3},
            {'event': 'action', 'kind': 'intervention', 'phase': 'configuration', 'elapsed_seconds': 4},
            {'event': 'finish', 'phase': 'cleanup', 'elapsed_seconds': 5}]}
        report = module.summarize(session)
        self.assertEqual(report['human_manual_actions'], 3)
        self.assertEqual(report['actions_by_phase'], {'setup': 1, 'configuration': 2})
        self.assertIsNone(report['benefit_target_demonstrated'])
        for actor in ('agent', 'scripted'):
            self.assertIsNone(module.summarize({**session, 'actor': actor})['human_manual_actions'])
        for status in ('observing', 'failed', 'blocked', 'interrupted'):
            report = module.summarize({**session, 'status': status})
            self.assertIsNone(report['human_manual_actions'])
            self.assertEqual(report['recorded_actions'], 3)
