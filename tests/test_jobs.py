"""WP14 lease/reconciliation acceptance, including real worker termination."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from worldfuzz.platform.contracts import Fault
from worldfuzz.platform.service import Service
from worldfuzz.platform.worker import run

ROOT=Path(__file__).resolve().parents[1]


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.service=Service(self.root/'results',self.root/'db',launch=False)
        self.plan=self.service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',case_indices=[0],max_seconds=20)
        self.job=self.service.execute_plan(self.plan['plan_id'],self.plan['plan_digest'],'one-logical-job')['run_id']
        self.children=[]

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:child.terminate()
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        self.tmp.cleanup()

    def paused_worker(self):
        child=subprocess.Popen([sys.executable,str(ROOT/'tests/fixtures/lease_worker.py'),
            str(self.service.root),str(self.service.corpus.path),self.job],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.children.append(child)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            attempts=self.service.jobs.attempts(self.job)
            if attempts and attempts[0]['artifacts']:return child,attempts[0]
            if child.poll() is not None:self.fail('Fault-injection worker exited before collection')
            time.sleep(.05)
        self.fail('Worker did not collect an artifact before the deadline')

    def test_killed_worker_reconciles_without_spending_unknown_cpu_again(self):
        child,first=self.paused_worker()
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            beat=self.service.jobs.attempts(self.job)[0]
            if beat['heartbeat']>first['heartbeat']:break
            time.sleep(.05)
        self.assertGreater(beat['heartbeat'],first['heartbeat'])
        child.terminate();child.wait(timeout=3)
        # Exercise lease expiry itself, not a fabricated successful worker exit.
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            state=self.service.get_run(self.job)
            if state['status']=='incomplete':break
            time.sleep(.05)
        self.assertEqual(state['status'],'incomplete')
        self.assertEqual(state['attempts'][0]['status'],'lost')
        partial=self.service.get_result(self.job)['result']
        self.assertEqual(len(partial['artifacts']),1)
        original_bytes=self.service.artifact(self.job,'route-0.json')
        self.assertEqual(self.service.execute_plan(self.plan['plan_id'],self.plan['plan_digest'],'one-logical-job')['run_id'],self.job)
        with self.assertRaises(Fault) as denied:self.service.resume_run(self.job,'retry-1')
        self.assertEqual(denied.exception.code,'BUDGET_EXCEEDED')
        final=self.service.get_run(self.job)
        self.assertEqual(final['status'],'incomplete',final)
        self.assertEqual(len(final['attempts']),1)
        self.assertEqual((self.service.root/self.job/'route-0.json').read_bytes(),original_bytes)
        with self.service.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0],1)

    def test_expired_worker_cannot_commit_result_checkpoint_or_artifact(self):
        owned=self.service.jobs.claim(self.job)
        with self.service.corpus.connect() as db:
            db.execute('UPDATE wf_attempts SET lease_until=0 WHERE attempt_id=?',(owned['attempt_id'],))
        self.service.jobs.reconcile(self.job)
        with self.assertRaises(Fault):self.service.finish(self.job,'completed',{'records':[{'forged':'stale'}]})
        with self.assertRaises(Fault):self.service.jobs.collect(self.job,'stale.json',b'{}',100)
        with self.assertRaises(Fault):self.service.checkpoint(self.job,{}, {'records':[]})
        self.assertEqual(self.service.get_run(self.job)['status'],'incomplete')
        self.assertFalse((self.service.root/self.job/'stale.json').exists())

    def test_cancel_stopped_worker_preserves_partial_artifacts_and_events(self):
        child,first=self.paused_worker()
        self.service.cancel_run(self.job)
        child.terminate();child.wait(timeout=3)
        self.service.jobs.reconcile(self.job,exit_code=child.returncode)
        result=self.service.get_run(self.job)
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(len(self.service.get_result(self.job)['result']['artifacts']),1)
        self.assertIn('cancel_requested',[event['status'] for event in result['events']])
        self.assertEqual(result['attempts'][0]['artifacts'][0]['sha256'],first['artifacts'][0]['sha256'])


if __name__=='__main__':unittest.main()
