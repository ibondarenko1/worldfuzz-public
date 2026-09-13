import tempfile
import unittest

from worldfuzz.platform.reuse import _episode_ids,_evidence
from worldfuzz.platform.service import Service


class ReuseContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.service=Service(self.tmp.name+'/jobs',self.tmp.name+'/db.sqlite3',launch=False)
    def tearDown(self):self.tmp.cleanup()

    def test_evidence_link_is_a_valid_typed_entity(self):
        plan={'created_at':1,'contract_plan':{'task_ref':{'id':'task.solve_navigation','version':'1.0.0'}}}
        result={'result':{'verification':{'verification_id':'a'*64,'performed_checks':[{
            'mode':'integrity','protocol':'sha256-artifacts-v1','status':'passed'}],
            'scope':'stored bytes only','verifier_sha256':'b'*64,'input_snapshot_sha256':'c'*64}}}
        evidence=_evidence(plan,result)
        self.assertEqual(evidence['kind'],'integrity')
        self.assertEqual(evidence['input_hashes'][0]['sha256'],'c'*64)

    def test_duplicate_selection_fails_before_any_automatic_inclusion(self):
        response=self.service.call('create_suite',{'name':'duplicate','purpose':'evaluation',
            'selected_result_ids':['a'*32,'a'*32],'dataset_result_id':'b'*32})
        self.assertEqual(response['status'],'error')
        self.assertEqual(response['error']['code'],'DUPLICATE_SELECTION')
        empty=self.service.call('find_verified_results',{'profile':'movingai.octile.v1',
            'operation':'solve_navigation','purpose':'evaluation','limit':10,'cursor':0})
        self.assertEqual(empty['data']['matches'],[])
        self.assertFalse(empty['data']['automatic_inclusion'])

    def test_manipulation_result_maps_to_exact_episode(self):
        plan={'request':{'operation':'view_recorded_states'},'snapshot':{'episode':{'id':'a'*64}}}
        self.assertEqual(_episode_ids(self.service,plan),['a'*64])


if __name__=='__main__':unittest.main()
