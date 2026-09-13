import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from worldfuzz.platform.contracts import OPERATIONS
from worldfuzz.platform.http import make_server
from worldfuzz.platform.identity import IdentityStore,OrganizationGateway
from worldfuzz.platform.sdk import Client
from test_wp25_organizations import PLAN


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.authority=IdentityStore(self.base/'authority.sqlite3');self.password=secrets.token_urlsafe(24)
        for ws in ('alpha','beta'):
            self.authority.provision('quota','owner',self.password,ws,self.base/ws/'results',self.base/ws/'catalog.sqlite3',list(OPERATIONS))
        self.token=self.authority.login('quota','owner',self.password,'alpha',list(OPERATIONS))['token']
        self.service=self.authority.session(self.token,launch=False)

    def tearDown(self):
        for child in self.service.worker_processes:child.wait(timeout=40)
        self.temp.cleanup()

    def ok(self,response):
        self.assertEqual(response['status'],'ok',response);return response['data']

    def quota(self,**changes):
        current=self.ok(self.service.call('get_workspace_usage'))
        return self.ok(self.service.call('set_workspace_quota',{'limits':{**current['limits'],**changes},'expected_version':current['version']}))

    def four(self,operation,args,token=None):
        token=token or self.token
        server=make_server(OrganizationGateway(self.authority,launch=False));thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}';spec=OPERATIONS[operation];body=dict(args);route=spec['route']
            for key in list(body):
                if '{'+key+'}' in route:route=route.replace('{'+key+'}',str(body.pop(key)))
            req=Request(base+route,data=json.dumps(body).encode() if spec['method']=='POST' else None,method=spec['method'],
                        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            try:
                with urlopen(req) as response:http=json.load(response)
            except HTTPError as exc:http=json.load(exc)
            sdk=Client(base,token).call(operation,args)
        finally:server.shutdown();server.server_close();thread.join()
        env={**os.environ,'WORLDFUZZ_SESSION_TOKEN':token};command=[sys.executable,'-m','worldfuzz','platform','--authority',str(self.authority.path)]
        p=subprocess.run(command+['call',operation,'--json',json.dumps(args)],capture_output=True,text=True,env=env,timeout=15)
        cli=json.loads(p.stdout)
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':operation,'arguments':args}}]
        p=subprocess.run(command+['mcp'],input=''.join(json.dumps(m)+'\n' for m in messages),capture_output=True,text=True,env=env,timeout=15)
        mcp=json.loads(p.stdout.splitlines()[-1])['result']['structuredContent']
        return [http,sdk,cli,mcp]

    def test_actual_run_quota_replay_denials_and_reading_across_four_interfaces(self):
        self.quota(max_jobs=1,cpu_seconds=20,wall_seconds=30)
        self.service.launch=True
        plan=self.ok(self.service.call('prepare_plan',PLAN));args={'plan_id':plan['plan_id'],'plan_digest':plan['plan_digest'],'idempotency_key':'single-job'}
        run=self.ok(self.service.call('execute_plan',args))['run_id']
        for child in self.service.worker_processes:child.wait(timeout=40)
        result=self.ok(self.service.call('get_result',{'result_id':run}));self.assertEqual(result['result']['execution_status'],'completed')
        for response in self.four('execute_plan',args):self.assertEqual(self.ok(response)['run_id'],run)
        for response in self.four('execute_plan',{**args,'idempotency_key':'second-job'}):self.assertEqual(response['error']['code'],'QUOTA_EXCEEDED')
        reports=[self.ok(x) for x in self.four('get_workspace_usage',{})]
        for report in reports:
            self.assertEqual(report,reports[0]);self.assertEqual(report['committed']['max_jobs'],1)
            self.assertGreater(report['measured']['cpu_seconds'],0);self.assertIsNone(report['cost_usd'])
            self.assertEqual(report['reservations'][0]['estimate_status'],'unknown')
        for response in self.four('get_service_metrics',{}):self.assertEqual(self.ok(response)['workspace_id'],'alpha')
        restricted=self.authority.login('quota','owner',self.password,'alpha',['get_service_metrics'])['token']
        for response in self.four('set_workspace_quota',{'limits':reports[0]['limits'],'expected_version':reports[0]['version']},restricted):
            self.assertEqual(response['error']['code'],'ACCESS_DENIED')
        other=self.authority.login('quota','owner',self.password,'beta',['get_workspace_usage'])['token']
        self.assertEqual(self.ok(self.authority.session(other).call('get_workspace_usage'))['committed']['max_jobs'],0)
        with self.service.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_budget_reservations').fetchone()[0],1)
        if os.environ.get('WORLDFUZZ_CHECK_RECEIPT'):
            Path(os.environ['WORLDFUZZ_CHECK_RECEIPT']).write_text(json.dumps({'run_id':run,'usage':reports[0],
                'worker_pids':[x.pid for x in self.service.worker_processes],'exit_codes':[x.returncode for x in self.service.worker_processes],
                'interfaces':['HTTP','SDK','CLI','MCP stdio'],'additional_reservations':0},indent=2))

    def test_unknown_tail_and_atomic_rollback_remain_separate_from_measurement(self):
        self.quota(cpu_seconds=25,wall_seconds=60)
        p=self.ok(self.service.call('prepare_plan',PLAN));run=self.ok(self.service.call('execute_plan',{
            'plan_id':p['plan_id'],'plan_digest':p['plan_digest'],'idempotency_key':'unknown'}))['run_id']
        self.service.jobs.claim(run)
        self.service.budgets.sample(run,'search',{'cpu_seconds':1.5,'wall_seconds':1.0})
        with self.service.corpus.connect() as db:db.execute('UPDATE wf_attempts SET lease_until=0 WHERE run_id=?',(run,))
        self.ok(self.service.call('reconcile_run',{'run_id':run}))
        usage=self.ok(self.service.call('get_workspace_usage'))
        self.assertEqual(usage['measured']['cpu_seconds'],1.5)
        self.assertEqual(usage['conservative_unknown_holds']['cpu_seconds'],18.5)
        self.assertEqual(usage['available']['cpu_seconds'],5)
        self.assertTrue(any(x['resource']=='cpu_seconds' for x in usage['unknown']))
        plan=self.ok(self.service.call('prepare_plan',{**PLAN,'resource_limits':{**PLAN['resource_limits'],'cpu_seconds':6}}))
        failed=self.service.call('execute_plan',{'plan_id':plan['plan_id'],'plan_digest':plan['plan_digest'],'idempotency_key':'unfunded'})
        self.assertEqual(failed['error']['code'],'QUOTA_EXCEEDED')
        with self.service.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_budget_reservations').fetchone()[0],1)
        low=self.service.call('set_workspace_quota',{'limits':{**usage['limits'],'cpu_seconds':19},'expected_version':usage['version']})
        self.assertEqual(low['error']['code'],'QUOTA_BELOW_COMMITTED')
        self.assertEqual(self.ok(self.service.call('get_workspace_usage'))['version'],usage['version'])


if __name__=='__main__':unittest.main()
