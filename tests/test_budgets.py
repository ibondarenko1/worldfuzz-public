"""WP16 atomic reservation, phase accounting and terminal-semantics acceptance."""
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest

from worldfuzz.platform.service import Service
from worldfuzz.platform.worker import run

ROOT = Path(__file__).resolve().parents[1]


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root/'results', self.root/'corpus.sqlite3', launch=False)
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        self.temp.cleanup()

    def plan(self, **extra):
        return self.service.prepare_plan(
            operation='solve_navigation', profile='movingai.octile.v1',
            case_indices=[0], max_seconds=10, **extra)

    def execute(self, plan, key):
        return self.service.execute_plan(plan['plan_id'], plan['plan_digest'], key)['run_id']

    def test_reservation_and_usage_are_idempotent_and_phase_separated(self):
        plan = self.plan()
        run_id = self.execute(plan, 'one')
        self.assertEqual(self.execute(plan, 'one'), run_id)
        with self.service.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_budget_reservations').fetchone()[0], 1)
        run(self.service, run_id)
        report = self.service.get_budget(run_id)
        self.assertEqual(report['reservation']['state'], 'released')
        self.assertEqual({x['phase'] for x in report['reservation']['actual_usage']},
                         {'search', 'reduction', 'verification'})
        calls = [x for x in report['reservation']['actual_usage']
                 if x['resource'] in ('model_calls', 'device_calls')]
        self.assertTrue(all(x['value'] == 0 for x in calls))
        self.assertTrue(any(x['resource'] == 'memory_bytes'
                            for x in report['reservation']['unknown_usage']))

    def test_retry_key_does_not_reserve_or_charge_twice(self):
        plan = self.plan()
        run_id = self.execute(plan, 'logical')
        first = self.service.jobs.claim(run_id)
        self.service.budgets.sample(run_id, 'search', {'cpu_seconds': .2, 'wall_seconds': .3})
        # A closed, metered interruption has a known remainder. An unobserved
        # loss must hold the whole allocation (covered by remediation tests).
        self.service.finish(run_id,'incomplete',{'records':[],'artifacts':[]},'METERED_INTERRUPTION')
        self.assertTrue(self.service.jobs.resume(run_id, 'retry-1'))
        self.assertFalse(self.service.jobs.resume(run_id, 'retry-1'))
        second = self.service.jobs.claim(run_id)
        self.service.budgets.sample(run_id, 'search', {'cpu_seconds': .1, 'wall_seconds': .1})
        self.service.finish(run_id, 'completed', {'records': [], 'artifacts': []})
        report = self.service.get_budget(run_id)
        self.assertEqual(len(report['attempt_accounting']), 2)
        self.assertEqual(len({first['attempt_id'], second['attempt_id']}), 2)
        with self.service.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_budget_reservations').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_retries').fetchone()[0], 1)

    def test_enforced_cpu_exit_is_budget_exhaustion_and_usage_tail_is_unknown(self):
        plan = self.plan(resource_limits={'cpu_seconds': .2, 'memory_bytes': 128*1024*1024,
                                         'artifact_bytes': 1024})
        run_id = self.execute(plan, 'cpu')
        launch_id = 'budget-exhaustion-fixture'
        child = subprocess.Popen([
            sys.executable, str(ROOT/'tests/fixtures/budget_worker.py'),
            str(self.service.root), str(self.service.corpus.path), run_id, launch_id])
        self.children.append(child)
        code = child.wait(timeout=10)
        self.service.jobs.reconcile(run_id, exit_code=code, expected_launch_id=launch_id)
        record = self.service.get_run(run_id)
        self.assertEqual(record['status'], 'budget_exhausted',
                         (record, self.service.jobs.attempts(run_id), child.pid, code))
        report = self.service.get_budget(run_id)
        self.assertEqual(report['reservation']['state'], 'released')
        unknown = {(x['phase'], x['resource']) for x in report['reservation']['unknown_usage']}
        self.assertIn(('search', 'cpu_seconds'), unknown)
        self.assertIn(('search', 'wall_seconds'), unknown)

    def test_robotic_task_failure_is_a_completed_verified_result(self):
        asset = self.service.register_asset(
            name='Authored failing metric', source_url='urn:test:wp16',
            data_license='private test data', code_license='MIT', frame='world', units='m',
            prediction=[[0, 0, 0, 0], [1, 2, 0, 0]],
            reference=[[0, 0, 0, 0], [1, 1, 0, 0]])
        plan = self.service.prepare_plan(
            operation='evaluate_trajectory', profile='sensor.trajectory.xyz.v1',
            asset_id=asset['asset_id'], threshold_m=0, max_seconds=10)
        run_id = self.execute(plan, 'task-failure')
        run(self.service, run_id)
        record = self.service.get_run(run_id)
        result = self.service.get_result(run_id)['result']
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(result['records'][0]['outcome'], 'fail')
        self.assertEqual(result['verification']['proven_result'], 'violation_found')


if __name__ == '__main__':
    unittest.main()
