"""Focused regressions for reviewed M3 admission and CPU accounting defects."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from worldfuzz.platform.contracts import Fault
from worldfuzz.platform.service import Service
from worldfuzz.platform.worker import run


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        self.service=Service(root/'results',root/'db',launch=False)
        self.plan=self.service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',
                                            case_indices=[0],max_seconds=10)

    def tearDown(self):self.tmp.cleanup()

    def execute(self,key):
        return self.service.call('execute_plan',{'plan_id':self.plan['plan_id'],
            'plan_digest':self.plan['plan_digest'],'idempotency_key':key})

    def test_concurrent_admission_has_one_reservation_and_every_accepted_job_runs(self):
        # Concurrent API admission only; one compute worker is launched below.
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers=list(pool.map(self.execute,['first','second']))
        self.assertEqual(sorted(a['status'] for a in answers),['error','ok'])
        accepted=next(a['data'] for a in answers if a['status']=='ok')
        rejected=next(a for a in answers if a['status']=='error')
        self.assertEqual(rejected['error']['code'],'RESOURCE_BUSY')
        with self.service.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_budget_reservations').fetchone()[0],1)
        run(self.service,accepted['run_id'])
        self.assertEqual(self.service.get_run(accepted['run_id'])['status'],'completed')
        blocked_key='first' if answers[0]['status']=='error' else 'second'
        following=self.execute(blocked_key)['data']['run_id']
        run(self.service,following)
        self.assertEqual(self.service.get_run(following)['status'],'completed')

    def test_retry_key_is_preserved_when_recovery_capacity_is_busy(self):
        original=self.execute('original')['data']['run_id']
        # A dispatch interruption before the attempt consumed any resources.
        self.service.finish(original,'incomplete',{'records':[]},'DISPATCH_INTERRUPTED')
        active=self.execute('active')['data']['run_id']
        self.assertEqual(self.execute('active')['data']['run_id'],active)
        denied=self.service.call('resume_run',{'run_id':original,'idempotency_key':'resume'})
        self.assertEqual(denied['error']['code'],'RESOURCE_BUSY')
        with self.service.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_retries').fetchone()[0],0)
        self.service.cancel_run(active);run(self.service,active)
        self.assertEqual(self.service.resume_run(original,'resume')['run_id'],original)
        self.assertEqual(self.service.resume_run(original,'resume')['run_id'],original)
        run(self.service,original)
        self.assertEqual(self.service.get_run(original)['status'],'completed')


class LostCpuTests(unittest.TestCase):
    def test_short_wall_loss_holds_all_cpu_without_rewriting_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);clock=[100.0]
            service=Service(root/'out',root/'db',launch=False,clock=lambda:clock[0])
            plan=service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',
                case_indices=[0],max_seconds=30)
            job=service.execute_plan(plan['plan_id'],plan['plan_digest'],'lost')['run_id']
            attempt=service.jobs.claim(job)
            # Authored observations deliberately describe CPU > wall, which is
            # valid for a multithreaded process. No CPU load is needed to test
            # this accounting invariant.
            service.budgets.sample(job,'search',{'cpu_seconds':2.0,'wall_seconds':0.5})
            clock[0]+=.5
            service.jobs.reconcile(job,exit_code=1,expected_pid=__import__('os').getpid())
            before=service.get_budget(job)
            ledger=before['attempt_accounting'][0]
            self.assertEqual(ledger['observed_cpu_seconds'],2)
            self.assertEqual(ledger['charge_cpu'],30)
            self.assertEqual(ledger['unmeasured_cpu_hold_seconds'],28)
            self.assertEqual(ledger['charge_basis'],'full_attempt_allocation_after_loss')
            self.assertTrue(any(x['resource']=='cpu_seconds' for x in before['reservation']['unknown_usage']))
            for key in ('retry','retry'):
                response=service.call('resume_run',{'run_id':job,'idempotency_key':key})
                self.assertEqual(response['error']['code'],'BUDGET_EXCEEDED')
            service.jobs.reconcile(job,exit_code=1)
            self.assertEqual(before,service.get_budget(job))
            with service.corpus.connect() as db:
                db.execute('UPDATE wf_budget_attempts SET charge_cpu=2,charge_wall=.5 WHERE attempt_id=?',
                           (attempt['attempt_id'],))
            reopened=Service(root/'out',root/'db',launch=False,clock=lambda:clock[0])
            self.assertEqual(before,reopened.get_budget(job))
            with service.corpus.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_retries').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_attempts').fetchone()[0],1)
