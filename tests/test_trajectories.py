"""Authored numerical fixtures test import semantics, not robot performance."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from worldfuzz.platform.service import Service
from worldfuzz.platform.http import make_server
from worldfuzz.platform.sdk import Client
from worldfuzz.platform.worker import run
from worldfuzz.platform.contracts import OPERATIONS, openapi


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root/'results', self.root/'db.sqlite3', launch=False)
        self.request = dict(name='authored unit fixture', source_url='urn:worldfuzz:test',
            data_license='test fixture', code_license='MIT', frame='shared-test-frame', units='m', format='tum.v1',
            prediction_text='# seconds metres xyzw\n1 1 0 0 0 0 0 1\n2 2 0 0 0 0 0 1\n',
            reference_text='1 0 0 0 0 0 0 1\n2 1 0 0 0 0 0 1\n')

    def tearDown(self):
        self.temp.cleanup()

    def test_recording_rejected_before_plan_job_and_reservation(self):
        from unittest.mock import patch
        recording = {'name': 'authored MCAP shape regression', 'import_provenance': {'adapter': 'mcap.ros.recording.v1'}}
        request = dict(operation='evaluate_trajectory', profile='sensor.trajectory.xyz.v1', asset_id='recording', threshold_m=1)
        with patch.object(self.service, '_asset', return_value=recording):
            result = self.service.call('prepare_plan', request)
        self.assertEqual(result['error']['code'], 'PROFILE_INCOMPATIBLE')
        self.assertIn('prediction/reference', result['error']['message'])
        self.assertEqual(result['error']['next_action'], 'register_trajectory')
        with self.service.corpus.connect() as db:
            for table in ('wf_plans', 'wf_runs', 'wf_budget_reservations'):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
        asset = self.service.register_trajectory(**self.request)['asset_id']
        request['asset_id'] = asset
        plan = self.service.prepare_plan(**request)
        with patch.object(self.service, '_asset', return_value=recording):
            result = self.service.call('execute_plan', dict(plan_id=plan['plan_id'], plan_digest=plan['plan_digest'], idempotency_key='changed'))
        self.assertEqual(result['error']['code'], 'PROFILE_INCOMPATIBLE')
        with self.service.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM wf_runs').fetchone()[0], 0)

    def test_two_registered_trajectory_frames_cannot_be_compared(self):
        jobs = []
        for frame in ('declared-enu', 'declared-ned'):
            request = {**self.request, 'frame': frame}
            asset = self.service.register_trajectory(**request)['asset_id']
            plan = self.service.prepare_plan(operation='evaluate_trajectory', profile='sensor.trajectory.xyz.v1', asset_id=asset, threshold_m=2)
            job = self.service.execute_plan(plan['plan_id'], plan['plan_digest'], frame)['run_id']
            run(self.service, job)
            self.assertEqual(self.service.get_result(job)['result']['execution_status'], 'completed')
            jobs.append(job)
        result = self.service.call('compare_results', dict(left_id=jobs[0], right_id=jobs[1]))
        self.assertEqual(result['error']['code'], 'INCOMPARABLE')
        # This is a regression fixture, not a replacement held-out success.

    def test_import_through_http_sdk_and_execute(self):
        server = make_server(self.service, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            client = Client(f'http://127.0.0.1:{server.server_port}', server.token)
            response = client.call('register_trajectory', self.request)
            self.assertEqual(response['status'], 'ok', response)
            asset_id = response['data']['asset_id']
            self.assertEqual(client.call('register_trajectory', self.request)['data']['asset_id'], asset_id)
            asset = client.call('get_asset', {'asset_id':asset_id})['data']['asset']
            self.assertEqual(asset['import_provenance']['prediction']['text'], self.request['prediction_text'])
            p = self.service.prepare_plan(operation='evaluate_trajectory', profile='sensor.trajectory.xyz.v1', asset_id=asset_id, threshold_m=0.5)
            job = self.service.execute_plan(p['plan_id'], p['plan_digest'], 'tum-test')['run_id']
            run(self.service, job)
            result = self.service.get_result(job)['result']
            self.assertEqual(result['execution_status'], 'completed')
            self.assertEqual(result['records'][0]['rmse_m'], 1.0)
            self.assertEqual(result['records'][0]['outcome'], 'fail')
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_cli_json_file_and_unreadable_file(self):
        import json
        import subprocess
        import sys
        path = self.root/'request.json'
        path.write_text(json.dumps(self.request))
        command = [sys.executable, '-m', 'worldfuzz', 'platform', '--root', str(self.root/'cli'),
                   '--store', str(self.root/'cli.sqlite3'), 'call', 'register_trajectory', '--json-file', str(path)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'ok')
        path.unlink()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['error']['code'], 'SCHEMA_INVALID')

    def test_invalid_inputs_never_create_asset(self):
        variants = ['1 nan 0 0 0 0 0 1\n2 0 0 0 0 0 0 1',
                    '1 0 0 0 0 0 0 0\n2 0 0 0 0 0 0 1',
                    '1 0 0 0 0 0 0 1\n1 0 0 0 0 0 0 1',
                    '1 0 0 0\n2 0 0 0',
                    '1 0 0 0 0 0 0 1\n3 0 0 0 0 0 0 1']
        for text in variants:
            request = copy.deepcopy(self.request); request['prediction_text'] = text
            self.assertEqual(self.service.call('register_trajectory', request)['status'], 'error')
        self.assertEqual(self.service.find_resources(profile='sensor.trajectory.xyz.v1')['total'], 0)

    def test_source_changes_change_identity_and_contract_is_discoverable(self):
        first = self.service.register_trajectory(**self.request)['asset_id']
        self.request['prediction_text'] += '# different provenance\n'
        second = self.service.register_trajectory(**self.request)['asset_id']
        self.assertNotEqual(first, second)
        self.assertEqual(openapi()['paths']['/v1/trajectory-assets']['post']['operationId'], 'register_trajectory')
        self.assertEqual(OPERATIONS['register_trajectory']['effect'], 'write')

    def test_timestamp_equivalence_uses_source_precision(self):
        # Numeric spellings may differ. Values lost by float rounding may not.
        request=copy.deepcopy(self.request)
        request['prediction_text']=request['prediction_text'].replace('\n1 1 ', '\n1.000000000000000001 1 ')
        response=self.service.call('register_trajectory',request)
        self.assertEqual(response['error']['code'],'PROFILE_INCOMPATIBLE')
        self.assertEqual(self.service.find_resources(profile='sensor.trajectory.xyz.v1')['total'],0)
        request['prediction_text']=request['prediction_text'].replace('1.000000000000000001 1 ','1.0e0 1 ')
        self.assertEqual(self.service.call('register_trajectory',request)['status'],'ok')
