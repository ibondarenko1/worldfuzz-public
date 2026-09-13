import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest

from worldfuzz.platform.contracts import Fault, OPERATIONS
from worldfuzz.platform.identity import IdentityStore
from worldfuzz.platform.placement import connect
from test_wp25_organizations import PLAN


SERVER='''
import json,os,sys,threading
from pathlib import Path
from worldfuzz.platform.identity import IdentityStore,OrganizationGateway
from worldfuzz.platform.http import make_server
import worldfuzz.platform.service as module
server=make_server(OrganizationGateway(IdentityStore(sys.argv[1])))
thread=threading.Thread(target=server.serve_forever);thread.start()
Path(sys.argv[2]).write_text(json.dumps({'pid':os.getpid(),'port':server.server_port,'module':module.__file__}))
timer=threading.Timer(60,server.shutdown);timer.daemon=True;timer.start()
try:sys.stdin.readline()
finally:server.shutdown();server.server_close();thread.join();timer.cancel()
'''


class PlacementTests(unittest.TestCase):
    def test_remote_credentials_and_transport_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            config=Path(temp)/'remote.json'
            config.write_text(json.dumps({'version':'1.0.0','kind':'remote_http','endpoint':'http://example.invalid',
                'token_env':'WORLDFUZZ_ABSENT_TEST_TOKEN'}))
            with self.assertRaises(Fault):connect(config)
            config.write_text(json.dumps({'version':'1.0.0','kind':'local','root':temp+'/root','store':temp+'/store','endpoint':'http://127.0.0.1'}))
            with self.assertRaises(Fault):connect(config)

    def test_published_task_local_source_and_remote_installed_wheel(self):
        runtime=os.environ.get('WORLDFUZZ_WHEEL_PYTHON')
        if not runtime:self.skipTest('WP26 installed-wheel acceptance requires WORLDFUZZ_WHEEL_PYTHON; bounded receipt retained')
        runtime=str(Path(runtime).resolve())
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);password=secrets.token_urlsafe(24)
            authority=IdentityStore(base/'identity.sqlite3')
            for ws in ('source','wheel'):
                authority.provision('placements','owner',password,ws,base/ws/'results',base/ws/'catalog.sqlite3',list(OPERATIONS))
            a=authority.login('placements','owner',password,'source',list(OPERATIONS))['token']
            b=authority.login('placements','owner',password,'wheel',list(OPERATIONS))['token']
            receipts=[];local=authority.session(a)
            def run(client,key):
                def ok(response):
                    self.assertEqual(response['status'],'ok',response);return response['data']
                p=ok(client.call('prepare_plan',PLAN))
                run=ok(client.call('execute_plan',{'plan_id':p['plan_id'],'plan_digest':p['plan_digest'],'idempotency_key':key}))
                deadline=time.monotonic()+40
                while run['status'] not in ('completed','failed','incomplete','budget_exhausted','cancelled'):
                    self.assertLess(time.monotonic(),deadline)
                    time.sleep(.05);run=ok(client.call('get_run',{'run_id':run['run_id']}))
                self.assertEqual(run['status'],'completed',run)
                result=ok(client.call('get_result',{'result_id':run['run_id']}))
                verified=ok(client.call('verify_run_result',{'result_id':run['run_id'],'mode':'metric_recompute'}))
                receipts.append({'run_id':run['run_id'],'result_sha256':result['result_sha256'],
                    'environment':p['plan']['runner_contract']['environment'],'adapter':p['plan']['adapter'],
                    'source_versions':p['run_plan']['input_versions'],'verification':verified,
                    'worker_pid':result['result']['runner']['pid'],'records':result['result']['records']})
            try:run(local,'source')
            finally:
                for child in local.worker_processes:child.wait(timeout=40)
            env=dict(os.environ);env.pop('PYTHONPATH',None)
            ready=base/'ready.json'
            process=subprocess.Popen([runtime,'-I','-c',SERVER,str(authority.path),str(ready)],cwd=base,env=env,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                deadline=time.monotonic()+15
                while not ready.exists():
                    if process.poll() is not None:self.fail(process.stderr.read())
                    self.assertLess(time.monotonic(),deadline);time.sleep(.05)
                info=json.loads(ready.read_text());self.assertIn('site-packages',info['module'])
                config=base/'remote.json';config.write_text(json.dumps({'version':'1.0.0','kind':'remote_http',
                    'endpoint':f"http://127.0.0.1:{info['port']}",'token_env':'WORLDFUZZ_PLACEMENT_TEST_TOKEN'}))
                os.environ['WORLDFUZZ_PLACEMENT_TEST_TOKEN']=b
                try:remote=connect(config);run(remote,'wheel')
                finally:os.environ.pop('WORLDFUZZ_PLACEMENT_TEST_TOKEN',None)
                self.assertEqual(receipts[0]['records'],receipts[1]['records'])
                self.assertEqual(receipts[0]['source_versions'],receipts[1]['source_versions'])
                self.assertEqual(receipts[0]['adapter'],receipts[1]['adapter'])
                if os.environ.get('WORLDFUZZ_CHECK_RECEIPT'):
                    Path(os.environ['WORLDFUZZ_CHECK_RECEIPT']).write_text(json.dumps({'placements':receipts,'server':info,
                        'docker_executed':False,'cloud_executed':False},indent=2))
            finally:
                try:process.communicate('stop\n',timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.communicate(timeout=5)


if __name__=='__main__':unittest.main()
