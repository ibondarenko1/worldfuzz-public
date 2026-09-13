"""WP24 explicit verified-result discovery and immutable suite inclusion."""
from copy import deepcopy
import json

from ..corpus import digest, encoded
from ..entities import validate_entity
from .contracts import Fault


def _tables(service):
    with service.corpus.connect() as db:db.execute('''CREATE TABLE IF NOT EXISTS wf_suites(
        id TEXT PRIMARY KEY,body TEXT NOT NULL,sha256 TEXT NOT NULL)''')


def _evidence(plan,result):
    report=result['result']['verification'];metric=any(x['mode']=='metric_recompute' for x in report['performed_checks'])
    protocols=','.join(x['protocol'] for x in report['performed_checks'])
    value={'entity_type':'Evidence','schema_version':'1.0.0','evidence_id':report['verification_id'],
        'kind':'metric_recompute' if metric else 'integrity','scope':report['scope'],
        'input_hashes':[{'input_ref':deepcopy(plan['contract_plan']['task_ref']),
            'sha256':report['input_snapshot_sha256']}],
        'procedure_ref':{'id':'verifier.'+('metric_recompute' if metric else 'integrity'),'version':report['verifier_sha256']},
        'issuer':{'identity':'worldfuzz.local-verifier','kind':'local_verifier'},
        'measured_values':[],'tolerances':[],
        'limitations':['No physical evidence; scope is exactly the verifier report','protocols: '+protocols],
        'created_at':plan['created_at']}
    validate_entity(value,'Evidence');return value


def _episode_ids(service,plan):
    snapshot=plan['snapshot'];operation=plan['request']['operation']
    if operation in ('view_recorded_states','replay_can_actions'):
        value=snapshot.get('episode_id') or snapshot.get('episode',{}).get('id')
        return [value] if value else []
    if operation=='build_dataset':return [x['id'] for x in snapshot['episodes']]
    if operation=='solve_navigation':
        wanted=snapshot['cases'];matches=[]
        for row in service.corpus.listing()['episodes']:
            if row['domain']=='navigation':
                item=service._episode(row['id'],'navigation')
                if item['episode']['case'] in wanted:matches.append(row['id'])
        return sorted(matches)
    return []


def _dataset(service,result_id):
    value=service.get_dataset_generation(result_id)
    result=service.get_result(result_id);plan=service._plan(result['plan_id'])
    return value,result,plan


def _candidate(service,run_id,dataset=None,purpose='evaluation'):
    result=service.get_result(run_id);body=result['result']
    if body['execution_status']!='completed' or not body.get('verification'):
        raise Fault('RESULT_NOT_READY','Result is not completed and verified')
    plan=service._plan(result['plan_id']);service.authorization.authorize_operation(plan,'inspect')
    ids=_episode_ids(service,plan)
    if not ids:raise Fault('PROFILE_INCOMPATIBLE','Result has no reusable source episode identity')
    dataset_ref=None
    if dataset:
        generation,_,dataset_plan=dataset
        members={x['id'] for x in generation['dataset_manifest']['episode_refs']}
        if not set(ids)<=members:raise Fault('PROFILE_INCOMPATIBLE','Result cases are absent from the selected dataset')
        selected_versions={(x['id'],x['version']) for x in plan['contract_plan']['input_versions']}
        dataset_versions={(x['id'],x['version']) for x in dataset_plan['contract_plan']['input_versions']}
        if not selected_versions<=dataset_versions:
            raise Fault('PROFILE_INCOMPATIBLE','Result and dataset use different source versions')
        if purpose=='training':
            if not generation['split_manifest']['heldout_policy']['training_allowed']:
                raise Fault('HELD_OUT_PROTECTED','Dataset is not approved for training')
            assignments={x['group_id']:x['split'] for x in generation['split_manifest']['group_assignments']}
            listing={x['id']:x for x in service.corpus.listing()['episodes']}
            if any(assignments.get(listing[x]['group_id'])!='train' for x in ids):
                raise Fault('HELD_OUT_PROTECTED','A selected result references held-out data')
            data_right=next(x for x in generation['dataset_manifest']['rights'] if x['subject']=='data')
            if data_right['status']!='verified' or 'train' not in data_right['allowed_uses']:
                raise Fault('ACCESS_DENIED','Dataset source rights do not allow training')
        dataset_ref={'id':generation['dataset_manifest']['dataset_id'],'version':generation['dataset_manifest']['version']}
    evidence=_evidence(plan,result)
    return {'result_id':run_id,'result_sha256':result['result_sha256'],
        'operation':plan['request']['operation'],'profile':plan['request']['profile'],
        'task_ref':deepcopy(plan['contract_plan']['task_ref']),
        'run_ref':{'id':run_id,'version':result['result_sha256']},'evidence':evidence,
        'dataset_ref':dataset_ref,'episode_refs':[{'id':x,'version':'1.0.0'} for x in ids],
        'source_versions':deepcopy(plan['contract_plan']['input_versions']),
        'profile_versions':deepcopy(plan['contract_plan']['runner_requirements']['profile_refs']),
        'evidence_scope':evidence['scope'],'physics_replayed':body['verification']['physics_replayed']}


def find(service,profile,operation,dataset_result_id=None,purpose='evaluation',limit=25,cursor=0):
    dataset=_dataset(service,dataset_result_id) if dataset_result_id else None
    with service.corpus.connect() as db:
        rows=db.execute("SELECT id FROM wf_runs WHERE status='completed' ORDER BY updated DESC,id").fetchall()
    matches=[]
    for row in rows:
        try:
            candidate=_candidate(service,row['id'],dataset,purpose)
            if candidate['profile']==profile and candidate['operation']==operation:matches.append(candidate)
        except Fault as exc:
            if exc.code in ('ACCESS_DENIED','PROFILE_INCOMPATIBLE','RESULT_NOT_READY'):continue
            raise
    return {'matches':matches[cursor:cursor+limit],'next_cursor':cursor+limit if cursor+limit<len(matches) else None,
        'total':len(matches),'automatic_inclusion':False,
        'scope':'Authorized completed results with verifier evidence and exact dataset/source compatibility'}


def create_suite(service,name,purpose,selected_result_ids,dataset_result_id):
    _tables(service)
    if len(set(selected_result_ids))!=len(selected_result_ids):raise Fault('DUPLICATE_SELECTION','Suite result IDs must be unique')
    dataset=_dataset(service,dataset_result_id);generation,_,dataset_plan=dataset
    selected=[_candidate(service,x,dataset,purpose) for x in selected_result_ids]
    signatures={(x['operation'],x['profile']) for x in selected}
    if len(signatures)!=1:raise Fault('PROFILE_INCOMPATIBLE','Suite cases require one operation and profile')
    body={'schema_version':'1.0.0','name':name,'purpose':purpose,
        'suite_id':'pending','dataset_ref':{'id':generation['dataset_manifest']['dataset_id'],
            'version':generation['dataset_manifest']['version']},
        'dataset_result_ref':{'id':dataset_result_id,'version':service.get_result(dataset_result_id)['result_sha256']},
        'dataset_source_versions':deepcopy(dataset_plan['contract_plan']['input_versions']),
        'rights':deepcopy(generation['dataset_manifest']['rights']),
        'split_ref':deepcopy(generation['dataset_manifest']['split_ref']),
        'selected_cases':selected,'automatic_inclusion':False,
        'foreign_data_included':False,'heldout_training_included':False,
        'scope':'Explicit compatible verified results only; evidence scopes are preserved per case'}
    identifier=digest({**body,'suite_id':None});body['suite_id']=identifier
    with service.corpus.connect() as db:
        db.execute('INSERT OR IGNORE INTO wf_suites VALUES (?,?,?)',(identifier,encoded(body),digest(body)))
        row=db.execute('SELECT body,sha256 FROM wf_suites WHERE id=?',(identifier,)).fetchone()
    if row['body']!=encoded(body) or row['sha256']!=digest(body):raise Fault('INTEGRITY_FAILED','Suite identity conflict')
    return body


def get_suite(service,suite_id):
    _tables(service)
    with service.corpus.connect() as db:row=db.execute('SELECT body,sha256 FROM wf_suites WHERE id=?',(suite_id,)).fetchone()
    if not row:raise Fault('NOT_FOUND','Suite not found')
    body=json.loads(row['body'])
    if digest(body)!=row['sha256'] or body['suite_id']!=suite_id:raise Fault('INTEGRITY_FAILED','Suite changed')
    dataset=_dataset(service,body['dataset_result_ref']['id'])
    generation,dataset_result,dataset_plan=dataset
    if ({'id':generation['dataset_manifest']['dataset_id'],'version':generation['dataset_manifest']['version']}!=body['dataset_ref']
            or dataset_result['result_sha256']!=body['dataset_result_ref']['version']
            or dataset_plan['contract_plan']['input_versions']!=body['dataset_source_versions']
            or generation['dataset_manifest']['rights']!=body['rights']
            or generation['dataset_manifest']['split_ref']!=body['split_ref']):
        raise Fault('PLAN_CHANGED','Linked dataset version, rights or split changed')
    for case in body['selected_cases']:
        current=_candidate(service,case['result_id'],dataset,body['purpose'])
        if current['result_sha256']!=case['result_sha256'] or current['evidence']!=case['evidence']:
            raise Fault('PLAN_CHANGED','Selected result or evidence changed')
    return body
