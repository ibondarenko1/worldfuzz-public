"""Optional competition recipe over installed WorldFuzz; not a cloud runner.

Default `prepare` is offline. `propose` requires an explicit authorization file
and never retries. `execute` is the later Serverless Job entry point, and calls
the existing scoped dispatcher/runner/verifier. No cloud creation occurs here.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time

from worldfuzz import __version__
from worldfuzz.nebius import NemotronPlanner, NebiusError
from worldfuzz.platform.contracts import Fault, obj, validate, strict_object
from worldfuzz.platform.identity import IdentityStore

VERSION = '0.4.0rc6'
MODEL = 'nvidia/Nemotron-3_5-Lightning'
RESOURCE = 'movingai:8'
KEY = 'physical-ai-nav-20260913-001'
SCOPES = ['find_resources','run_goal','compile_goal','prepare_plan','execute_plan',
          'get_run','get_result','get_result_report','get_artifact_json','cancel_run',
          'get_run_audit','get_workspace_usage']
GOAL = {'operation':'solve_navigation','profile':'movingai.octile.v1',
        'parameters':{'case_indices':[8],'max_cases':1},'max_seconds':30,
        'resource_limits':{'cpu_seconds':20,'memory_bytes':268435456,'artifact_bytes':1048576},
        'verification':{'mode':'metric_recompute','protocol':'movingai.octile.route-check.v1'}}
PROPOSAL_SCHEMA = obj({'algorithm':{'enum':['astar','dijkstra']},
                      'reason':{'enum':['goal_directed_search','uninformed_reference']}},['algorithm','reason'])


def encoded(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def sha(raw): return hashlib.sha256(raw).hexdigest()


def write(path,value):
    with Path(path).open('xb') as f: f.write(encoded(value))


def read(path):
    raw=Path(path).read_bytes()
    if len(raw)>65536:raise ValueError('Input exceeds 64 KiB')
    return json.loads(raw,object_pairs_hook=strict_object)


def call(service,operation,**arguments):
    response=service.call(operation,arguments)
    if response['status']!='ok':
        error=response['error'];raise Fault(error['code'],error['message'],next_action=error.get('next_action'))
    return response['data']


def workspace(root,launch):
    """New, isolated operator-owned workspace. Never adopts an existing corpus."""
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=False,mode=0o700)
    authority=IdentityStore(root/'authority.sqlite3');password=secrets.token_urlsafe(32)
    authority.provision('competition','operator',password,'navigation',root/'results',root/'catalog.sqlite3',SCOPES)
    token=authority.login('competition','operator',password,'navigation',SCOPES,ttl_seconds=300)['token']
    return authority,token,authority.session(token,launch=launch)


def resource(service):
    found=call(service,'find_resources',profile=GOAL['profile'],limit=100)
    selected=[item for item in found['matches'] if item['id']==RESOURCE]
    if len(selected)!=1 or selected[0]['case_index']!=8:raise ValueError('Pinned resource unavailable')
    return {k:selected[0][k] for k in ('id','case_index','case','source_ref','profile_ref','verification_scope')}


def request(selected):
    # Only public typed task facts, never arbitrary workspace text or credentials.
    messages=[{'role':'system','content':
        'Select a supported shortest-path solver for the supplied published octile grid task. '
        'Prefer low search effort for a single start-goal query while retaining optimality. '
        'astar uses an admissible octile heuristic; dijkstra is an uninformed reference. '
        'Choose algorithm and reason only. Do not change the task, budgets or verification. '
        'The model is a configuration assistant, not the route solver or verifier. Return JSON.'},
        {'role':'user','content':json.dumps({'task':selected,'fixed_goal':GOAL},sort_keys=True)}]
    # Byte admission is enforced. Provider input-token count is not inferred from
    # characters; allow for the entire documented context in the spending reserve.
    if len(encoded({'messages':messages,'schema':PROPOSAL_SCHEMA}))>8192:raise ValueError('Prompt byte budget exceeded')
    return messages


def prepare(root):
    authority,token,service=workspace(root,False)
    try:
        selected=resource(service);messages=request(selected)
        result={'recipe_version':'1.0.0','worldfuzz_version':VERSION,'model':MODEL,'resource':selected,
                'goal':GOAL,'messages':messages,'response_schema':PROPOSAL_SCHEMA,
                'max_completion_tokens':512,'request_count':1,'automatic_retries':0,
                'idempotency_key':KEY,'prompt_sha256':sha(encoded(messages)),
                'prompt_bytes':len(encoded({'messages':messages,'schema':PROPOSAL_SCHEMA})),
                'state':'prepared_not_executed','model_calls':0,'cloud_jobs':0}
        write(Path(root)/'request.json',result)
        return result
    finally:authority.revoke(token)


def validate_proposal(proposal):
    validate(proposal,PROPOSAL_SCHEMA)
    expected={'astar':'goal_directed_search','dijkstra':'uninformed_reference'}
    if proposal['reason']!=expected[proposal['algorithm']]:raise ValueError('Inconsistent solver reason')
    goal=deepcopy(GOAL);goal['parameters']['algorithm']=proposal['algorithm']
    return goal


def propose(prepared,authorization,out,planner=None):
    if (prepared['worldfuzz_version']!=VERSION or prepared['model']!=MODEL or prepared['goal']!=GOAL
            or prepared['messages']!=request(prepared['resource']) or prepared['response_schema']!=PROPOSAL_SCHEMA):
        raise ValueError('Prepared request changed')
    if (authorization.get('approved') is not True or authorization.get('request_sha256')!=sha(encoded(prepared))
            or authorization.get('max_requests')!=1 or authorization.get('max_completion_tokens')!=512
            or authorization.get('maximum_usd')!=1 or authorization.get('rates_and_project_confirmed') is not True):
        raise ValueError('Explicit matching allowance and verified project/rates required')
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    # Exclusive write BEFORE handing a request to transport. An uncertain request
    # cannot be repeated using this directory, even after process termination.
    write(out/'inference-attempt.json',{'request_sha256':sha(encoded(prepared)),
        'request_state':'possibly_sent','usage_state':'unknown','maximum_requests':1})
    try:
        planner=planner or NemotronPlanner(model=MODEL)
        proposal,provenance=planner._complete(prepared['messages'],PROPOSAL_SCHEMA,'worldfuzz_solver_selection',512)
        goal=validate_proposal(proposal)
        provenance['validation_state']='validated'
        result={'schema_version':'1.0.0','worldfuzz_version':VERSION,'model':MODEL,
                'resource':prepared['resource'],'goal':goal,'proposal':proposal,'provenance':provenance,
                'idempotency_key':KEY,'request_sha256':sha(encoded(prepared)),'cloud_jobs':0}
        write(out/'approved-input.json',result)
        return result
    except (NebiusError,Fault,ValueError) as exc:
        write(out/'inference-error.json',{'error_type':type(exc).__name__,'code':getattr(exc,'code','INVALID_PROPOSAL'),
            'provenance':getattr(exc,'provenance',{}),'automatic_retry':False})
        raise


def execute(value,root):
    if __version__!=VERSION or value.get('worldfuzz_version')!=VERSION:raise ValueError('Installed product version changed')
    if value.get('model')!=MODEL or value.get('idempotency_key')!=KEY:raise ValueError('Frozen identity changed')
    if value.get('goal')!=validate_proposal(value['proposal']):raise ValueError('Budget, operation or task substitution')
    provenance=value.get('provenance',{})
    if not (provenance.get('source')=='live_api' and provenance.get('model')==MODEL
            and provenance.get('completion_request_attempted') is True and provenance.get('usage_state')=='known'
            and provenance.get('validation_state')=='validated'):
        raise ValueError('A contract-only or historical fixture is not this live inference')
    authority,token,service=workspace(root,True);run_id=None;receipt={}
    try:
        if resource(service)!=value['resource']:raise ValueError('Published source/profile changed across placements')
        # The same public dispatcher used by HTTP/SDK/CLI/MCP checks current
        # scopes, source rights, compatibility and reservations for every step.
        result=call(service,'run_goal',goal=value['goal'],resource_id=RESOURCE,
                    idempotency_key=KEY,wait_seconds=30,include_artifacts=True)
        run_id=result['run_id']
        receipt={'execution_placement':'nebius_job_entrypoint_requires_external_job_receipt',
                 'input_sha256':sha(encoded(value)),'worldfuzz_version':__version__,'workflow':result,
                 'cloud_job_identity_verified':False,'physical_replay':False}
        if run_id:
            receipt['audit']=call(service,'get_run_audit',run_id=run_id)
            receipt['usage']=call(service,'get_workspace_usage')
        write(Path(root)/'result.json',receipt)
        # Logs are a bounded result transport, not proof of Nebius execution.
        # Retrieve using the known Job ID and pair with provider job metadata.
        raw=encoded(receipt)
        if len(raw)>1048576:raise ValueError('Result log budget exceeded')
        print('WORLDFUZZ_RESULT '+raw.decode().strip(),flush=True)
        print('WORLDFUZZ_RESULT_SHA256 '+sha(raw),flush=True)
        return receipt
    finally:
        if run_id:
            try:
                run=call(service,'get_run',run_id=run_id)
                if run['status'] not in ('completed','failed','cancelled','budget_exhausted','incomplete'):
                    call(service,'cancel_run',run_id=run_id)
            except Fault:pass
        # Only Popen handles launched by this service; never stop processes by name.
        for process in service.worker_processes:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        authority.revoke(token)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    pre=sub.add_parser('prepare');pre.add_argument('--workspace',type=Path,required=True)
    pro=sub.add_parser('propose');pro.add_argument('--request',type=Path,required=True)
    pro.add_argument('--authorization',type=Path,required=True);pro.add_argument('--out',type=Path,required=True)
    run=sub.add_parser('execute');run.add_argument('--input',type=Path,required=True);run.add_argument('--workspace',type=Path,required=True)
    a=p.parse_args()
    if a.command=='prepare':result=prepare(a.workspace);print(json.dumps({'state':result['state'],'prompt_bytes':result['prompt_bytes']}))
    elif a.command=='propose':propose(read(a.request),read(a.authorization),a.out)
    else:
        if sha(a.input.read_bytes())!=os.environ.get('WORLDFUZZ_APPROVED_INPUT_SHA256'):
            raise ValueError('Injected approval bytes differ from the pinned job request')
        signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
        execute(read(a.input),a.workspace)


if __name__=='__main__':main()
