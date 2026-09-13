"""Bounded integration checks; MCP stdio here is a transport test, not an agent."""
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from worldfuzz.platform.contracts import OPERATIONS, Fault, validate_output
from worldfuzz.platform.identity import IdentityStore, OrganizationGateway
from worldfuzz.platform.http import make_server
from worldfuzz.platform.sdk import Client
from worldfuzz.platform.event_delivery import EventInbox
from worldfuzz.platform.reporting import result_report


class Integrations(unittest.TestCase):
    def test_published_result_transports_local_receiver_and_revocation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'WORLDFUZZ_WEBHOOK_SIGNING_KEY':secrets.token_hex(32)}):
            base=Path(tmp);authority=IdentityStore(base/'identity.sqlite3');password=secrets.token_urlsafe(24)
            for ws in ('one','two'):
                authority.provision('org','owner',password,ws,base/ws/'results',base/ws/'catalog.sqlite3',list(OPERATIONS))
            token=authority.login('org','owner',password,'one',list(OPERATIONS))['token']
            token2=authority.login('org','owner',password,'two',list(OPERATIONS))['token']
            service=authority.session(token)
            server=make_server(OrganizationGateway(authority,launch=False));thread=threading.Thread(target=server.serve_forever);thread.start()
            receiver=None;receiver_thread=None
            def good(value):
                self.assertEqual(value['status'],'ok',value);return value['data']
            try:
                client=Client(f'http://127.0.0.1:{server.server_port}',token)
                plan=good(service.call('prepare_plan',{'operation':'solve_navigation','profile':'movingai.octile.v1',
                    'case_indices':[0],'max_cases':1,'max_seconds':30,
                    'resource_limits':{'cpu_seconds':15,'memory_bytes':268435456,'artifact_bytes':1048576}}))
                run=good(service.call('execute_plan',{'plan_id':plan['plan_id'],'plan_digest':plan['plan_digest'],'idempotency_key':'wp30-published'}))
                for child in service.worker_processes:child.wait(timeout=40)
                report=good(client.call('get_result_report',{'result_id':run['run_id']}))
                self.assertEqual(report['summary']['ci_status'],'passed');self.assertEqual(report['summary']['task_outcome'],'no_violation_found')
                placement=base/'placement.json';placement.write_text(json.dumps({'version':'1.0.0','kind':'remote_http',
                    'endpoint':client.base_url,'token_env':'WORLDFUZZ_TEST_TOKEN'}))
                env=os.environ|{'WORLDFUZZ_TEST_TOKEN':token}
                cli=subprocess.run([sys.executable,'-m','worldfuzz.platform.ci','--placement',str(placement),
                    '--result-id',run['run_id'],'--output',str(base/'ci')],env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(cli.returncode,0,cli.stdout+cli.stderr)
                self.assertEqual(json.loads((base/'ci/worldfuzz.json').read_text()),report['summary'])
                self.assertEqual(ET.parse(base/'ci/worldfuzz.junit.xml').getroot().attrib['errors'],'0')
                command=[sys.executable,'-m','worldfuzz','platform','--placement',str(placement),'mcp']
                messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
                    {'jsonrpc':'2.0','method':'notifications/initialized'},
                    {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'get_result_report','arguments':{'result_id':run['run_id']}}}]
                mcp=subprocess.run(command,input=''.join(json.dumps(v)+'\n' for v in messages),env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(mcp.returncode,0,mcp.stderr)
                self.assertEqual(good(json.loads(mcp.stdout.splitlines()[-1])['result']['structuredContent']),report)
                self.assertEqual(Client(client.base_url,token2).call('get_result_report',{'result_id':run['run_id']})['error']['code'],'NOT_FOUND')
                webhook=good(client.call('create_webhook',{'name':'local-receiver'}))['webhook_id']
                batch=good(client.call('read_signed_webhook',{'webhook_id':webhook}))
                envelope=batch['events'][0];inbox=EventInbox(base/'inbox.sqlite3',os.environ['WORLDFUZZ_WEBHOOK_SIGNING_KEY'].encode(),'one',webhook)
                def effect(db,payload):
                    db.execute('CREATE TABLE IF NOT EXISTS effects(id TEXT)')
                    db.execute('INSERT INTO effects VALUES(?)',(payload['event']['run_id'],))
                class Receiver(BaseHTTPRequestHandler):
                    def log_message(self,*args):pass
                    def do_POST(self):
                        length=int(self.headers['Content-Length'])
                        if not 0 < length <= 65536:self.send_error(413);return
                        result=inbox.receive(json.loads(self.rfile.read(length)),effect)
                        data=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                receiver=ThreadingHTTPServer(('127.0.0.1',0),Receiver);receiver_thread=threading.Thread(target=receiver.serve_forever);receiver_thread.start()
                answers=[]
                for _ in range(2):
                    with urlopen(Request(f'http://127.0.0.1:{receiver.server_port}/events',data=json.dumps(envelope).encode(),headers={'Content-Type':'application/json'}),timeout=5) as response:answers.append(json.load(response))
                self.assertEqual([x['duplicate'] for x in answers],[False,True])
                with closing(sqlite3.connect(inbox.path)) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM effects').fetchone()[0],1)
                changed=copy.deepcopy(envelope);changed['payload']['event']['status']='failed'
                with self.assertRaises(Fault) as invalid:inbox.receive(changed)
                self.assertEqual(invalid.exception.code,'EVENT_SIGNATURE_INVALID')
                foreign=EventInbox(base/'foreign.sqlite3',inbox.key,'two',webhook)
                with self.assertRaises(Fault) as denied:foreign.receive(envelope)
                self.assertEqual(denied.exception.code,'ACCESS_DENIED')
                expired=EventInbox(base/'expired.sqlite3',inbox.key,'one',webhook,clock=lambda:time.time()+600)
                with self.assertRaises(Fault) as stale:expired.receive(envelope)
                self.assertEqual(stale.exception.code,'EVENT_EXPIRED')
                rollback=EventInbox(base/'rollback.sqlite3',inbox.key,'one',webhook)
                def fail(db,payload):effect(db,payload);raise RuntimeError('local effect failed')
                with self.assertRaises(RuntimeError):rollback.receive(envelope,fail)
                self.assertTrue(rollback.receive(envelope,effect)['accepted'])
                authority.revoke(token)
                for op,args in [('get_result_report',{'result_id':run['run_id']}),('read_signed_webhook',{'webhook_id':webhook})]:
                    self.assertEqual(client.call(op,args)['error']['code'],'ACCESS_DENIED')
                revoked=subprocess.run([sys.executable,'-m','worldfuzz.platform.ci','--placement',str(placement),'--result-id',run['run_id'],'--output',str(base/'ci')],env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(revoked.returncode,1);self.assertFalse((base/'ci/worldfuzz.json').exists());self.assertFalse((base/'ci/worldfuzz.junit.xml').exists())
                if os.environ.get('WORLDFUZZ_CHECK_RECEIPT'):
                    Path(os.environ['WORLDFUZZ_CHECK_RECEIPT']).write_text(json.dumps({'report':report,'signed_events':len(batch['events']),
                        'receiver_deliveries':2,'receiver_effects':1,'cross_workspace_denied':True,'revoked_denied':True,
                        'mcp_scope':'scripted transport only','workers':[{'pid':x.pid,'exit_code':x.returncode} for x in service.worker_processes]},indent=2)+'\n')
            finally:
                if receiver:receiver.shutdown();receiver.server_close();receiver_thread.join()
                server.shutdown();server.server_close();thread.join()
                for child in service.worker_processes:
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=5)

    def test_scope_and_technical_failure_do_not_become_robotics_success(self):
        # Authored verifier receipts, not additional published executions.
        class Stored:
            state='completed';outcome='not_evaluated';checked=True;mode='integrity'
            def get_result(self,_):return {'plan_id':'p','result_sha256':'h','result':{'execution_status':self.state,
                'verification':{'proven_result':self.outcome,'scope':'Fixture scope','physics_replayed':False,
                    'performed_checks':[{'mode':self.mode,'status':'passed','protocol':'fixture.v1'}] if self.checked else []}}}
            def _plan(self,_):return {'request':{'profile':'fixture','operation':'inspect'},'verification_contract':{'mode':self.mode}}
        stored=Stored()
        value=result_report(stored,'r');validate_output('get_result_report',value)
        self.assertEqual(value['summary']['task_outcome'],'not_evaluated');self.assertEqual(value['summary']['exit_code'],0)
        stored.mode='metric_recompute';stored.outcome='violation_found'
        value=result_report(stored,'r');self.assertIsNotNone(ET.fromstring(value['junit_xml']).find('testcase/failure'))
        stored.state='failed'
        value=result_report(stored,'r');self.assertEqual(value['summary']['exit_code'],1);self.assertIsNotNone(ET.fromstring(value['junit_xml']).find('testcase/error'))
        stored.state='budget_exhausted'
        self.assertEqual(result_report(stored,'r')['summary']['exit_code'],3)
        stored.state='completed';stored.checked=False
        self.assertEqual(result_report(stored,'r')['summary']['exit_code'],3)
