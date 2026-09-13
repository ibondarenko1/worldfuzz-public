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


GOAL={'goal':{'operation':'solve_navigation','profile':'movingai.octile.v1',
    'parameters':{'case_indices':[0],'max_cases':1,'algorithm':'astar'},'max_seconds':30,
    'resource_limits':{'cpu_seconds':20,'memory_bytes':268435456,'artifact_bytes':1048576}},
    'provider_mode':'contract_only'}


class GoalPlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=self.tmp.name+'/jobs';self.store=self.tmp.name+'/db.sqlite3'
        self.service=Service(self.root,self.store,launch=False)

    def tearDown(self):self.tmp.cleanup()

    def test_structured_and_ambiguous_goals(self):
        ambiguous=self.service.call('compile_goal',{'goal':{},'provider_mode':'contract_only'})['data']
        self.assertEqual(ambiguous['status'],'needs_information')
        self.assertEqual({x['path'] for x in ambiguous['missing_parameters']},{'goal.operation','goal.profile'})
        self.assertFalse(ambiguous['provider']['completion_request_attempted'])
        ready=self.service.call('compile_goal',GOAL)['data']
        self.assertEqual(ready['status'],'ready')
        self.assertEqual(ready['typed_request']['parameters']['case_indices'],[0])
        self.assertEqual(ready['prepared_plan']['compatibility'],'compatible')
        self.assertEqual(ready['prepared_plan']['run_plan']['resource_limits']['cpu_seconds'],20)
        self.assertEqual(ready['provider_calls'],0)

    def test_rights_gate_is_the_existing_prepare_gate(self):
        denied=Service(self.tmp.name+'/restricted',self.tmp.name+'/restricted.sqlite3',launch=False,
            allowed_actions=['inspect']).call('compile_goal',GOAL)
        self.assertEqual(denied['status'],'error')
        self.assertEqual(denied['error']['code'],'ACCESS_DENIED')

    def test_http_sdk_cli_and_mcp_share_the_compiler(self):
        server=make_server(self.service);thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}/v1/goals/compile'
            request=Request(url,data=json.dumps(GOAL).encode(),method='POST',headers={
                'Authorization':'Bearer '+server.token,'Content-Type':'application/json'})
            with urlopen(request) as response:http=json.load(response)
            sdk=Client(f'http://127.0.0.1:{server.server_port}',server.token).call('compile_goal',GOAL)
        finally:
            server.shutdown();server.server_close();thread.join()
        process=subprocess.run([sys.executable,'-m','worldfuzz','platform','--root',self.root,'--store',self.store,
            'call','compile_goal','--json',json.dumps(GOAL)],capture_output=True,text=True,check=True)
        cli=json.loads(process.stdout)
        mcp=handle(self.service,{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
            'name':'compile_goal','arguments':GOAL}})['result']['structuredContent']
        for response in (http,sdk,cli,mcp):
            self.assertEqual(response['status'],'ok')
            self.assertEqual(response['data']['status'],'ready')
            self.assertEqual(response['data']['typed_request'],http['data']['typed_request'])
            self.assertEqual(response['data']['provider_calls'],0)
            self.assertFalse(response['data']['provider']['contract_validation_is_inference'])


if __name__=='__main__':unittest.main()
