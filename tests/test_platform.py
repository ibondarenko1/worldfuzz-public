"""Behavioral acceptance for persistent jobs, compatibility and public transports."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from worldfuzz.corpus import import_navigation
from worldfuzz.platform.service import Service
from worldfuzz.platform.worker import run
from worldfuzz.platform.contracts import Fault, OPERATIONS
from worldfuzz.platform.http import make_server
from worldfuzz.platform.sdk import Client
from worldfuzz.platform.mcp import handle, serve

ROOT=Path(__file__).resolve().parents[1]


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=Service(self.root/'results',self.root/'corpus.sqlite3',launch=False)

    def tearDown(self):self.temp.cleanup()

    def plan(self,**kw):
        return self.service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',case_indices=[0],max_seconds=30,**kw)

    def execute(self,p,key='test'):
        return self.service.execute_plan(p['plan_id'],p['plan_digest'],key)['run_id']

    def test_navigation_executes_original_case_and_rechecks_outcome(self):
        p=self.plan();identifier=self.execute(p);run(self.service,identifier)
        result=self.service.get_result(identifier)['result']
        self.assertEqual(result['execution_status'],'completed',result)
        self.assertEqual(result['records'][0]['outcome'],'success')
        self.assertTrue(result['records'][0]['check']['matches_published_optimum'])
        self.assertEqual(result['provider_calls'],0)
        payload=json.loads(self.service.artifact(identifier,'route-0.json'))
        self.assertEqual(payload['route'][0],payload['case']['start'])

    def test_idempotency_survives_restart_and_concurrent_requests(self):
        p=self.plan()
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(lambda _:self.execute(p),range(5)))
        self.assertEqual(len(set(results)),1)
        reopened=Service(self.root/'results',self.root/'corpus.sqlite3',launch=False)
        self.assertEqual(reopened.execute_plan(p['plan_id'],p['plan_digest'],'test')['run_id'],results[0])
        different=self.plan()
        with self.assertRaises(Fault):self.execute(different)

    def test_cancel_before_worker_never_executes(self):
        identifier=self.execute(self.plan());self.service.cancel_run(identifier);run(self.service,identifier)
        self.assertEqual(self.service.get_run(identifier)['status'],'cancelled')
        self.assertEqual(self.service.corpus.listing()['episodes'],[])

    def test_expired_worker_is_incomplete_not_success_and_not_retried(self):
        p=self.plan();identifier=self.execute(p)
        with self.service.corpus.connect() as db:db.execute('UPDATE wf_runs SET created=0 WHERE id=?',(identifier,))
        self.assertEqual(self.service.get_run(identifier)['status'],'incomplete')
        self.assertEqual(self.execute(p),identifier)

    def test_queued_time_does_not_fabricate_execution_usage(self):
        identifier=self.execute(self.plan())
        with self.service.corpus.connect() as db:db.execute('UPDATE wf_runs SET created=? WHERE id=?',(time.time()-31,identifier))
        run(self.service,identifier)
        self.assertEqual(self.service.get_run(identifier)['status'],'completed')
        report=self.service.get_budget(identifier)['reservation']
        observed=sum(x['value'] for x in report['actual_usage'] if x['resource']=='wall_seconds')
        self.assertLess(observed,30)

    def test_capacity_is_reserved_and_released_without_duplicate_jobs(self):
        p=self.plan();first=self.execute(p,'one')
        with self.assertRaises(Fault):self.execute(p,'two')
        self.assertEqual(self.execute(p,'one'),first)
        self.service.cancel_run(first);run(self.service,first)
        self.assertNotEqual(self.execute(p,'two'),first)

    def test_interrupted_worker_retains_checkpoint_evidence(self):
        identifier=self.execute(self.plan())
        self.service.jobs.claim(identifier)
        self.service.checkpoint(identifier,{'completed_units':1},{'records':[{'unit':'checked'}],'artifacts':[]})
        with self.service.corpus.connect() as db:db.execute('UPDATE wf_runs SET created=0 WHERE id=?',(identifier,))
        result=self.service.get_result(identifier)['result']
        self.assertEqual(result['execution_status'],'incomplete')
        self.assertEqual(result['records'],[{'unit':'checked'}])

    def test_input_code_and_digest_changes_rejected(self):
        p=self.plan()
        with self.assertRaises(Fault):self.service.execute_plan(p['plan_id'],'wrong','x')
        with self.service.corpus.connect() as db:
            body=json.loads(db.execute('SELECT body FROM wf_plans').fetchone()['body']);body['request']['max_seconds']=179
            db.execute('UPDATE wf_plans SET body=?',(json.dumps(body),))
        with self.assertRaises(Fault):self.execute(p)

    def test_capabilities_reject_control_and_silent_argument_changes(self):
        bad=self.service.call('prepare_plan',{'operation':'solve_navigation','profile':'sensor.trajectory.xyz.v1'})
        self.assertEqual(bad['error']['code'],'PROFILE_INCOMPATIBLE')
        bad=self.service.call('prepare_plan',{'operation':'solve_navigation','profile':'movingai.octile.v1','threshold_m':1})
        self.assertEqual(bad['error']['code'],'SCHEMA_INVALID')
        bad=self.service.call('prepare_plan',{'operation':'solve_navigation','profile':'movingai.octile.v1','max_seconds':True})
        self.assertEqual(bad['error']['code'],'SCHEMA_INVALID')

    def test_legacy_ids_payloads_and_splits_survive_service_and_export(self):
        import_navigation(self.service.corpus,ROOT/'examples/published-street-cases-20260910')
        before=self.service.corpus.listing()
        p=self.service.export_result('movingai.octile.v1',{'domain':'navigation'})
        identifier=self.execute(p);run(self.service,identifier)
        self.assertEqual(self.service.get_run(identifier)['status'],'completed')
        exported=[json.loads(line) for line in self.service.artifact(identifier,'episodes.jsonl').splitlines()]
        self.assertEqual(len(exported),9)
        self.assertEqual(before,self.service.corpus.listing())
        self.assertEqual(import_navigation(self.service.corpus,ROOT/'examples/published-street-cases-20260910')['added'],0)
        groups={}
        for item in exported:groups.setdefault(item['episode']['group_id'],set()).add(item['split'])
        self.assertTrue(all(len(splits)==1 for splits in groups.values()))

    def test_tampered_artifact_rejected(self):
        identifier=self.execute(self.plan());run(self.service,identifier)
        (self.root/'results'/identifier/'route-0.json').write_text('{}')
        with self.assertRaises(Fault):self.service.get_result(identifier)

    def test_sensor_metric_uses_explicit_frame_time_threshold_and_reference_origin(self):
        # Authored numerical unit fixture, not a published flight or product result.
        asset={'name':'unit fixture','source_url':'https://example.invalid/fixture','data_license':'test-only','code_license':'MIT','frame':'test','units':'m',
               'prediction':[[0,1,0,0],[1,2,0,0]],'reference':[[0,0,0,0],[1,1,0,0]]}
        identifier=self.service.register_asset(**asset)['asset_id']
        p=self.service.prepare_plan(operation='evaluate_trajectory',profile='sensor.trajectory.xyz.v1',asset_id=identifier,threshold_m=0.5)
        run_id=self.execute(p);run(self.service,run_id)
        record=self.service.get_result(run_id)['result']['records'][0]
        self.assertEqual(record['rmse_m'],1);self.assertEqual(record['outcome'],'fail')
        self.assertFalse(record['control_outcome_verified'])
        bad=copy.deepcopy(asset);bad['prediction'][1][0]=0
        with self.assertRaises(Fault):self.service.register_asset(**bad)

    def test_http_sdk_and_mcp_use_same_contracts_and_errors(self):
        server=make_server(self.service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=Client(f'http://127.0.0.1:{server.server_port}',server.token)
            http=client.call('get_capabilities')['data']
            mcp=handle(self.service,{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'get_capabilities'}})['result']['structuredContent']['data']
            self.assertEqual(http,mcp)
            self.assertEqual(Client(client.base_url,'invalid').call('get_capabilities')['error']['code'],'ACCESS_DENIED')
            p=client.call('prepare_plan',{'operation':'solve_navigation','profile':'movingai.octile.v1','case_indices':[0]})['data']
            job=client.call('execute_plan',{'plan_id':p['plan_id'],'plan_digest':p['plan_digest'],'idempotency_key':'http'})['data']
            run(self.service,job['run_id'])
            self.assertEqual(client.call('get_result',{'result_id':job['run_id']})['data']['result']['execution_status'],'completed')
        finally:server.shutdown();server.server_close();thread.join()

    def test_stdio_initialization_tools_output_and_readonly(self):
        stream=io.StringIO('\n'.join(json.dumps(m) for m in [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'prepare_plan','arguments':{}}}])+'\n')
        output=io.StringIO();serve(self.service,True,stream,output)
        replies=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(replies),3)
        self.assertEqual(replies[0]['result']['protocolVersion'],'2025-11-25')
        self.assertTrue(all(OPERATIONS[t['name']]['effect']=='read' for t in replies[1]['result']['tools']))
        self.assertTrue(replies[2]['result']['isError'])

    def test_real_worker_subprocess_reaches_terminal_result(self):
        self.service.launch=True;p=self.plan();identifier=self.execute(p)
        until=time.monotonic()+15
        while time.monotonic()<until:
            record=self.service.get_run(identifier)
            if record['status'] in ('completed','failed','incomplete'):break
            time.sleep(0.05)
        self.assertEqual(record['status'],'completed',record)

    def test_actual_mcp_entrypoint_exchanges_jsonrpc_over_stdio(self):
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'get_capabilities','arguments':{}}}]
        result=subprocess.run([sys.executable,'-m','worldfuzz','platform','--root',str(self.root/'results'),
                               '--store',str(self.root/'corpus.sqlite3'),'mcp'],
                              input='\n'.join(map(json.dumps,messages))+'\n',text=True,capture_output=True,timeout=10,cwd=ROOT)
        self.assertEqual(result.returncode,0,result.stderr)
        responses=[json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(responses[1]['result']['tools']),len(OPERATIONS))
        self.assertEqual(responses[2]['result']['structuredContent']['status'],'ok')


if __name__=='__main__':unittest.main()
