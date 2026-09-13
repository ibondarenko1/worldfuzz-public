"""WP15 common mode semantics and negative verification acceptance."""
from pathlib import Path
import tempfile
import unittest

from worldfuzz.platform.service import Service
from worldfuzz.platform.worker import run


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.service=Service(self.root/'results',self.root/'db',launch=False)

    def tearDown(self):self.tmp.cleanup()

    def completed(self,**extra):
        prepared=self.service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',
                                          case_indices=[0],max_seconds=15,**extra)
        job=self.service.execute_plan(prepared['plan_id'],prepared['plan_digest'],'verified')['run_id']
        run(self.service,job)
        self.assertEqual(self.service.get_run(job)['status'],'completed',self.service.get_run(job))
        return job

    def test_no_finding_metric_result_and_integrity_have_distinct_claims(self):
        job=self.completed()
        original=self.service.get_result(job)['result']['verification']
        self.assertEqual(original['requested_mode'],'metric_recompute')
        self.assertEqual(original['proven_result'],'no_violation_found')
        self.assertFalse(original['physics_replayed'])
        rechecked=self.service.call('verify_run_result',{'result_id':job,'mode':'metric_recompute'})
        self.assertEqual(rechecked['status'],'ok',rechecked)
        integrity=self.service.call('verify_run_result',{'result_id':job,'mode':'integrity'})['data']
        self.assertEqual(integrity['proven_result'],'not_evaluated')
        self.assertEqual([r['mode'] for r in integrity['performed_checks']],['integrity'])
        self.assertFalse(integrity['physics_replayed'])
        self.assertEqual(integrity,self.service.verify_run_result(job))
        self.assertEqual(self.service.get_result(job)['result']['verification'],original)

    def test_bad_hash_and_metric_protocol_are_rejected(self):
        job=self.completed()
        mismatch=self.service.call('verify_run_result',{'result_id':job,'mode':'metric_recompute','protocol':'different-units-v1'})
        self.assertEqual(mismatch['error']['code'],'VERIFICATION_INCOMPATIBLE')
        (self.service.root/job/'route-0.json').write_bytes(b'changed')
        response=self.service.call('verify_run_result',{'result_id':job})
        self.assertEqual(response['error']['code'],'INTEGRITY_FAILED')

    def test_replay_is_rejected_before_launch_and_for_completed_result(self):
        response=self.service.call('prepare_plan',{'operation':'solve_navigation',
            'profile':'movingai.octile.v1','verification':{'mode':'replay'}})
        self.assertEqual(response['error']['code'],'VERIFICATION_UNSUPPORTED')
        with self.service.corpus.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0],0)
        job=self.completed()
        response=self.service.call('verify_run_result',{'result_id':job,'mode':'replay'})
        self.assertEqual(response['error']['code'],'VERIFICATION_UNSUPPORTED')

    def test_integrity_only_does_not_accumulate_verified_outcomes(self):
        job=self.completed(verification={'mode':'integrity'})
        result=self.service.get_result(job)['result']
        self.assertEqual(result['verification']['proven_result'],'not_evaluated')
        self.assertEqual(result['records'][0]['outcome'],'unverified')
        self.assertEqual(self.service.corpus.listing()['episodes'],[])


if __name__=='__main__':unittest.main()
