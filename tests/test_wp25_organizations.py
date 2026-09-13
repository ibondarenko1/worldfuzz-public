import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from worldfuzz.platform.contracts import OPERATIONS, Fault
from worldfuzz.platform.identity import IdentityStore, OrganizationGateway
from worldfuzz.platform.http import make_server
from worldfuzz.platform.sdk import Client
from worldfuzz.platform.service import Service

PLAN={'operation':'solve_navigation','profile':'movingai.octile.v1','case_indices':[0],
      'max_cases':1,'max_seconds':30,'resource_limits':{'cpu_seconds':20,'memory_bytes':268435456,'artifact_bytes':1048576}}


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.authority=IdentityStore(self.base/'identity.sqlite3')
        self.password=secrets.token_urlsafe(24);self.services=[]
        for ws in ('alpha','beta'):
            self.authority.provision('org','owner',self.password,ws,self.base/ws/'results',self.base/ws/'catalog.sqlite3',list(OPERATIONS))
        self.a=self.login('alpha');self.b=self.login('beta')

    def login(self,ws,operations=None):
        return self.authority.login('org','owner',self.password,ws,operations or list(OPERATIONS))['token']

    def service(self,token,launch=False):
        value=self.authority.session(token,launch=launch);self.services.append(value);return value

    def tearDown(self):
        for service in self.services:
            for child in service.worker_processes:
                try:child.wait(timeout=40)
                except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
        self.temp.cleanup()

    def good(self,response):
        self.assertEqual(response['status'],'ok',response);return response['data']

    def test_workspace_worker_and_all_transports(self):
        a,b=self.service(self.a,True),self.service(self.b)
        p=self.good(a.call('prepare_plan',PLAN))
        request={'plan_id':p['plan_id'],'plan_digest':p['plan_digest'],'idempotency_key':'one-published-task'}
        run=self.good(a.call('execute_plan',request));run_id=run['run_id']
        for child in a.worker_processes:child.wait(timeout=40)
        result=self.good(a.call('get_result',{'result_id':run_id}))
        self.assertEqual(result['result']['execution_status'],'completed')
        self.assertEqual(self.good(a.call('execute_plan',request))['run_id'],run_id)
        self.assertTrue(a._plan(p['plan_id'])['actor']['workspace']=='alpha')
        for operation,args in [('get_result',{'result_id':run_id}),('get_run',{'run_id':run_id}),
                              ('get_dataset_generation',{'result_id':run_id}),('execute_plan',request)]:
            self.assertEqual(b.call(operation,args)['status'],'error')
        for table in ('wf_runs','episodes'):
            with b.corpus.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        webhook=self.good(a.call('create_webhook',{'name':'events'}))['webhook_id']
        self.assertTrue(self.good(a.call('read_webhook',{'webhook_id':webhook}))['events'])
        self.assertEqual(b.call('read_webhook',{'webhook_id':webhook})['error']['code'],'NOT_FOUND')
        from test_wp23_campaigns import request as campaign_request
        campaign=self.good(a.call('create_campaign',campaign_request()))['campaign_id']
        self.assertEqual(b.call('get_campaign',{'campaign_id':campaign})['error']['code'],'NOT_FOUND')
        episodes=[x['id'] for x in a.corpus.listing()['episodes']]
        prepared=self.good(a.call('prepare_plan',{'operation':'build_dataset','profile':'dataset.navigation.jsonl.v1',
            'parameters':{'episode_ids':episodes,'purpose':'evaluation','normalization_split':'train'},'max_seconds':30}))
        dataset=self.good(a.call('execute_plan',{'plan_id':prepared['plan_id'],'plan_digest':prepared['plan_digest'],'idempotency_key':'one-dataset'}))['run_id']
        for child in a.worker_processes:child.wait(timeout=40)
        self.good(a.call('get_dataset_generation',{'result_id':dataset}))
        self.assertEqual(b.call('get_dataset_generation',{'result_id':dataset})['error']['code'],'NOT_FOUND')
        suite=self.good(a.call('create_suite',{'name':'one-case','purpose':'evaluation','selected_result_ids':[run_id],'dataset_result_id':dataset}))['suite_id']
        self.assertEqual(b.call('get_suite',{'suite_id':suite})['error']['code'],'NOT_FOUND')
        self.assertEqual(self.good(b.call('find_verified_results',{'profile':'movingai.octile.v1','operation':'solve_navigation'}))['matches'],[])
        if os.environ.get('WORLDFUZZ_CHECK_RECEIPT'):
            Path(os.environ['WORLDFUZZ_CHECK_RECEIPT']).write_text(json.dumps({'workspace':'alpha','isolated_workspace':'beta',
                'run_id':run_id,'dataset_run_id':dataset,'suite_id':suite,'campaign_id':campaign,
                'result_sha256':result['result_sha256'],'worker_pids':[x.pid for x in a.worker_processes],
                'worker_exit_codes':[x.returncode for x in a.worker_processes]},indent=2))
        server=make_server(OrganizationGateway(self.authority,launch=False));thread=threading.Thread(target=server.serve_forever);thread.start()
        restricted=self.login('alpha',['get_capabilities'])
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            client=Client(base,self.b)
            self.assertEqual(client.call('get_result',{'result_id':run_id})['error']['code'],'NOT_FOUND')
            self.assertEqual(Client(base,self.a).call('get_result',{'result_id':run_id})['status'],'ok')
            with self.assertRaises(HTTPError):urlopen(base+'/bootstrap')
            # HTTP, SDK, actual CLI and MCP stdio all enforce the same scope.
            headers={'Authorization':'Bearer '+restricted,'Content-Type':'application/json'}
            # Obtain the route from the common contract to avoid an alias bypass.
            req=Request(base+OPERATIONS['find_resources']['route'],data=b'{}',headers=headers)
            try:
                with urlopen(req) as response:http=json.load(response)
            except HTTPError as exc:http=json.load(exc)
            sdk=Client(base,restricted).call('find_resources',{})
            env={**os.environ,'WORLDFUZZ_SESSION_TOKEN':restricted}
            command=[sys.executable,'-m','worldfuzz','platform','--authority',str(self.authority.path)]
            proc=subprocess.run(command+['call','find_resources','--json','{}'],capture_output=True,text=True,env=env,timeout=15)
            cli=json.loads(proc.stdout)
            messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
                      {'jsonrpc':'2.0','method':'notifications/initialized'},
                      {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'find_resources','arguments':{}}}]
            proc=subprocess.run(command+['mcp'],input=''.join(json.dumps(x)+'\n' for x in messages),capture_output=True,text=True,env=env,timeout=15)
            mcp=json.loads(proc.stdout.splitlines()[-1])['result']['structuredContent']
            for value in (http,sdk,cli,mcp):self.assertEqual(value['error']['code'],'ACCESS_DENIED',value)
            artifact=result['result']['artifacts'][0]['name']
            req=Request(base+f'/v1/results/{run_id}/artifacts/{artifact}',headers={'Authorization':'Bearer '+self.b})
            with self.assertRaises(HTTPError):urlopen(req)
            self.authority.revoke(self.a)
            self.assertEqual(Client(base,self.a).call('get_result',{'result_id':run_id})['error']['code'],'ACCESS_DENIED')
        finally:server.shutdown();server.server_close();thread.join()

    def test_membership_does_not_override_source_or_owner_grant(self):
        a=self.service(self.a)
        plan=self.good(a.call('prepare_plan',PLAN))
        a.authorization.revoke()
        self.assertEqual(a.call('execute_plan',{'plan_id':plan['plan_id'],'plan_digest':plan['plan_digest'],
            'idempotency_key':'revoked-owner'})['error']['code'],'ACCESS_DENIED')
        with self.assertRaises(Fault):Service(self.base/'alpha/results',self.base/'alpha/catalog.sqlite3')
        self.authority.remove_member('beta','owner')
        with self.assertRaises(Fault):self.authority.session(self.b)
        with self.assertRaises(Fault):self.authority.login('wrong-org','owner',self.password,'alpha',['get_capabilities'])

    def test_revocation_between_admission_and_worker(self):
        a=self.service(self.a)
        plan=self.good(a.call('prepare_plan',PLAN))
        run=self.good(a.call('execute_plan',{'plan_id':plan['plan_id'],'plan_digest':plan['plan_digest'],'idempotency_key':'revocation'}))
        self.authority.revoke(self.a)
        from worldfuzz.platform.runner import spawn
        child=spawn(a,run['run_id'],wait=True)
        self.assertNotEqual(child.returncode,0)
        with a.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_budget_attempts').fetchone()[0],0)
            self.assertIsNone(db.execute('SELECT result FROM wf_runs WHERE id=?',(run['run_id'],)).fetchone()[0])

    def test_migration_preserves_existing_records_and_login_contract(self):
        from worldfuzz.corpus import import_navigation
        legacy=Service(self.base/'legacy/results',self.base/'legacy/catalog.sqlite3',launch=False)
        import_navigation(legacy.corpus,Path(__file__).resolve().parents[1]/'examples/published-street-cases-20260910')
        with legacy.corpus.connect() as db:
            before={table:[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    for table in ('datasets','episodes','corpus_group_splits')}
        self.authority.provision('org','owner',self.password,'legacy',legacy.root,legacy.corpus.path,list(OPERATIONS))
        gateway=OrganizationGateway(self.authority,launch=False)
        logged=self.good(gateway.call('organization_login',{'organization':'org','principal':'owner','password':self.password,
            'workspace':'legacy','operations':['find_resources','get_capabilities']}))
        migrated=self.service(logged['token'])
        with migrated.corpus.connect() as db:
            after={table:[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in before}
        self.assertEqual(before,after)
        self.assertEqual(len(after['episodes']),9)
        self.good(migrated.call('find_resources',{}))
        self.assertEqual(migrated.call('prepare_plan',PLAN)['error']['code'],'ACCESS_DENIED')

    def test_equal_principal_names_in_distinct_organizations_do_not_share_access(self):
        self.authority.provision('other-org','owner',self.password,'gamma',self.base/'gamma/results',
                                 self.base/'gamma/catalog.sqlite3',list(OPERATIONS))
        with self.assertRaises(Fault):
            self.authority.login('org','owner',self.password,'gamma',list(OPERATIONS))
        token=self.authority.login('other-org','owner',self.password,'gamma',list(OPERATIONS))['token']
        a,c=self.service(self.a),self.service(token)
        plan=self.good(a.call('prepare_plan',PLAN))
        run=self.good(a.call('execute_plan',{'plan_id':plan['plan_id'],'plan_digest':plan['plan_digest'],
                                            'idempotency_key':'org-boundary'}))['run_id']
        for op,args in [('get_run',{'run_id':run}),('get_budget',{'run_id':run}),
                        ('execute_plan',{'plan_id':plan['plan_id'],'plan_digest':plan['plan_digest'],'idempotency_key':'other-org'})]:
            self.assertEqual(c.call(op,args)['error']['code'],'NOT_FOUND')
        self.assertEqual(c.identity.bound['org'],'other-org')
        self.assertEqual(a.identity.bound['org'],'org')
        with c.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_budget_reservations').fetchone()[0],0)


if __name__=='__main__':unittest.main()
