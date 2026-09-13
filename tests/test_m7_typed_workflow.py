"""Focused M7 regression for the typed, bounded, transport-shared workflow."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from worldfuzz.platform.contracts import Fault, OPERATIONS
from worldfuzz.platform.http import make_server
from worldfuzz.platform.identity import IdentityStore
from worldfuzz.platform.mcp import handle
from worldfuzz.platform.sdk import Client
from worldfuzz.platform.service import Service
from worldfuzz.platform.worker import run


ROOT=Path(__file__).resolve().parents[1]
GOAL={'operation':'solve_navigation','profile':'movingai.octile.v1',
      'parameters':{'case_indices':[0],'max_cases':1,'algorithm':'astar'},
      'verification':{'mode':'metric_recompute'},'max_seconds':20,
      'resource_limits':{'cpu_seconds':10,'memory_bytes':268435456,'artifact_bytes':8388608}}
REQUEST={'goal':GOAL,'idempotency_key':'typed-nav-0','resource_id':'movingai:0',
         'wait_seconds':0,'include_artifacts':False}


class TypedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.service=Service(self.base/'results',self.base/'catalog.sqlite3',launch=False)

    def tearDown(self):self.temp.cleanup()

    def count(self, table):
        with self.service.corpus.connect() as db:return db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def test_one_typed_request_is_shared_by_http_sdk_mcp_and_cli(self):
        server=make_server(self.service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            request=Request(base+'/v1/goals/run',data=json.dumps(REQUEST).encode(),method='POST',
                            headers={'Authorization':'Bearer '+server.token,'Content-Type':'application/json'})
            with urlopen(request,timeout=15) as response:http=json.load(response)
            sdk=Client(base,server.token).run_goal(GOAL,'typed-nav-0',resource_id='movingai:0',wait_seconds=0)
        finally:
            server.shutdown();server.server_close();thread.join()
        mcp=handle(self.service,{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
            'name':'run_goal','arguments':REQUEST}})['result']['structuredContent']
        identifiers={reply['data']['run_id'] for reply in (http,sdk,mcp)}
        self.assertEqual(len(identifiers),1)
        run_id=identifiers.pop()
        self.assertTrue(run_id)
        self.assertEqual({reply['data']['status'] for reply in (http,sdk,mcp)},{'waiting'})
        self.assertEqual(self.count('wf_runs'),1)
        self.assertEqual(self.count('wf_budget_reservations'),1)

        run(self.service,run_id)
        export=self.base/'export'
        process=subprocess.run([sys.executable,'-m','worldfuzz','platform','--root',str(self.base/'results'),
            '--store',str(self.base/'catalog.sqlite3'),'run','navigation','--key','typed-nav-0',
            '--resource','movingai:0','--wait-seconds','0','--export',str(export)],
            cwd=ROOT,capture_output=True,text=True,timeout=25)
        cli=json.loads(process.stdout)
        self.assertEqual(process.returncode,0,process.stderr+'\n'+process.stdout)
        self.assertEqual(cli['data']['status'],'terminal')
        self.assertEqual(cli['data']['run_id'],run_id)
        self.assertEqual(cli['data']['report']['summary']['ci_status'],'passed')
        self.assertTrue((export/'summary.json').is_file())
        self.assertTrue((export/'junit.xml').is_file())
        self.assertTrue((export/'result.json').is_file())
        self.assertTrue((export/'exact-json'/'route-0.json').is_file())
        self.assertEqual(self.count('wf_runs'),1)
        self.assertEqual(self.count('wf_budget_reservations'),1)
        self.assertEqual(self.count('wf_goal_requests'),1)

    def test_missing_fields_timeout_and_conflict_have_precise_state(self):
        missing=self.service.call('run_goal',{'goal':{'operation':'view_recorded_states',
            'profile':'robomimic.can-paired.v1','parameters':{}},'idempotency_key':'missing','wait_seconds':0})
        self.assertEqual(missing['data']['status'],'needs_information')
        self.assertEqual(missing['data']['missing_parameters'][0]['path'],'goal.parameters.episode_id')
        self.assertEqual(self.count('wf_runs'),0)
        waiting=self.service.call('run_goal',REQUEST)
        self.assertEqual(waiting['data']['status'],'waiting')
        self.assertTrue(waiting['data']['run_id'])
        changed={**REQUEST,'goal':{**GOAL,'parameters':{**GOAL['parameters'],'case_indices':[1]}}}
        conflict=self.service.call('run_goal',changed)
        self.assertEqual(conflict['error']['code'],'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.count('wf_runs'),1)
        self.assertEqual(self.count('wf_budget_reservations'),1)

    def test_no_json_cli_presets_name_enabled_profiles(self):
        for workflow,field in (('recorded-state','goal.parameters.episode_id'),
                               ('sensor-recording','goal.parameters.asset_id')):
            with self.subTest(workflow=workflow):
                process=subprocess.run([sys.executable,'-m','worldfuzz','platform','--root',
                    str(self.base/(workflow+'-results')),'--store',str(self.base/(workflow+'.sqlite3')),
                    'run',workflow,'--key','missing-'+workflow,'--wait-seconds','0'],
                    cwd=ROOT,capture_output=True,text=True,timeout=15)
                response=json.loads(process.stdout)
                self.assertEqual(process.returncode,0,process.stderr+'\n'+process.stdout)
                self.assertEqual(response['data']['status'],'needs_information')
                self.assertEqual(response['data']['missing_parameters'][0]['path'],field)

    def test_direct_composition_cannot_bypass_scoped_dispatcher(self):
        authority=IdentityStore(self.base/'authority.sqlite3')
        authority.provision('org','limited','secret-password-1','limited',self.base/'limited-results',
                            self.base/'limited.sqlite3',['run_goal'])
        token=authority.login('org','limited','secret-password-1','limited',['run_goal'])['token']
        limited=authority.session(token,launch=False)
        with self.assertRaises(Fault) as raised:
            limited.run_goal(**REQUEST)
        self.assertEqual(raised.exception.code,'ACCESS_DENIED')
        with limited.corpus.connect() as db:
            for table in ('wf_goal_requests','wf_plans','wf_runs','wf_budget_reservations'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0,table)


if __name__=='__main__':unittest.main()
