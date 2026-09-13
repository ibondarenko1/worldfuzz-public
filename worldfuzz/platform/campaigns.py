"""WP23 bounded durable campaigns composed only from ordinary shared jobs."""
from copy import deepcopy
import json
import time

from ..corpus import digest, encoded
from .contracts import Fault


TERMINAL={'completed','failed','cancelled','budget_exhausted','incomplete'}


def _tables(service):
    with service.corpus.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS wf_campaigns(id TEXT PRIMARY KEY,body TEXT NOT NULL,sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS wf_campaign_requests(request_sha256 TEXT PRIMARY KEY,campaign_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS wf_campaign_runs(
          campaign_id TEXT NOT NULL,case_id TEXT NOT NULL,retry_key TEXT NOT NULL,run_id TEXT NOT NULL,
          PRIMARY KEY(campaign_id,retry_key),UNIQUE(campaign_id,run_id));
        CREATE TABLE IF NOT EXISTS wf_campaign_reports(
          campaign_id TEXT PRIMARY KEY,body TEXT NOT NULL,sha256 TEXT NOT NULL);
        ''')


def _load(service,campaign_id):
    _tables(service)
    with service.corpus.connect() as db:row=db.execute('SELECT body,sha256 FROM wf_campaigns WHERE id=?',(campaign_id,)).fetchone()
    if not row:raise Fault('NOT_FOUND','Campaign not found')
    body=json.loads(row['body'])
    if digest(body)!=row['sha256'] or digest(body)!=campaign_id:raise Fault('INTEGRITY_FAILED','Campaign definition changed')
    return body


def create(service,name,cases,allowed_mutations,immutable_criteria,budget,reduction='exact_typed_request'):
    _tables(service)
    request_sha=digest({'name':name,'cases':cases,'allowed_mutations':allowed_mutations,
        'immutable_criteria':immutable_criteria,'budget':budget,'reduction':reduction})
    with service.corpus.connect() as db:
        prior=db.execute('SELECT campaign_id FROM wf_campaign_requests WHERE request_sha256=?',(request_sha,)).fetchone()
    if prior:
        body=_load(service,prior['campaign_id'])
        return {'campaign_id':prior['campaign_id'],'campaign':body,'status':'prepared','attempts':[],
            'scope':'Frozen case specifications; no case has executed'}
    if len({x['case_id'] for x in cases})!=len(cases):raise Fault('SCHEMA_INVALID','Campaign case IDs must be unique')
    if len(set(allowed_mutations))!=len(allowed_mutations) or len(set(immutable_criteria))!=len(immutable_criteria):
        raise Fault('SCHEMA_INVALID','Campaign mutation and criterion names must be unique')
    if len(cases)>budget['max_cases'] or budget['max_attempts']<len(cases):
        raise Fault('BUDGET_EXCEEDED','Campaign cases exceed the frozen case/attempt budget')
    known=set();compiled=[]
    for case in cases:
        origin=case['origin'];parent=case.get('parent_case_id');mutations=case.get('mutations',[])
        if origin=='published' and (parent is not None or mutations):
            raise Fault('SCHEMA_INVALID','Published cases cannot have a derived parent or mutations')
        if origin=='derived':
            if parent not in known:raise Fault('SCHEMA_INVALID','Derived case parent must be an earlier campaign case')
            paths=[x['path'] for x in mutations]
            if not paths or len(paths)!=len(set(paths)) or not set(paths)<=set(allowed_mutations):
                raise Fault('SCHEMA_INVALID','Derived case uses an unapproved or duplicate mutation')
            if set(paths)&set(immutable_criteria):raise Fault('SCHEMA_INVALID','Derived case changes an immutable criterion')
        result=service.compile_goal(case['goal'],'disabled')
        if result['status']!='ready':
            raise Fault('GOAL_NEEDS_INFORMATION','Campaign goal lacks: '+', '.join(x['path'] for x in result['missing_parameters']))
        compiled.append({'case_id':case['case_id'],'origin':origin,'parent_case_id':parent,
            'mutations':deepcopy(mutations),'typed_request':result['typed_request'],
            'plan_id':result['prepared_plan']['plan_id'],'plan_digest':result['prepared_plan']['plan_digest']})
        known.add(case['case_id'])
    body={'schema_version':'1.0.0','name':name,'cases':compiled,
        'allowed_mutations':sorted(allowed_mutations),'immutable_criteria':sorted(immutable_criteria),
        'budget':deepcopy(budget),'reduction':reduction,'created_at':service.clock(),
        'source_cases_immutable':True,'derived_cases_are_published_benchmarks':False}
    identifier=digest(body)
    with service.corpus.connect() as db:
        db.execute('INSERT OR IGNORE INTO wf_campaigns VALUES (?,?,?)',(identifier,encoded(body),identifier))
        db.execute('INSERT OR IGNORE INTO wf_campaign_requests VALUES (?,?)',(request_sha,identifier))
        row=db.execute('SELECT body FROM wf_campaigns WHERE id=?',(identifier,)).fetchone()
        mapped=db.execute('SELECT campaign_id FROM wf_campaign_requests WHERE request_sha256=?',(request_sha,)).fetchone()
    if row['body']!=encoded(body) or mapped['campaign_id']!=identifier:raise Fault('INTEGRITY_FAILED','Campaign identity conflict')
    return {'campaign_id':identifier,'campaign':body,'status':'prepared','attempts':[],
        'scope':'Frozen case specifications; no case has executed'}


def _attempts(service,campaign_id):
    with service.corpus.connect() as db:
        rows=db.execute('SELECT case_id,retry_key,run_id FROM wf_campaign_runs WHERE campaign_id=? ORDER BY rowid',(campaign_id,)).fetchall()
    return [{'case_id':r['case_id'],'retry_key':r['retry_key'],'run':service.get_run(r['run_id'])} for r in rows]


def get(service,campaign_id):
    body=_load(service,campaign_id);attempts=_attempts(service,campaign_id)
    with service.corpus.connect() as db:report=db.execute('SELECT body,sha256 FROM wf_campaign_reports WHERE campaign_id=?',(campaign_id,)).fetchone()
    final=None
    if report:
        final=json.loads(report['body'])
        if digest(final)!=report['sha256']:raise Fault('INTEGRITY_FAILED','Campaign report changed')
    return {'campaign_id':campaign_id,'campaign':body,'status':'finalized' if final else 'running' if any(
        a['run']['status'] not in TERMINAL for a in attempts) else 'prepared' if not attempts else 'ready_to_finalize',
        'attempts':attempts,'report':final}


def _spent(service,attempts):
    cpu=wall=artifacts=0.0
    phases={};unknown=[]
    for attempt in attempts:
        budget=service.get_budget(attempt['run']['run_id'])
        for row in budget['reservation']['actual_usage']:
            phases.setdefault(row['phase'],{}).setdefault(row['resource'],0.0)
            phases[row['phase']][row['resource']]+=row['value']
            if row['resource']=='artifact_bytes':artifacts+=row['value']
        for row in budget['attempt_accounting']:
            cpu+=row['charge_cpu'];wall+=row['charge_wall']
        unknown.extend({'run_id':attempt['run']['run_id'],**x} for x in budget['reservation']['unknown_usage'])
    return {'cpu_seconds':cpu,'wall_seconds':wall,'artifact_bytes':artifacts,'phases':phases,'unknown_usage':unknown}


def launch_case(service,campaign_id,case_id,retry_key):
    campaign=_load(service,campaign_id);case=next((x for x in campaign['cases'] if x['case_id']==case_id),None)
    if case is None:raise Fault('NOT_FOUND','Campaign case not found')
    with service.corpus.connect() as db:finalized=db.execute('SELECT 1 FROM wf_campaign_reports WHERE campaign_id=?',(campaign_id,)).fetchone()
    if finalized:raise Fault('PLAN_CHANGED','Finalized campaign cannot accept another attempt')
    attempts=_attempts(service,campaign_id)
    prior=next((a for a in attempts if a['retry_key']==retry_key),None)
    if prior:return {'campaign_id':campaign_id,'case_id':case_id,'run':prior['run'],'idempotent_replay':True}
    if len(attempts)>=campaign['budget']['max_attempts']:raise Fault('BUDGET_EXCEEDED','Campaign attempt budget exhausted')
    if any(a['run']['status'] not in TERMINAL for a in attempts):raise Fault('RESOURCE_BUSY','Another campaign case is active')
    spent=_spent(service,attempts);plan=service._plan(case['plan_id']);limits=plan['contract_plan']['resource_limits']
    for resource in ('cpu_seconds','wall_seconds','artifact_bytes'):
        if spent[resource]+limits[resource]>campaign['budget'][resource]:
            raise Fault('BUDGET_EXCEEDED','Confirmed spend plus the next reservation exceeds campaign '+resource)
    idem='campaign:'+campaign_id[:16]+':'+retry_key
    with service.corpus.connect() as db:existing=db.execute('SELECT id FROM wf_runs WHERE idem=?',(idem,)).fetchone()
    run=service.get_run(existing['id']) if existing else service.execute_plan(case['plan_id'],case['plan_digest'],idem)
    with service.corpus.connect() as db:
        db.execute('INSERT OR IGNORE INTO wf_campaign_runs VALUES (?,?,?,?)',(campaign_id,case_id,retry_key,run['run_id']))
        saved=db.execute('SELECT case_id,run_id FROM wf_campaign_runs WHERE campaign_id=? AND retry_key=?',(campaign_id,retry_key)).fetchone()
    if saved['case_id']!=case_id or saved['run_id']!=run['run_id']:raise Fault('IDEMPOTENCY_CONFLICT','Retry key belongs to another campaign case')
    return {'campaign_id':campaign_id,'case_id':case_id,'run':run,'idempotent_replay':False}


def finalize(service,campaign_id):
    campaign=_load(service,campaign_id);attempts=_attempts(service,campaign_id)
    if not attempts or any(a['run']['status'] not in TERMINAL for a in attempts):
        raise Fault('RESULT_PENDING','All started campaign attempts must be terminal')
    latest={}
    for item in attempts:latest[item['case_id']]=item
    if set(latest)!={x['case_id'] for x in campaign['cases']}:
        raise Fault('RESULT_PENDING','Every campaign case requires a terminal attempt')
    selected=[];duplicates=[];seen={}
    for case in campaign['cases']:
        attempt=latest[case['case_id']];run=attempt['run'];key=digest(case['typed_request'])
        if key in seen:
            duplicates.append({'case_id':case['case_id'],'same_as':seen[key]});continue
        seen[key]=case['case_id']
        if run['status']=='completed':
            result=service.get_result(run['run_id'])
            if not result['result'].get('verification'):raise Fault('VERIFICATION_FAILED','Completed campaign case lacks verifier evidence')
            selected.append({'case_id':case['case_id'],'origin':case['origin'],'run_id':run['run_id'],
                'result_sha256':result['result_sha256'],'verification_id':result['result']['verification']['verification_id']})
    spend=_spent(service,attempts)
    for resource in ('cpu_seconds','wall_seconds','artifact_bytes'):
        if spend[resource]>campaign['budget'][resource]:raise Fault('BUDGET_EXCEEDED','Campaign actual charge exceeds budget')
    report={'schema_version':'1.0.0','campaign_id':campaign_id,'selected_cases':selected,'duplicates':duplicates,
        'attempts':[{'case_id':x['case_id'],'retry_key':x['retry_key'],'run_id':x['run']['run_id'],
            'status':x['run']['status'],'error':x['run']['error']} for x in attempts],
        'spend':spend,'reduction':campaign['reduction'],'immutable_criteria':campaign['immutable_criteria'],
        'allowed_mutations':campaign['allowed_mutations'],'derived_cases_are_published_benchmarks':False,
        'scope':'Existing runner results and independent verifier receipts; no benchmark source was modified'}
    with service.corpus.connect() as db:
        db.execute('INSERT OR IGNORE INTO wf_campaign_reports VALUES (?,?,?)',(campaign_id,encoded(report),digest(report)))
        prior=db.execute('SELECT body FROM wf_campaign_reports WHERE campaign_id=?',(campaign_id,)).fetchone()
    if prior['body']!=encoded(report):raise Fault('PLAN_CHANGED','Finalized campaign report is immutable')
    return report
