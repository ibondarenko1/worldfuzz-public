import json
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen

from worldfuzz.platform.http import make_server
from worldfuzz.platform.mcp import handle
from worldfuzz.platform.sdk import Client
from worldfuzz.platform.service import Service
from worldfuzz.platform.campaigns import _spent


def goal(algorithm='astar'):
    return {'operation':'solve_navigation','profile':'movingai.octile.v1',
        'parameters':{'case_indices':[0],'max_cases':1,'algorithm':algorithm},'max_seconds':20,
        'resource_limits':{'cpu_seconds':10,'memory_bytes':268435456,'artifact_bytes':1048576}}


def request():
    return {'name':'bounded','cases':[
        {'case_id':'published','origin':'published','goal':goal()},
        {'case_id':'derived','origin':'derived','parent_case_id':'published',
         'mutations':[{'path':'goal.parameters.algorithm','value':'dijkstra'}],'goal':goal('dijkstra')}],
        'allowed_mutations':['goal.parameters.algorithm'],
        'immutable_criteria':['published source','verification protocol'],
        'budget':{'max_cases':2,'max_attempts':3,'cpu_seconds':40,'wall_seconds':40,'artifact_bytes':4194304},
        'reduction':'exact_typed_request'}


class CampaignDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=self.tmp.name+'/jobs';self.store=self.tmp.name+'/db.sqlite3'
        self.service=Service(self.root,self.store,launch=False,clock=lambda:10)
    def tearDown(self):self.tmp.cleanup()

    def test_definition_is_versioned_and_idempotent(self):
        first=self.service.call('create_campaign',request())
        second=self.service.call('create_campaign',request())
        self.assertEqual(first['status'],'ok');self.assertEqual(first['data'],second['data'])
        campaign=first['data']['campaign']
        self.assertTrue(campaign['source_cases_immutable'])
        self.assertFalse(campaign['derived_cases_are_published_benchmarks'])
        self.assertEqual(campaign['cases'][1]['parent_case_id'],'published')

    def test_unapproved_mutation_and_small_budget_fail(self):
        invalid=request();invalid['cases'][1]['mutations'][0]['path']='goal.parameters.case_indices'
        self.assertEqual(self.service.call('create_campaign',invalid)['error']['code'],'SCHEMA_INVALID')
        small=request();small['budget']['max_cases']=1
        self.assertEqual(self.service.call('create_campaign',small)['error']['code'],'BUDGET_EXCEEDED')

    def test_four_interfaces_share_campaign_identity(self):
        arguments=request();server=make_server(self.service);thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            call=Request(f'http://127.0.0.1:{server.server_port}/v1/campaigns',data=json.dumps(arguments).encode(),
                method='POST',headers={'Authorization':'Bearer '+server.token,'Content-Type':'application/json'})
            with urlopen(call) as response:http=json.load(response)
            sdk=Client(f'http://127.0.0.1:{server.server_port}',server.token).call('create_campaign',arguments)
        finally:server.shutdown();server.server_close();thread.join()
        process=subprocess.run([sys.executable,'-m','worldfuzz','platform','--root',self.root,'--store',self.store,
            'call','create_campaign','--json',json.dumps(arguments)],capture_output=True,text=True,check=True)
        cli=json.loads(process.stdout)
        mcp=handle(self.service,{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
            'name':'create_campaign','arguments':arguments}})['result']['structuredContent']
        ids={x['data']['campaign_id'] for x in (http,sdk,cli,mcp)}
        self.assertEqual(len(ids),1)

    def test_failed_attempt_charge_and_unknown_tail_are_not_dropped(self):
        class Failed:
            def get_budget(self,_):return {'reservation':{'actual_usage':[{
                'phase':'search','resource':'artifact_bytes','value':12}],
                'unknown_usage':[{'phase':'search','resource':'cpu_seconds','reason':'worker lost'}]},
                'attempt_accounting':[{'charge_cpu':10,'charge_wall':4}]}
        value=_spent(Failed(),[{'run':{'run_id':'failed-run'}}])
        self.assertEqual(value['cpu_seconds'],10)
        self.assertEqual(value['artifact_bytes'],12)
        self.assertEqual(value['unknown_usage'][0]['run_id'],'failed-run')


if __name__=='__main__':unittest.main()
