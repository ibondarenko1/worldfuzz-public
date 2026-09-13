"""Stored WP32 observations are regressions, never fresh held-out trials."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from worldfuzz.corpus import digest
from worldfuzz.platform.service import Service
from worldfuzz.platform.http import make_server
from worldfuzz.platform.sdk import Client
from worldfuzz.platform.mcp import handle
from worldfuzz.platform.contracts import envelope, validate, output_schema, openapi
from worldfuzz.platform.exact_json import project
from worldfuzz.platform.worker import run
from worldfuzz.corpus import import_navigation
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=Service(self.root/'results',self.root/'catalog.sqlite3',launch=False)

    def tearDown(self):self.temp.cleanup()

    def test_existing_nanoseconds_through_http_sdk_mcp_and_ui(self):
        review=json.loads((ROOT/'examples/wp32-real-agents-20260912/attempts/wp32-c2-supported-07/timestamp-review.json').read_text())
        values=[r['stored_log_time_ns'] for r in review['rows']]
        asset={'recording':{'messages':[{'log_time_ns':v,'publish_time_ns':v} for v in values],
                            'time_range_ns':{'first_log':values[0],'last_log':values[-1]}},'unknown_ns':None}
        asset_id=self.service._store_asset(asset)['asset_id']
        server=make_server(self.service,0);thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            sdk=Client(f'http://127.0.0.1:{server.server_port}',server.token)
            response=sdk.call('get_asset',{'asset_id':asset_id})
            mcp=handle(self.service,{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'get_asset','arguments':{'asset_id':asset_id}}})['result']['structuredContent']
            self.assertEqual(response['data'],mcp['data'])
            self.assertEqual(response['data_representation']['version'],'nanoseconds-decimal.v1')
            self.assertEqual([m['log_time_ns'] for m in response['data']['asset']['recording']['messages']],[str(v) for v in values])
            self.assertIsNone(response['data']['asset']['unknown_ns'])
            self.assertEqual(self.service._asset(asset_id),asset)
            self.assertEqual(digest(asset),asset_id)
            # The actual existing UI parser consumes the public contract in JS.
            script="import {parseResponseText} from './worldfuzz/ui/response-json.mjs'; let s=''; for await(const x of process.stdin)s+=x; console.log(JSON.stringify(parseResponseText(s).data));"
            done=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(response),text=True,capture_output=True,cwd=ROOT,timeout=10)
            self.assertEqual(done.returncode,0,done.stderr)
            self.assertEqual(json.loads(done.stdout),response['data'])
        finally:server.shutdown();server.server_close();thread.join(5)
        validate(str(values[0]),openapi()['components']['schemas']['ExactNanoseconds'])

    def test_old_artifact_view_preserves_original_bytes_and_hash(self):
        path=next((ROOT/'examples/wp32-real-agents-20260912/attempts/wp32-c1-supported-07/artifacts').rglob('sensor-recording-inventory.json'))
        raw=path.read_bytes();old=json.loads(raw)
        with patch.object(self.service,'get_result',return_value={'result_id':'fixture'}), patch.object(self.service,'artifact',return_value=raw):
            # The inner dispatcher must still validate the read, so mock its valid envelope only here.
            with patch.object(self.service,'invoke',return_value={}):view=self.service.get_artifact_json('fixture',path.name)
        self.assertEqual(path.read_bytes(),raw)
        self.assertEqual(view['content'],project(old))
        self.assertEqual(view['representation_sha256'],digest(view['content']))
        validate(envelope(view),output_schema('get_artifact_json'))

    def test_discovery_refuses_workflow_substitution_and_errors_are_actionable(self):
        caps=self.service.call('get_capabilities')['data']
        self.assertNotIn('evaluate_trajectory',caps['discovery']['service_tools'])
        self.assertIn('evaluate_trajectory',caps['discovery']['adapter_operations']['sensor.trajectory.xyz.v1'])
        schema=self.service.call('describe_operation',{'name':'compile_goal'})['data']['input_schema']
        self.assertIn('goal',schema['required'])
        wrong=self.service.call('compile_goal',{'goal':'do something'})
        self.assertEqual(wrong['error']['next_action'],'revise_request')
        self.assertIn('input_schema',wrong['error'])
        campaign=self.service.call('compile_goal',{'goal':{'workflow_kind':'campaign','operation':'solve_navigation'}})
        self.assertEqual(campaign['error']['code'],'WORKFLOW_INCOMPATIBLE')
        self.assertEqual(campaign['error']['next_action'],'create_campaign')
        unknown=self.service.call('compile_goal',{'goal':{'operation':'convert_unknown_controller'}})
        self.assertEqual(unknown['status'],'error')
        with self.service.corpus.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM wf_runs').fetchone()[0],0)

    def test_compiler_cannot_bypass_prepare_scope(self):
        import secrets
        from worldfuzz.platform.identity import IdentityStore
        authority=IdentityStore(self.root/'authority.sqlite3');password=secrets.token_urlsafe(24)
        authority.provision('org','owner',password,'restricted',self.root/'scoped-results',self.root/'scoped.sqlite3',['compile_goal'])
        token=authority.login('org','owner',password,'restricted',['compile_goal'])['token']
        service=authority.session(token,launch=False)
        response=service.call('compile_goal',{'goal':{'operation':'solve_navigation','profile':'movingai.octile.v1',
            'parameters':{'case_indices':[0]},'max_seconds':20}})
        self.assertEqual(response['error']['code'],'ACCESS_DENIED')
        with service.corpus.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM wf_plans').fetchone()[0],0)


class FreshFailureContractTests(unittest.TestCase):
    """Known-case diagnostics, never independent WP32 attempts or rescoring."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=Service(self.root/'results',self.root/'catalog.sqlite3',launch=False)

    def tearDown(self):self.temp.cleanup()

    def good(self, response):
        self.assertEqual(response['status'],'ok',response)
        return response['data']

    def mcp(self, name, arguments):
        return handle(self.service,{'jsonrpc':'2.0','id':1,'method':'tools/call',
            'params':{'name':name,'arguments':arguments}})['result']['structuredContent']

    def cli(self, name, arguments):
        args=self.root/'request.json';args.write_text(json.dumps(arguments))
        done=subprocess.run([sys.executable,'-m','worldfuzz','platform','--root',str(self.service.root),
            '--store',str(self.service.corpus.path),'call',name,'--json-file',str(args)],
            cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(done.returncode,0,done.stderr+'\n'+done.stdout)
        return json.loads(done.stdout)

    def test_zero_based_schema_discovery_and_stored_case_report(self):
        from worldfuzz.platform.adapters import check_schema
        from worldfuzz.platform.contracts import Fault
        check_schema({'type':'integer','description':'Zero-based example'})
        for invalid in ({'type':'integer','description':{'minimum':0}},
                        {'type':'integer','description':'x','unknown_constraint':True}):
            with self.assertRaises(Fault):check_schema(invalid)
        tools=handle(self.service,{'jsonrpc':'2.0','id':1,'method':'tools/list'})['result']['tools']
        goal=next(t for t in tools if t['name']=='run_goal')['inputSchema']['properties']['goal']
        self.assertIn('Zero-based',goal['properties']['parameters']['properties']['case_indices']['description'])
        found=self.good(self.mcp('find_resources',{'profile':'movingai.octile.v1'}))['matches']
        item=next(x for x in found if x['id']=='movingai:8')
        self.assertEqual(item['case_index'],8)
        self.assertEqual(item['prepare_request']['case_indices'],[8])
        mismatch=self.service.call('run_goal',{'goal':{'operation':'solve_navigation','profile':'movingai.octile.v1',
            'parameters':{'case_indices':[7]}},'resource_id':'movingai:8','idempotency_key':'mismatch','wait_seconds':0})
        self.assertEqual(mismatch['error']['code'],'WORKFLOW_INCOMPATIBLE')
        # Re-project the actual frozen case-7 result. Do not rerun navigation or
        # pretend the service received the original user's case-8 text.
        captured=json.loads((ROOT/'examples/wp32-fresh-20260912/attempts/t-e04dc5bf7545/service-evidence.json').read_text())['tables']
        row=captured['wf_runs'][0]
        plan=json.loads(next(p['body'] for p in captured['wf_plans'] if p['id']==row['plan_id']))
        checked={'plan_id':row['plan_id'],'result_sha256':row['result_hash'],'result':json.loads(row['result'])}
        with patch.object(self.service,'get_result',return_value=checked),patch.object(self.service,'_plan',return_value=plan):
            report=self.good(self.service.call('get_result_report',{'result_id':row['id']}))
        self.assertIn('resource=case_index (zero-based): movingai:7=7.',report['summary']['meaning'])
        self.assertNotIn('movingai:8',report['summary']['meaning'])
        props={p.attrib['name']:p.attrib['value'] for p in ET.fromstring(report['junit_xml']).findall('.//property')}
        self.assertEqual(props['resource_id'],'movingai:7');self.assertEqual(props['case_index'],'7')

    def test_export_plan_and_completed_artifacts_share_all_transports(self):
        # Existing tiny published route records in a disposable fixture catalog;
        # no source-corpus restoration, HDF5 read or robotics re-execution.
        import_navigation(self.service.corpus,ROOT/'examples/published-street-cases-20260910')
        before=self.service.corpus.listing()
        arguments={'profile':'movingai.octile.v1','filters':{'domain':'navigation'}}
        server=make_server(self.service);thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            client=Client(f'http://127.0.0.1:{server.server_port}',server.token)
            responses=[client.call('export_result',arguments),self.mcp('export_result',arguments),self.cli('export_result',arguments)]
            for response in responses:
                prepared=self.good(response)
                self.assertEqual(prepared['plan_id'],prepared['plan_digest'])
                state=prepared['plan']['preparation']
                self.assertEqual(state['stage'],'prepared');self.assertFalse(state['execution_started'])
                self.assertIsNone(state['run_id']);self.assertIsNone(state['run']);self.assertIsNone(state['result_id'])
                self.assertFalse(state['export_artifact_created'])
            with self.service.corpus.connect() as db:
                for table in ('wf_runs','wf_budget_reservations'):
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
            self.assertFalse(list(self.service.root.rglob('dataset-manifest.json')))
            goal={'operation':'export_dataset',**arguments,'parameters':{'filters':arguments['filters']},
                'max_seconds':20,'resource_limits':{'cpu_seconds':10,'memory_bytes':268435456,'artifact_bytes':8388608}}
            del goal['filters']
            request={'goal':goal,'idempotency_key':'diagnostic-export','wait_seconds':0}
            queued=self.good(self.service.call('run_goal',request))
            self.assertEqual(queued['status'],'waiting');self.assertIsNone(queued['report']);self.assertEqual(queued['artifacts'],[])
            run(self.service,queued['run_id'])
            done=self.good(self.mcp('run_goal',request))
            self.assertEqual(done['run_id'],queued['run_id']);self.assertEqual(done['run']['status'],'completed')
            report=done['report']
            for response in (client.call('get_result_report',{'result_id':queued['run_id']}),
                             self.cli('get_result_report',{'result_id':queued['run_id']})):
                self.assertEqual(self.good(response),report)
            self.assertTrue(report['summary']['meaning'].startswith('Dataset export confirmed:'))
            confirmation=ET.fromstring(report['junit_xml']).find(".//property[@name='dataset_export_confirmed']")
            self.assertEqual(confirmation.attrib['value'],'true')
            artifacts=done['result']['result']['artifacts']
            self.assertEqual({a['name'] for a in artifacts},{'episodes.jsonl','dataset-manifest.json'})
            self.assertEqual(len(self.service.artifact(queued['run_id'],'episodes.jsonl').splitlines()),9)
            self.assertEqual(before,self.service.corpus.listing())
            with self.service.corpus.connect() as db:
                for table in ('wf_runs','wf_budget_reservations'):
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],1)
            # Removing this disposable output must invalidate confirmation.
            artifact=artifacts[0]
            (self.service.root/queued['run_id']/artifact.get('storage_name',artifact['name'])).unlink()
            missing=self.service.call('get_result_report',{'result_id':queued['run_id']})
            self.assertEqual(missing['status'],'error');self.assertEqual(missing['data'],{})
        finally:server.shutdown();server.server_close();thread.join(5)

    def test_export_missing_payload_or_incomplete_execution_never_confirms(self):
        # Authored result-shape negatives; existing-artifact integrity is exercised
        # through real get_result in the transport test above.
        for state,names in [('completed',[]),('completed',['dataset-manifest.json']),
                            ('incomplete',['episodes.jsonl','dataset-manifest.json'])]:
            with self.subTest(state=state,names=names):
                checked={'plan_id':'p','result_sha256':'h','result':{'execution_status':state,
                    'artifacts':[{'name':name} for name in names],
                    'verification':{'proven_result':'not_evaluated','scope':'Authored fixture; not physical replay',
                        'physics_replayed':False,'performed_checks':[{'mode':'integrity','status':'passed','protocol':'fixture.v1'}]}}}
                plan={'request':{'operation':'export_dataset','profile':'movingai.octile.v1'},'verification_contract':{'mode':'integrity'}}
                with patch.object(self.service,'get_result',return_value=checked),patch.object(self.service,'_plan',return_value=plan):
                    report=self.good(self.service.call('get_result_report',{'result_id':'r'}))
                self.assertTrue(report['summary']['meaning'].startswith('Export is not confirmed:'))
                confirmation=ET.fromstring(report['junit_xml']).find(".//property[@name='dataset_export_confirmed']")
                self.assertEqual(confirmation.attrib['value'],'false')
                self.assertEqual(report['summary']['task_outcome'],'not_evaluated')
                self.assertEqual(report['summary']['ci_status'],'incomplete')

    def test_mcp_server_prefix_is_not_an_alias_or_permission_expansion(self):
        instructions=handle(self.service,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}})['result']['instructions']
        self.assertIn('ServerName',instructions);self.assertIn('ToolName',instructions)
        with patch.object(self.service,'call') as dispatched:
            response=self.mcp('worldfuzz-task/get_result_report',{'result_id':'r'})
            self.assertEqual(response['error']['code'],'UNSUPPORTED_OPERATION')
            self.assertEqual(response['error']['next_action'],'revise_request')
            dispatched.assert_not_called()
        described=self.service.call('describe_operation',{'name':'worldfuzz-task/get_result_report'})
        self.assertEqual(described['error']['next_action'],'revise_request')
        readonly=handle(self.service,{'jsonrpc':'2.0','id':1,'method':'tools/call',
            'params':{'name':'export_result','arguments':{'profile':'movingai.octile.v1','filters':{'domain':'navigation'}}}},readonly=True)
        self.assertEqual(readonly['result']['structuredContent']['error']['code'],'ACCESS_DENIED')
