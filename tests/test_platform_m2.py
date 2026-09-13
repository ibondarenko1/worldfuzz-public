"""M2 acceptance for catalog policy gates and transport parity."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from worldfuzz.m1_catalog import canonical
from worldfuzz.entities.compatibility import check_compatibility
from worldfuzz.platform.authorization import RESOURCE_LIMITS
from worldfuzz.platform.contracts import Fault
from worldfuzz.platform.http import make_server
from worldfuzz.platform.sdk import Client
from worldfuzz.platform.service import Service
from worldfuzz.platform.source_contracts import m1_contract
from worldfuzz.platform.worker import run


ROOT = Path(__file__).resolve().parents[1]
REQUEST = {'operation': 'solve_navigation', 'profile': 'movingai.octile.v1',
           'case_indices': [0], 'max_seconds': 30}


class M2PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root/'results', self.root/'corpus.sqlite3', launch=False)

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_contains_valid_catalog_contracts_and_outputs_are_enforced(self):
        saved_assets = json.loads((ROOT/'examples/m1-catalog-20260910/AssetVersion.json').read_text())
        saved_profiles = json.loads((ROOT/'examples/m1-catalog-20260910/EmbodimentProfile.json').read_text())
        for domain, offset in (('navigation', 0), ('manipulation', 1)):
            self.assertEqual(m1_contract(domain)['asset'], saved_assets[offset])
            self.assertEqual(m1_contract(domain)['profile'], saved_profiles[offset])
        prepared = self.service.prepare_plan(**REQUEST)
        self.assertEqual(prepared['compatibility_decision']['status'], 'compatible')
        self.assertEqual(prepared['run_plan']['digest'],
                         prepared['authorization'][0]['plan_digest'])
        self.assertEqual(prepared['run_plan']['input_versions'], [{
            'id': '902dcd0856dede0e7a7b003e3170d718dabc99ff4fae3eba51842e9837a69400',
            'version': 'pinned-original-selection-v1'}])
        self.assertEqual(prepared['run_plan']['runner_requirements']['profile_refs'], [{
            'id': 'worldfuzz.legacy.navigation', 'version': '1.0.0'}])
        self.assertNotEqual(prepared['run_plan']['input_versions'][0]['id'],
                            hashlib.sha256(canonical(prepared['plan']['snapshot']).encode()).hexdigest())
        with self.service.corpus.connect() as db:
            counts = dict(db.execute(
                'SELECT entity_type,COUNT(*) AS n FROM wf_contract_entities GROUP BY entity_type'))
        self.assertEqual(counts, {'AssetVersion': 1, 'CompatibilityDecision': 1,
                                  'EmbodimentProfile': 1, 'SourceFamily': 1})
        original = self.service.get_capabilities
        self.service.get_capabilities = lambda: {'profiles': []}
        try:
            result = self.service.call('get_capabilities')
            self.assertEqual(result['error']['code'], 'RESPONSE_INVALID')
        finally:
            self.service.get_capabilities = original

    def test_m1_unknowns_and_registered_declarations_are_preserved(self):
        run_plan, manipulation_decision, _ = self.service.authorization.contracts(
            {'operation': 'verify_episodes', 'profile': 'robomimic.can-paired.v1',
             'filters': {'domain': 'manipulation'}, 'max_seconds': 30},
            {'episodes': []}, self.service.clock()+3600)
        profile_ref = run_plan['runner_requirements']['profile_refs'][0]
        profile = self.service.authorization._resolve(profile_ref, 'EmbodimentProfile')
        self.assertEqual(profile['profile_id'], 'worldfuzz.legacy.manipulation')
        self.assertEqual(profile['unknown_fields'], [
            'controller configuration', 'simulator frame conventions',
            'physical calibration and replay state'])
        self.assertEqual(manipulation_decision['status'], 'compatible')
        self.assertIn('profile.unknown_fields.preserved_not_required',
                      manipulation_decision['matched_requirements'])
        source_contract = m1_contract('manipulation')
        replay_decision = check_compatibility(
            source_contract['profile'], copy.deepcopy(source_contract['profile']),
            [source_contract['asset']],
            required_rights={'data': 'evaluate', 'code': None, 'model': None, 'scene': None},
            required_resources=copy.deepcopy(RESOURCE_LIMITS),
            offered_resources=copy.deepcopy(RESOURCE_LIMITS), checked_at=0,
            required_profile_fields=['components', 'state_schema', 'action_schema', 'frames'],
            require_known_profile=True)
        self.assertEqual(replay_decision['status'], 'needs_information')
        self.assertIn('controller configuration', replay_decision['missing_fields'])

        asset_id = self.service.register_asset(
            name='declared trajectory', source_url='urn:test:declared',
            data_license='fixture owner declaration', code_license='MIT',
            frame='shared', units='m', prediction=[[0, 0, 0, 0], [1, 1, 0, 0]],
            reference=[[0, 0, 0, 0], [1, 1, 0, 0]])['asset_id']
        sensor = self.service.prepare_plan(
            operation='evaluate_trajectory', profile='sensor.trajectory.xyz.v1',
            asset_id=asset_id, threshold_m=1)
        source_ref = sensor['run_plan']['input_versions'][0]
        source = self.service.authorization._resolve(source_ref, 'AssetVersion')
        sensor_profile_ref = sensor['run_plan']['runner_requirements']['profile_refs'][0]
        sensor_profile = self.service.authorization._resolve(sensor_profile_ref, 'EmbodimentProfile')
        self.assertIsNone(sensor_profile['action_schema'])
        self.assertIn('physical platform identity', sensor_profile['unknown_fields'])
        right = next(value for value in source['rights'] if value['subject'] == 'data')
        self.assertEqual(right['status'], 'declared')
        self.assertEqual(right['allowed_uses'], ['inspect', 'evaluate'])
        self.assertNotIn('export', right['allowed_uses'])
        self.assertEqual(sensor['authorization'][0]['source_rights_statuses'],
                         {source_ref['id']+'@'+source_ref['version']: 'declared'})
        with self.service.corpus.connect() as db:
            grant = json.loads(db.execute('SELECT body FROM wf_policy_grants').fetchone()['body'])
        self.assertNotIn('rights', grant)
        self.assertEqual(grant['data_scopes'][-1]['asset_ref'], source_ref)

    def test_unknown_and_disallowed_source_rights_fail_during_preparation(self):
        for index, state in enumerate(('unknown', 'disallowed')):
            source = m1_contract('navigation')
            right = next(value for value in source['asset']['rights'] if value['subject'] == 'data')
            if state == 'unknown':
                right.update(status='unknown', license=None, allowed_uses=[], evidence_urls=[])
            else:
                right['allowed_uses'] = ['inspect']
            isolated = Service(self.root/f'{state}-results', self.root/f'{state}.sqlite3', launch=False)
            with patch('worldfuzz.platform.authorization.m1_contract', return_value=source):
                response = isolated.call('prepare_plan', REQUEST)
            self.assertEqual(response['error']['code'], 'ACCESS_DENIED', (index, response))
            with isolated.corpus.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0], 0)

    def test_expiry_changed_catalog_and_insufficient_rights_block_launch(self):
        now = [100.0]
        service = Service(self.root/'clock-results', self.root/'clock.sqlite3',
                          launch=False, clock=lambda: now[0])
        prepared = service.prepare_plan(**REQUEST)
        now[0] += 3600
        with self.assertRaisesRegex(Fault, 'Prepare a new plan') as expired:
            service.execute_plan(prepared['plan_id'], prepared['plan_digest'], 'expired')
        self.assertEqual(expired.exception.code, 'PLAN_EXPIRED')

        prepared = self.service.prepare_plan(**REQUEST)
        ref = prepared['run_plan']['input_versions'][0]
        with self.service.corpus.connect() as db:
            row = db.execute(
                "SELECT body FROM wf_contract_entities WHERE entity_type='AssetVersion' AND entity_id=? AND version=?",
                (ref['id'], ref['version'])).fetchone()
            asset = json.loads(row['body'])
            data_right = next(item for item in asset['rights'] if item['subject'] == 'data')
            data_right.update(status='unknown', license=None, allowed_uses=[], evidence_urls=[])
            body = canonical(asset)
            db.execute(
                "UPDATE wf_contract_entities SET body=?,sha256=? WHERE entity_type='AssetVersion' AND entity_id=? AND version=?",
                (body, hashlib.sha256(body.encode()).hexdigest(), ref['id'], ref['version']))
        with self.assertRaises(Fault) as changed:
            self.service.execute_plan(prepared['plan_id'], prepared['plan_digest'], 'changed')
        self.assertEqual(changed.exception.code, 'ACCESS_DENIED')

        profile_service = Service(self.root/'profile-results', self.root/'profile.sqlite3', launch=False)
        profile_plan = profile_service.prepare_plan(**REQUEST)
        profile_ref = profile_plan['run_plan']['runner_requirements']['profile_refs'][0]
        with profile_service.corpus.connect() as db:
            row = db.execute(
                "SELECT body FROM wf_contract_entities WHERE entity_type='EmbodimentProfile' AND entity_id=? AND version=?",
                (profile_ref['id'], profile_ref['version'])).fetchone()
            profile = json.loads(row['body'])
            profile['unknown_fields'] = ['injected unknown frame convention']
            body = canonical(profile)
            db.execute(
                "UPDATE wf_contract_entities SET body=?,sha256=? WHERE entity_type='EmbodimentProfile' AND entity_id=? AND version=?",
                (body, hashlib.sha256(body.encode()).hexdigest(), profile_ref['id'], profile_ref['version']))
        with self.assertRaises(Fault) as changed_profile:
            profile_service.execute_plan(
                profile_plan['plan_id'], profile_plan['plan_digest'], 'changed-profile')
        self.assertEqual(changed_profile.exception.code, 'ACCESS_DENIED')

        restricted = Service(self.root/'restricted-results', self.root/'restricted.sqlite3',
                             launch=False, allowed_actions=['inspect'])
        denied = restricted.call('prepare_plan', REQUEST)
        self.assertEqual(denied['error']['code'], 'ACCESS_DENIED')
        with restricted.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0], 0)

        scoped = Service(self.root/'scoped-results', self.root/'scoped.sqlite3', launch=False)
        scoped_plan = scoped.prepare_plan(**REQUEST)
        with scoped.corpus.connect() as db:
            grant=json.loads(db.execute('SELECT body FROM wf_policy_grants').fetchone()['body'])
        grant['data_scopes'][0]['actions']=['inspect']
        scoped.authorization.replace_grant(grant)
        with self.assertRaises(Fault) as denied_scope:
            scoped.execute_plan(scoped_plan['plan_id'],scoped_plan['plan_digest'],'scope')
        self.assertEqual(denied_scope.exception.code,'ACCESS_DENIED')

    def test_revocation_after_queue_is_rechecked_before_worker_side_effects(self):
        prepared = self.service.prepare_plan(**REQUEST)
        queued = self.service.execute_plan(prepared['plan_id'], prepared['plan_digest'], 'revoke')
        self.service.authorization.revoke()
        run(self.service, queued['run_id'])
        record = self.service._run_record(queued['run_id'], authorize=False)
        self.assertEqual(record['status'], 'failed')
        self.assertTrue(record['error'].startswith('ACCESS_DENIED'))
        self.assertEqual(self.service.corpus.listing()['episodes'], [])
        denied = self.service.call('get_run', {'run_id': queued['run_id']})
        self.assertEqual(denied['error']['code'], 'ACCESS_DENIED')


class M2TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root/'results', self.root/'corpus.sqlite3', launch=False)
        self.server = make_server(self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.sdk = Client(self.base, self.server.token)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def raw(self, operation, arguments=None):
        from worldfuzz.platform.contracts import OPERATIONS
        spec = OPERATIONS[operation]
        args = dict(arguments or {})
        route = spec['route']
        for key in list(args):
            if '{'+key+'}' in route:
                route = route.replace('{'+key+'}', str(args.pop(key)))
        body = json.dumps(args).encode() if spec['method'] == 'POST' else None
        request = Request(self.base+route, data=body, method=spec['method'],
                          headers={'Authorization': 'Bearer '+self.server.token,
                                   'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def cli(self, operation, arguments=None):
        command = [sys.executable, '-m', 'worldfuzz', 'platform', '--root',
                   str(self.root/'results'), '--store', str(self.root/'corpus.sqlite3'),
                   'call', operation, '--json', json.dumps(arguments or {})]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
        return result.returncode, json.loads(result.stdout)

    def mcp_stdio(self, operation, arguments=None):
        messages=[
            {'jsonrpc':'2.0','id':1,'method':'initialize',
             'params':{'protocolVersion':'2025-11-25','capabilities':{},
                       'clientInfo':{'name':'m2-acceptance','version':'1'}}},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','id':2,'method':'tools/call',
             'params':{'name':operation,'arguments':arguments or {}}},
        ]
        command=[sys.executable,'-m','worldfuzz','platform','--root',str(self.root/'results'),
                 '--store',str(self.root/'corpus.sqlite3'),'mcp']
        result=subprocess.run(command,cwd=ROOT,input='\n'.join(map(json.dumps,messages))+'\n',
                              text=True,capture_output=True,timeout=20)
        replies=[json.loads(line) for line in result.stdout.splitlines()]
        return result.returncode,replies[-1]['result']['structuredContent']

    def lifecycle(self, call, prefix):
        prepared = call('prepare_plan', REQUEST)['data']
        arguments = {'plan_id':prepared['plan_id'], 'plan_digest':prepared['plan_digest'],
                     'idempotency_key':prefix}
        first = call('execute_plan', arguments)['data']
        second = call('execute_plan', arguments)['data']
        self.assertEqual(first['run_id'], second['run_id'])
        run(self.service, first['run_id'])
        self.assertEqual(call('get_run', {'run_id':first['run_id']})['data']['status'], 'completed')
        self.assertEqual(call('get_result', {'result_id':first['run_id']})['data']['result']['execution_status'], 'completed')
        cancel_plan = call('prepare_plan', REQUEST)['data']
        cancel_run = call('execute_plan', {'plan_id':cancel_plan['plan_id'],
            'plan_digest':cancel_plan['plan_digest'],'idempotency_key':prefix+'-cancel'})['data']
        cancelled = call('cancel_run', {'run_id':cancel_run['run_id']})['data']
        self.assertEqual(cancelled['status'], 'cancel_requested')
        run(self.service, cancel_run['run_id'])
        self.assertEqual(call('get_run', {'run_id':cancel_run['run_id']})['data']['status'], 'cancelled')

    def test_http_sdk_mcp_and_cli_share_lifecycle_idempotency_and_denial(self):
        self.lifecycle(lambda op,args=None:self.raw(op,args)[1], 'raw-http')
        self.lifecycle(self.sdk.call, 'python-sdk')

        mcp_code, mcp_prepared=self.mcp_stdio('prepare_plan',REQUEST)
        self.assertEqual(mcp_code,0,mcp_prepared)
        mcp_data=mcp_prepared['data']
        mcp_args={'plan_id':mcp_data['plan_id'],'plan_digest':mcp_data['plan_digest'],
                  'idempotency_key':'mcp-stdio'}
        _,mcp_first=self.mcp_stdio('execute_plan',mcp_args)
        _,mcp_second=self.mcp_stdio('execute_plan',mcp_args)
        self.assertEqual(mcp_first['data']['run_id'],mcp_second['data']['run_id'])
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            _,mcp_status=self.mcp_stdio('get_run',{'run_id':mcp_first['data']['run_id']})
            if mcp_status['data']['status'] in ('completed','failed','incomplete'):break
            time.sleep(.05)
        self.assertEqual(mcp_status['data']['status'],'completed',mcp_status)
        self.assertEqual(self.mcp_stdio('get_result',{'result_id':mcp_first['data']['run_id']})[1]
                         ['data']['result']['execution_status'],'completed')
        mcp_queued=self.service.execute_plan(mcp_data['plan_id'],mcp_data['plan_digest'],'mcp-cancel')
        _,mcp_cancelled=self.mcp_stdio('cancel_run',{'run_id':mcp_queued['run_id']})
        self.assertEqual(mcp_cancelled['data']['status'],'cancel_requested')
        run(self.service,mcp_queued['run_id'])

        code, prepared = self.cli('prepare_plan', REQUEST)
        self.assertEqual(code, 0, prepared)
        data = prepared['data']
        args = {'plan_id':data['plan_id'],'plan_digest':data['plan_digest'],
                'idempotency_key':'cli'}
        first_code, first = self.cli('execute_plan', args)
        second_code, second = self.cli('execute_plan', args)
        self.assertEqual((first_code,second_code),(0,0))
        self.assertEqual(first['data']['run_id'],second['data']['run_id'])
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            _, status=self.cli('get_run',{'run_id':first['data']['run_id']})
            if status['data']['status'] in ('completed','failed','incomplete'):break
            time.sleep(.05)
        self.assertEqual(status['data']['status'],'completed',status)
        self.assertEqual(self.cli('get_result',{'result_id':first['data']['run_id']})[1]
                         ['data']['result']['execution_status'],'completed')
        queued = self.service.execute_plan(data['plan_id'],data['plan_digest'],'cli-cancel')
        cancel_code,cancelled=self.cli('cancel_run',{'run_id':queued['run_id']})
        self.assertEqual(cancel_code,0,cancelled)
        self.assertEqual(cancelled['data']['status'],'cancel_requested')

        self.service.authorization.revoke()
        direct = self.service.call('prepare_plan', REQUEST)
        http_status, raw = self.raw('prepare_plan', REQUEST)
        sdk = self.sdk.call('prepare_plan', REQUEST)
        _,mcp = self.mcp_stdio('prepare_plan', REQUEST)
        cli_code, cli = self.cli('prepare_plan', REQUEST)
        self.assertEqual(http_status,403)
        self.assertEqual(cli_code,1)
        self.assertEqual({value['error']['code'] for value in (direct,raw,sdk,mcp,cli)},
                         {'ACCESS_DENIED'})

    def test_revoked_access_denies_execute_read_result_and_cancel_on_every_transport(self):
        prepared = self.service.prepare_plan(**REQUEST)
        completed = self.service.execute_plan(
            prepared['plan_id'], prepared['plan_digest'], 'denial-completed')
        run(self.service, completed['run_id'])
        queued = self.service.execute_plan(
            prepared['plan_id'], prepared['plan_digest'], 'denial-queued')
        self.service.authorization.revoke()
        operations = [
            ('prepare_plan', REQUEST),
            ('execute_plan', {'plan_id': prepared['plan_id'], 'plan_digest': prepared['plan_digest'],
                              'idempotency_key': 'denial-new'}),
            ('get_run', {'run_id': completed['run_id']}),
            ('get_result', {'result_id': completed['run_id']}),
            ('cancel_run', {'run_id': queued['run_id']}),
        ]
        transports = {
            'http': lambda op, args: self.raw(op, args)[1],
            'sdk': lambda op, args: self.sdk.call(op, args),
            'mcp': lambda op, args: self.mcp_stdio(op, args)[1],
            'cli': lambda op, args: self.cli(op, args)[1],
        }
        for transport, call in transports.items():
            for operation, arguments in operations:
                with self.subTest(transport=transport, operation=operation):
                    response = call(operation, arguments)
                    self.assertEqual(response['error']['code'], 'ACCESS_DENIED', response)
        with self.service.corpus.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM wf_runs WHERE idem='denial-new'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
