"""Lifetime workspace quotas over the existing atomic BudgetReservation ledger."""
import json
from ..corpus import encoded
from .contracts import Fault

DEFAULT={'max_jobs':1000,'cpu_seconds':3600,'wall_seconds':7200,'artifact_bytes':536870912,
         'active_memory_bytes':2147483648,'model_calls':0,'device_calls':0}
RESOURCES=('cpu_seconds','wall_seconds','artifact_bytes','model_calls','device_calls')


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS wf_workspace_quota(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,body TEXT NOT NULL)')
    db.execute('INSERT OR IGNORE INTO wf_workspace_quota VALUES(1,1,?)',(encoded(DEFAULT),))


def totals(db,exclude=None):
    committed={k:0 for k in DEFAULT};measured={k:0 for k in RESOURCES};holds={k:0 for k in RESOURCES}
    unknown=[];reservations=[]
    for row in db.execute('SELECT * FROM wf_budget_reservations ORDER BY run_id'):
        key=row['run_id']
        if key==exclude:continue
        body=json.loads(row['body']);limits=body['limits'];committed['max_jobs']+=1
        usage={r['resource']:r['amount'] for r in db.execute('SELECT resource,SUM(value) AS amount FROM wf_usage WHERE run_id=? GROUP BY resource',(key,))}
        attempts=[dict(r) for r in db.execute('SELECT * FROM wf_budget_attempts WHERE run_id=?',(key,))]
        active=body['state'] in ('reserved','consuming','reconciling')
        lost=any(a['unknown_tail'] for a in attempts)
        committed['active_memory_bytes']+=limits['memory_bytes'] if body['state'] in ('reserved','consuming') else 0
        for resource in RESOURCES:
            observation=usage.get(resource,0);measured[resource]+=observation
            if active:charge=max(limits[resource],observation)
            elif resource in ('cpu_seconds','wall_seconds'):
                field='charge_cpu' if resource=='cpu_seconds' else 'charge_wall'
                charge=max(observation,sum(a[field] for a in attempts))
            elif lost:charge=max(limits[resource],observation)
            else:charge=observation
            committed[resource]+=charge
            if lost:holds[resource]+=max(0,charge-observation)
        for attempt in attempts:
            unknown.append({'run_id':key,'attempt_id':attempt['attempt_id'],'resource':'memory_bytes',
                            'reason':'Peak memory not sampled; runner ceiling is not an observation'})
            if attempt['unknown_tail'] or not attempt['closed']:
                for resource in RESOURCES:
                    if resource in ('model_calls','device_calls'):continue # Offline runner cannot issue these calls.
                    unknown.append({'run_id':key,'attempt_id':attempt['attempt_id'],'resource':resource,
                                    'reason':'Unobserved tail after last committed sample'})
        reservations.append({'run_id':key,'state':body['state'],'limits':limits,
                             'estimated_usage':body['estimated_usage'],'estimate_status':'unknown' if not body['estimated_usage'] else 'provided'})
    return {'committed':committed,'measured':measured,'conservative_unknown_holds':holds,
            'unknown':unknown,'reservations':reservations}


def read(service):
    with service.corpus.connect() as db:
        db.execute('BEGIN')
        row=db.execute('SELECT version,body FROM wf_workspace_quota WHERE id=1').fetchone()
        report=totals(db);limits=json.loads(row['body'])
    return {'workspace_id':service.authorization.workspace_id,'version':row['version'],'limits':limits,
            'available':{key:max(0,value-report['committed'][key]) for key,value in limits.items()},**report,
            'cost_usd':None,'scope':'Lifetime job/phase quotas and current memory reservations; limits are not estimates, samples are not billing'}


def update(service,limits,expected_version):
    with service.corpus.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT version,body FROM wf_workspace_quota WHERE id=1').fetchone()
        if row['version']!=expected_version:raise Fault('QUOTA_VERSION_CONFLICT','Read the current quota version')
        used=totals(db)['committed']
        if any(used[key]>limits[key] for key in limits):raise Fault('QUOTA_BELOW_COMMITTED','Quota is below an existing reservation or charge')
        if json.loads(row['body'])!=limits:
            db.execute('UPDATE wf_workspace_quota SET version=version+1,body=? WHERE id=1',(encoded(limits),))
    return read(service)


def admit(db,limits,existing_run=None):
    """Caller already holds the same writer transaction as job/reservation creation."""
    quota=json.loads(db.execute('SELECT body FROM wf_workspace_quota WHERE id=1').fetchone()[0])
    committed=totals(db,exclude=existing_run)['committed']
    requested={**{key:limits[key] for key in RESOURCES},'max_jobs':1,'active_memory_bytes':limits['memory_bytes']}
    exceeded=[key for key in quota if committed[key]+requested[key]>quota[key]+1e-9]
    if exceeded:raise Fault('QUOTA_EXCEEDED','Workspace quota would be exceeded: '+', '.join(exceeded),next_action='get_workspace_usage')
