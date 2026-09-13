"""M3 recovery, verification and budget semantics through all public transports."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from worldfuzz.platform.contracts import OPERATIONS
from worldfuzz.platform.http import make_server
from worldfuzz.platform.mcp import handle
from worldfuzz.platform.sdk import Client
from worldfuzz.platform.service import Service
from worldfuzz.platform.worker import run

ROOT = Path(__file__).resolve().parents[1]
REQUEST = {'operation':'solve_navigation','profile':'movingai.octile.v1',
           'case_indices':[0],'max_seconds':15}


class M3TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root/'results', self.root/'corpus.sqlite3', launch=False)
        self.server = make_server(self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.sdk = Client(self.base, self.server.token)
        self.mcp_id = 0

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def raw(self, operation, arguments=None):
        spec = OPERATIONS[operation]
        args = dict(arguments or {})
        route = spec['route']
        for key in list(args):
            if '{'+key+'}' in route:
                route = route.replace('{'+key+'}', str(args.pop(key)))
        request = Request(self.base+route,
                          data=json.dumps(args).encode() if spec['method']=='POST' else None,
                          method=spec['method'], headers={'Authorization':'Bearer '+self.server.token,
                                                         'Content-Type':'application/json'})
        try:
            with urlopen(request, timeout=15) as response:return json.load(response)
        except HTTPError as exc:return json.load(exc)

    def cli(self, operation, arguments=None):
        result = subprocess.run([
            sys.executable, '-m', 'worldfuzz', 'platform', '--root', str(self.root/'results'),
            '--store', str(self.root/'corpus.sqlite3'), 'call', operation,
            '--json', json.dumps(arguments or {})], cwd=ROOT, capture_output=True, text=True, timeout=25)
        return json.loads(result.stdout)

    def mcp(self, operation, arguments=None):
        self.mcp_id += 1
        reply = handle(self.service, {'jsonrpc':'2.0','id':self.mcp_id,'method':'tools/call',
            'params':{'name':operation,'arguments':arguments or {}}})
        return reply['result']['structuredContent']

    def make_incomplete(self, prepare_call, key):
        prepared = prepare_call('prepare_plan', REQUEST)['data']
        run_id = self.service.execute_plan(
            prepared['plan_id'], prepared['plan_digest'], 'm3-'+key)['run_id']
        attempt = self.service.jobs.claim(run_id, launch_id='stopped-'+key)
        self.service.budgets.sample(run_id, 'search', {'cpu_seconds':.01, 'wall_seconds':.02})
        self.service.finish(run_id,'incomplete',{'records':[],'artifacts':[]},'METERED_INTERRUPTION')
        return run_id

    def wait(self, call, run_id):
        deadline = time.monotonic()+20
        while time.monotonic()<deadline:
            response = call('get_run', {'run_id':run_id})
            if response['status']=='error':return response
            if response['data']['status'] in ('completed','failed','cancelled','budget_exhausted','incomplete'):
                return response
            time.sleep(.05)
        self.fail('M3 transport worker did not reach a terminal state')

    def test_recovery_budget_and_verification_share_four_interfaces(self):
        transports = [('http', self.raw, False), ('sdk', self.sdk.call, False),
                      ('cli', self.cli, True), ('mcp', self.mcp, False)]
        completed = {}
        for name, call, auto_launch in transports:
            with self.subTest(transport=name):
                run_id = self.make_incomplete(call, name)
                reconciled = call('reconcile_run', {'run_id':run_id})
                self.assertEqual(reconciled['data']['status'], 'incomplete', reconciled)
                args = {'run_id':run_id, 'idempotency_key':'retry-'+name}
                resumed = call('resume_run', args)
                repeated = call('resume_run', args)
                self.assertEqual(resumed['data']['run_id'], repeated['data']['run_id'])
                if not auto_launch:
                    run(self.service, run_id)
                terminal = self.wait(call, run_id)
                self.assertEqual(terminal['data']['status'], 'completed', terminal)
                result = call('get_result', {'result_id':run_id})
                self.assertEqual(result['data']['result']['verification']['proven_result'],
                                 'no_violation_found')
                verified = call('verify_run_result', {'result_id':run_id, 'mode':'integrity'})
                self.assertEqual(verified['data']['proven_result'], 'not_evaluated', verified)
                budget = call('get_budget', {'run_id':run_id})
                self.assertEqual(budget['data']['reservation']['state'], 'released', budget)
                self.assertEqual(len(budget['data']['attempt_accounting']), 2)
                self.assertTrue(budget['data']['reservation']['unknown_usage'])
                completed[name] = run_id

        self.service.authorization.revoke()
        for name, call, _ in transports:
            for operation, arguments in (
                    ('get_budget', {'run_id':completed[name]}),
                    ('verify_run_result', {'result_id':completed[name], 'mode':'integrity'})):
                with self.subTest(transport=name, denied=operation):
                    response = call(operation, arguments)
                    self.assertEqual(response['error']['code'], 'ACCESS_DENIED', response)


if __name__ == '__main__':
    unittest.main()
