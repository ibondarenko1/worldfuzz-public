"""One authored numerical example through an already running common HTTP service.

This is an SDK usage example, not another executor or an independent verifier.
Missing packages, missing profiles and unfinished runs are errors, never skips.
"""
import argparse
import hashlib
from importlib import metadata, resources
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from worldfuzz.platform.sdk import Client

from . import __version__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Loopback URL of the common platform server')
    parser.add_argument('--out', type=Path, required=True, help='New output directory')
    parser.add_argument('--key', required=True, help='Retain this key and request after any timeout')
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost') or url.username or url.password or url.path not in ('', '/'):
        parser.error('This local example requires an HTTP loopback origin')
    args.out.mkdir(parents=True, exist_ok=False)
    journal = []

    def save(name, value):
        (args.out / name).write_bytes((json.dumps(value, indent=2, allow_nan=False) + '\n').encode())

    def call(name, arguments=None):
        response = client.call(name, arguments)
        journal.append({'operation': name, 'arguments': arguments or {}, 'response': response})
        save('actions.json', journal)  # Never includes the authentication token.
        if response['status'] != 'ok':
            raise RuntimeError(name + ': ' + response['error']['code'] + ': ' + response['error']['message'])
        return response['data']

    # Personal workspace bootstrap is an existing loopback API. Organization
    # gateways require an operator-provided scoped token in the environment.
    token = os.environ.get('WORLDFUZZ_SESSION_TOKEN')
    if not token:
        with urlopen(args.url.rstrip('/') + '/bootstrap', timeout=5) as response:
            token = json.load(response)['token']
    client = Client(args.url, token, timeout=35)
    version = metadata.version('worldfuzz-example-trajectory-extent')
    if version != __version__:
        raise RuntimeError('Installed example distribution differs from the imported code')
    capabilities = call('get_capabilities')
    descriptor = next((d for d in capabilities['adapters'] if d['profile']['id'] == 'sensor.xyz.extent.v1'), None)
    if descriptor is None or descriptor['version'] != __version__:
        raise RuntimeError('Required pinned adapter is absent; acceptance is incomplete')
    call('find_resources', {'profile': 'sensor.xyz.extent.v1', 'limit': 1})
    sample = json.loads(resources.files('trajectory_extent').joinpath('sample.json').read_text())
    asset = call('register_asset', sample)
    goal = {'profile': 'sensor.xyz.extent.v1', 'operation': 'summarize_trajectory',
            'parameters': {'asset_id': asset['asset_id']}, 'max_seconds': 20,
            'resource_limits': {'cpu_seconds': 10, 'memory_bytes': 268435456, 'artifact_bytes': 1048576},
            'verification': {'mode': 'integrity'}}
    request = {'goal': goal, 'idempotency_key': args.key, 'wait_seconds': 20, 'include_artifacts': True}
    save('request.json', request)
    run = call('run_goal', request)
    save('run.json', run)
    if run['status'] != 'terminal':
        raise RuntimeError('Execution unfinished; retain request.json, the key and run ID, then read get_run. Do not create a new job.')
    if run['run']['status'] != 'completed':
        raise RuntimeError('Execution did not complete: ' + run['run']['status'])
    result = call('get_result', {'result_id': run['run_id']})
    body = result['result']; record, = body['records']
    assert record['min_xyz_m'] == [2, -3, 1] and record['max_xyz_m'] == [5, 6, 4], record
    assert record['samples'] == 2 and record['frame'] == sample['frame']
    assert record['control_outcome_verified'] is False
    verification = body['verification']
    assert verification['proven_result'] == 'not_evaluated' and verification['physics_replayed'] is False
    assert verification['performed_checks'] == [{'mode': 'integrity', 'protocol': 'sha256-artifacts-v1', 'status': 'passed'}]
    artifact, = body['artifacts']
    endpoint = args.url.rstrip('/') + '/v1/results/' + run['run_id'] + '/artifacts/' + artifact['name']
    with urlopen(Request(endpoint, headers={'Authorization': 'Bearer ' + token}), timeout=5) as response:
        raw = response.read()
    assert hashlib.sha256(raw).hexdigest() == artifact['sha256'] and json.loads(raw) == record
    (args.out / 'extent.json').write_bytes(raw)
    for operation, arguments, name in (
        ('get_result_report', {'result_id': run['run_id']}, 'report.json'),
        ('get_run_audit', {'run_id': run['run_id']}, 'audit.json'),
        ('get_service_metrics', {}, 'metrics.json'),
        ('get_workspace_usage', {}, 'usage.json')):
        save(name, call(operation, arguments))
    save('receipt.json', {'status': 'passed', 'worldfuzz_version': metadata.version('worldfuzz'),
        'adapter_version': version, 'asset_id': asset['asset_id'], 'run_id': run['run_id'],
        'runner': body['runner'], 'artifact_sha256': artifact['sha256'],
        'verification': verification, 'expected_numbers_match': True,
        'scope': 'WorldFuzz-authored numerical integration fixture; no external user, published robotics result or independent metric verifier'})
    print(json.dumps({'status': 'passed', 'run_id': run['run_id'], 'output': str(args.out)}))


if __name__ == '__main__':
    main()
