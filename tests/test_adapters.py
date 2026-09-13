"""Adapter conformance and installed-package lifecycle, no robot performance claims."""
import copy
from importlib import metadata
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from worldfuzz.platform.adapters import Registry, load_registry, check_descriptor
from worldfuzz.platform.adapters.builtin import builtins
from worldfuzz.platform.contracts import Fault
from worldfuzz.platform.service import Service
from worldfuzz.platform.http import make_server
from worldfuzz.platform.sdk import Client

try:
    metadata.version('worldfuzz-example-trajectory-extent')
    INSTALLED=True
except metadata.PackageNotFoundError:
    INSTALLED=False


class RegistryTests(unittest.TestCase):
    def test_duplicates_and_unimplemented_schema_rejected(self):
        adapter=builtins()[0]
        registry=Registry([adapter])
        with self.assertRaises(Fault):registry.add(adapter)
        class Unsupported:
            resources=adapter.resources
            snapshot=adapter.snapshot
            execute=adapter.execute
            def describe(self):
                d=adapter.describe()
                d['request_schemas']['solve_navigation']['oneOf']=[]
                return d
        with self.assertRaises(Fault):check_descriptor(Unsupported())

    def test_registry_freezes_and_rejects_unknown_profiles(self):
        with patch.dict(os.environ, {'WORLDFUZZ_ADAPTERS':''}):registry=load_registry()
        with self.assertRaises(Fault):registry.add(builtins()[0])
        with self.assertRaises(Fault):registry.resolve('sensor.unknown.v1','evaluate_trajectory')
        self.assertEqual({p['id'] for p in registry.profiles()}, {
            'movingai.octile.v1', 'robomimic.can-paired.v1',
            'sensor.trajectory.xyz.v1', 'sensor.mcap.aerial.v1',
            'pymunk.basic-fall.v7.2', 'robomimic.can-paired.action-replay.v1',
            'dataset.can.lerobot.v3', 'dataset.navigation.jsonl.v1'})

    def test_missing_pin_or_unknown_package_fails_closed(self):
        for selected in ('not-installed', 'not-installed==1.0.0'):
            with patch.dict(os.environ, {'WORLDFUZZ_ADAPTERS':selected}):
                with self.assertRaises(Fault):load_registry()

    def test_builtin_flat_requests_and_parameters_are_equivalent(self):
        with patch.dict(os.environ, {'WORLDFUZZ_ADAPTERS':''}):registry=load_registry()
        base={'profile':'movingai.octile.v1','operation':'solve_navigation'}
        self.assertEqual(registry.normalized({**base,'case_indices':[0]}),
                         registry.normalized({**base,'parameters':{'case_indices':[0]}}))
        with self.assertRaises(Fault):registry.normalized({**base,'parameters':{'case_indices':[0]},'case_indices':[0]})
        with self.assertRaises(Fault):registry.normalized({**base,'parameters':{'case_indices':[True]}})


@unittest.skipUnless(INSTALLED,'Install the local example adapter to test the real package lifecycle')
class InstalledAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ, {'WORLDFUZZ_ADAPTERS':'trajectory-extent==0.2.0'})
        self.env.start()
        self.service=Service(self.root/'results',self.root/'data.sqlite3')

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def test_external_profile_http_sdk_real_worker_and_restart(self):
        server=make_server(self.service,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=Client(f'http://127.0.0.1:{server.server_port}',server.token)
            capabilities=client.call('get_capabilities')['data']
            self.assertIn('sensor.xyz.extent.v1',[p['id'] for p in capabilities['profiles']])
            self.assertIn('request_schemas',capabilities['adapters'][-1])
            asset=client.call('register_asset',dict(name='authored adapter fixture',source_url='urn:test',
                data_license='fixture only',code_license='MIT',frame='shared',units='m',
                prediction=[[1,2,-3,4],[2,5,6,1]],reference=[[1,2,-3,4],[2,5,6,1]]))['data']['asset_id']
            arguments={'operation':'summarize_trajectory','profile':'sensor.xyz.extent.v1',
                       'parameters':{'asset_id':asset},'max_seconds':10}
            plan=client.call('prepare_plan',arguments)
            self.assertEqual(plan['status'],'ok',plan)
            data=plan['data'];self.assertIn('adapter_registry',data['plan']['code'])
            run_args={'plan_id':data['plan_id'],'plan_digest':data['plan_digest'],'idempotency_key':'external-adapter'}
            job=client.call('execute_plan',run_args)['data']['run_id']
            until=time.monotonic()+12
            while time.monotonic()<until:
                result=client.call('get_run',{'run_id':job})['data']
                if result['status'] in ('completed','failed','incomplete','budget_exhausted'):break
                time.sleep(0.03)
            self.assertEqual(result['status'],'completed',result)
            record=client.call('get_result',{'result_id':job})['data']['result']['records'][0]
            self.assertEqual(record['min_xyz_m'],[2,-3,1])
            self.assertEqual(record['max_xyz_m'],[5,6,4])
            self.assertFalse(record['control_outcome_verified'])
            restart=Service(self.root/'results',self.root/'data.sqlite3')
            self.assertEqual(restart.execute_plan(**run_args)['run_id'],job)
            invalid=copy.deepcopy(arguments);invalid['parameters']['unexpected']=1
            self.assertEqual(client.call('prepare_plan',invalid)['status'],'error')
            # Completed evidence remains readable with the adapter disabled.
            with patch.dict(os.environ,{'WORLDFUZZ_ADAPTERS':''}):
                reader=Service(self.root/'results',self.root/'data.sqlite3')
                self.assertEqual(reader.get_result(job)['result']['records'][0],record)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_version_and_enabled_set_are_pinned(self):
        with patch.dict(os.environ, {'WORLDFUZZ_ADAPTERS':'trajectory-extent==9.9.9'}):
            with self.assertRaises(Fault):load_registry()
        plan=self.service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',case_indices=[0])
        with patch.dict(os.environ,{'WORLDFUZZ_ADAPTERS':''}):
            changed=Service(self.root/'results',self.root/'data.sqlite3')
            response=changed.call('execute_plan',{'plan_id':plan['plan_id'],'plan_digest':plan['plan_digest'],'idempotency_key':'changed-set'})
            self.assertEqual(response['error']['code'],'PLAN_CHANGED')
