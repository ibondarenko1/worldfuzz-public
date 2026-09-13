"""Offline collection of reviewed external-agent evidence; never invokes inference.

File hashes establish identity, not authenticity or a robotics outcome. A reviewer
must inspect the original host trace and service evidence. This aggregator cannot
declare M7 accepted or turn scripted MCP checks into agent work.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


def checked_file(root, ref):
    if set(ref)!={'path','sha256'}:raise ValueError('Evidence needs path and sha256')
    root=Path(root).resolve();relative=Path(ref['path'])
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('Evidence must stay within its collection')
    path=(root/relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('Evidence file is unavailable')
    if path.stat().st_size>16*1024*1024:raise ValueError('Evidence file exceeds 16 MiB')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('Evidence hash changed')
    return ref['sha256']


def assess(manifest, records, root):
    manifest_hash=hashlib.sha256((json.dumps(manifest,indent=2)+'\n').encode()).hexdigest()
    tasks={t['id']:t for t in manifest['tasks']}
    if len(tasks)!=60 or Counter(t['category'] for t in tasks.values())!=Counter(supported=30,incompatible=15,failure_budget=15):
        raise ValueError('Expected frozen 30/15/15 task composition')
    seen=set();traces=set();sessions=set();supported=0;false_outcomes=0;reviewed=0;refusals=0;clients={};missing_cost=0;spent=0.;rows=[]
    for r in records:
        tid=r['task_id']
        if tid not in tasks or tid in seen:raise ValueError('Unknown or repeated task; keep retries outside primary scoring')
        seen.add(tid);task=tasks[tid]
        if r['manifest_sha256']!=manifest_hash:raise ValueError('Held-out manifest differs from the frozen attempt')
        if r['assigned_client']!=task['assigned_client']:raise ValueError('Client assignment changed')
        if type(r['attempt']) is not int or r['attempt']!=1:raise ValueError('Only the first held-out attempt is primary')
        if r['execution_kind']!='real_agent':raise ValueError('Scripted calls are not external-agent evidence')
        client=r['client']
        for key in ('family','version','model','inference_mode'):
            if not isinstance(client.get(key),str) or not client[key].strip():raise ValueError('Client/model identity required')
        prior=clients.setdefault(r['assigned_client'],client)
        if prior!=client:raise ValueError('Client/model changed within the frozen allocation')
        for key in ('host_trace','service_evidence'):
            fingerprint=checked_file(root,r[key])
            if key=='host_trace':
                if fingerprint in traces:raise ValueError('Host transcript reused for multiple held-out tasks')
                traces.add(fingerprint)
        if r['host_session_id'] in sessions:raise ValueError('Each task needs an independent fresh host session')
        sessions.add(r['host_session_id'])
        if not isinstance(r['operation_choices'],list) or not isinstance(r['errors'],list) or not isinstance(r['interventions'],list):
            raise ValueError('Record operation choices, errors and interventions')
        if not isinstance(r['execution'],dict) or not isinstance(r['final_response'],str):raise ValueError('Preserve execution and the final agent response')
        cost=r['cost']
        for key in ('usd','input_tokens','output_tokens','wall_seconds','worker_cpu_seconds'):
            value=cost[key]
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<0):raise ValueError('Invalid measured cost')
        if cost['usd'] is None:missing_cost+=1
        else:spent+=cost['usd']
        review=r['review']
        if type(review['workflow_completed']) is not bool or type(review['false_verified_outcome']) is not bool:raise ValueError('Explicit review verdicts required')
        if not isinstance(review['correct_refusal'],bool):raise ValueError('Explicit refusal assessment required')
        valid=review['status']=='reviewed' and bool(review['reviewer']) and review['host_trace_matches_service'] is True
        if valid:
            reviewed+=1;false_outcomes+=int(review['false_verified_outcome'])
            if task['category']=='supported' and review['workflow_completed'] and not r['interventions'] and not review['false_verified_outcome']:supported+=1
            if task['category']!='supported' and review['correct_refusal']:refusals+=1
        rows.append({'task_id':tid,'reviewed':bool(valid),'category':task['category'],'interventions':len(r['interventions'])})
    families={c['family'] for c in clients.values()}
    eligible=(reviewed==60 and len(families)>=3 and len(clients)==3 and supported>=27 and false_outcomes==0 and refusals==30)
    return {'status':'REVIEWED_TARGETS_MET' if eligible else 'INCOMPLETE_OR_TARGETS_NOT_MET',
        'observed_tasks':len(seen),'reviewed_tasks':reviewed,'missing_tasks':sorted(set(tasks)-seen),
        'supported_completed_without_intervention':supported,'supported_denominator':30,'supported_completion_rate':supported/30,
        'correct_incompatible_or_budget_refusals':refusals,'false_verified_outcomes':false_outcomes if reviewed else None,
        'independent_client_families_declared':sorted(families),'known_host_cost_usd':spent,'unknown_cost_tasks':missing_cost,
        'targets_met_from_reviewed_evidence':eligible,'milestone_accepted':False,'rows':rows,
        'scope':'Offline aggregate of reviewer-attested host/service traces. Does not authenticate host exports, verify physics, or accept M7.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--records',type=Path,required=True);p.add_argument('--evidence-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.records.stat().st_size>8*1024*1024:raise ValueError('Review records exceed 8 MiB')
    report=assess(json.loads(args.manifest.read_text()),json.loads(args.records.read_text()),args.evidence_root)
    args.out.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
