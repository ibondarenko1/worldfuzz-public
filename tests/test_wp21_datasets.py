import unittest

from worldfuzz.platform.adapters.dataset_build import DatasetAdapter, selected
from worldfuzz.platform.contracts import Fault, OPERATIONS, SUCCESS_DATA


class _Corpus:
    def listing(self):
        return {'episodes': [
            {'id':'a','domain':'manipulation','group_id':'pair-1','split':'train'},
            {'id':'b','domain':'manipulation','group_id':'pair-1','split':'train'},
            {'id':'c','domain':'manipulation','group_id':'pair-2','split':'test'},
            {'id':'d','domain':'manipulation','group_id':'pair-2','split':'test'}]}


class _Service:
    corpus = _Corpus()


class DatasetGateTests(unittest.TestCase):
    def test_whole_groups_and_heldout_training_are_enforced(self):
        service=_Service()
        self.assertEqual(selected(service,{'episode_ids':['b','a'],'purpose':'training',
            'normalization_split':'train'},'manipulation'),['a','b'])
        with self.assertRaisesRegex(Fault,'every member'):
            selected(service,{'episode_ids':['a'],'purpose':'evaluation','normalization_split':'train'},'manipulation')
        with self.assertRaisesRegex(Fault,'only the original train'):
            selected(service,{'episode_ids':['c','d'],'purpose':'training','normalization_split':'train'},'manipulation')

    def test_shared_contract_exposes_versioned_build_and_read(self):
        descriptor=DatasetAdapter('dataset.can.lerobot.v3').describe()
        self.assertEqual(descriptor['profile']['operations'],['build_dataset'])
        self.assertIn('get_dataset_generation',OPERATIONS)
        self.assertIn('dataset_manifest',SUCCESS_DATA['get_dataset_generation']['properties'])
        self.assertIn('never full simulator state',descriptor['profile']['observation'])


if __name__=='__main__':unittest.main()
