"""Published-case regression and authored negative verifier checks."""
import copy
from pathlib import Path
import tempfile
import unittest
from worldfuzz.platform.service import Service
from worldfuzz.platform.worker import run
from worldfuzz.platform.verification import compare_navigation
from worldfuzz.platform.verifier import verify
from worldfuzz.platform.contracts import Fault
from worldfuzz.published_benchmarks import load_cases
from worldfuzz.corpus import encoded


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=Service(self.root/'results',self.root/'data.sqlite3',launch=False)

    def tearDown(self):self.temp.cleanup()

    def execute(self, algorithm, max_seconds=30):
        plan=self.service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',
                                      case_indices=[0],algorithm=algorithm,max_seconds=max_seconds)
        job=self.service.execute_plan(plan['plan_id'],plan['plan_digest'],plan['plan_id'])['run_id']
        run(self.service,job)
        return job,plan

    def verify_candidate(self, payload):
        prepared=self.service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',
                                           case_indices=[0],algorithm='astar',max_seconds=30)
        job=self.service.execute_plan(prepared['plan_id'],prepared['plan_digest'],prepared['plan_id'])['run_id']
        self.service.jobs.claim(job)
        artifacts=[self.service.jobs.collect(job,'route-0.json',encoded(payload).encode(),1024*1024)]
        records=[{'candidate_claim':'ignored'}]
        def checkpoint():self.service.checkpoint(job,{}, {'records':records,'artifacts':artifacts})
        return job,records,verify(self.service,job,prepared['plan'],records,artifacts,checkpoint,accumulate=True)

    def test_algorithms_compared_under_one_verifier(self):
        left,a=self.execute('astar');right,b=self.execute('dijkstra')
        comparison=self.service.compare_results(left,right,mode='algorithms')
        self.assertTrue(comparison['comparable']);self.assertTrue(comparison['equivalent_outcomes'])
        self.assertEqual(comparison['coverage'],{'expected':1,'compared':1})
        self.assertEqual(comparison['paired_results'][0]['length_delta_right_minus_left'],0)
        with self.assertRaises(Fault):self.service.compare_results(left,right)
        episodes=self.service.corpus.listing()['episodes']
        self.assertEqual(len(episodes),2)
        self.assertEqual(len({e['group_id'] for e in episodes}),1)
        # Missing coverage cannot produce a successful comparison.
        l=self.service.get_result(left);r=self.service.get_result(right)
        r['result']['records']=[]
        with self.assertRaises(Fault):compare_navigation(l,r,a['plan'],b['plan'])

    def test_different_budgets_are_not_comparable(self):
        left,_=self.execute('astar',30);right,_=self.execute('dijkstra',31)
        response=self.service.call('compare_results',{'left_id':left,'right_id':right,'mode':'algorithms'})
        self.assertEqual(response['error']['code'],'INCOMPARABLE')

    def test_forged_success_is_replaced_by_verifier_and_not_accumulated(self):
        case=load_cases()[3][0]
        job,records,report=self.verify_candidate({'case':case,'route':[], 'check':{'route_valid':True}})
        self.assertEqual(records[0]['outcome'],'failed')
        self.assertFalse(records[0]['check']['route_valid'])
        self.assertEqual(report['proven_result'],'violation_found')
        self.assertEqual(self.service.corpus.listing()['episodes'],[])
        self.service.finish(job,'completed',{'records':records,'artifacts':[],'verification':report})

    def test_candidate_cannot_replace_frozen_task(self):
        case=copy.deepcopy(load_cases()[3][0]);case['published_length']=0
        with self.assertRaises(Fault) as raised:self.verify_candidate({'case':case,'route':[]})
        self.assertEqual(raised.exception.code,'VERIFICATION_FAILED')
        self.assertEqual(self.service.corpus.listing()['episodes'],[])
