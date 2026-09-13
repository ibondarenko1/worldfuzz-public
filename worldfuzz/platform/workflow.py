"""Bounded composition of existing dispatched operations, not a new runner."""
import time
from copy import deepcopy
from ..corpus import digest
from .contracts import Fault


def run_goal(service, goal, idempotency_key, resource_id=None, wait_seconds=20, include_artifacts=False):
    required=['run_goal','compile_goal','prepare_plan','execute_plan','get_run','get_result','get_result_report']
    if resource_id:required.append('find_resources')
    if include_artifacts:required.append('get_artifact_json')
    # Also applies when called directly in trusted Python code.
    if service.identity:
        for tool in required:service.identity.check(tool)
    request_digest=digest({'goal':goal,'resource_id':resource_id})
    with service.corpus.connect() as db:
        prior=db.execute('SELECT * FROM wf_goal_requests WHERE request_key=?',(idempotency_key,)).fetchone()
    if prior and prior['request_digest']!=request_digest:
        raise Fault('IDEMPOTENCY_CONFLICT','Key belongs to a different goal or pinned resource')
    if prior:
        plan_id=prior['plan_id']
    else:
        resolved=deepcopy(goal)
        if resource_id:
            profile=resolved.get('profile')
            if not profile:
                return pending_information('goal.profile','Select the resource profile before discovery')
            matches=[];cursor=0
            while cursor is not None:
                found=service.invoke('find_resources',profile=profile,limit=100,cursor=cursor)
                matches.extend(x for x in found['matches'] if resource_id in (x['id'],x.get('source_key')))
                cursor=found['next_cursor']
                if cursor is not None and cursor>10000:raise Fault('RESOURCE_AMBIGUOUS','Use the exact resource ID in a smaller catalog')
            if len(matches)!=1:raise Fault('RESOURCE_AMBIGUOUS','Resource selector must match exactly one resource in the selected profile','$.resource_id',next_action='find_resources')
            suggested=matches[0].get('prepare_request')
            if not suggested:raise Fault('PROFILE_INCOMPATIBLE','Selected resource has no compatible request for this profile','$.resource_id')
            if resolved.get('operation') and resolved['operation']!=suggested['operation']:
                raise Fault('WORKFLOW_INCOMPATIBLE','Resource suggestion differs from the requested operation; no substitution performed','$.goal.operation')
            resolved['operation']=suggested['operation']
            schema=service.adapters.binding(profile)['descriptor']['request_schemas'][suggested['operation']]
            parameters={k:v for k,v in suggested.items() if k in schema['properties']}
            supplied=resolved.get('parameters',{})
            for field in ('asset_id','episode_id','case_indices'):
                if field in supplied and field in parameters and supplied[field]!=parameters[field]:
                    raise Fault('WORKFLOW_INCOMPATIBLE','Explicit input differs from selected resource','$.goal.parameters.'+field)
            resolved['parameters']={**parameters,**supplied}
            # Never adopt suggested CPU, memory, wall or verification ceilings.
        compiled=service.invoke('compile_goal',goal=resolved,provider_mode='disabled')
        if compiled['status']!='ready':
            return {'status':'needs_information','missing_parameters':compiled['missing_parameters'],
                    'run_id':None,'run':None,'result':None,'report':None,'artifacts':[], 'export_skipped':[]}
        plan_id=compiled['prepared_plan']['plan_id']
        with service.corpus.connect() as db:
            db.execute('INSERT OR IGNORE INTO wf_goal_requests VALUES(?,?,?)',(idempotency_key,request_digest,plan_id))
            winner=db.execute('SELECT * FROM wf_goal_requests WHERE request_key=?',(idempotency_key,)).fetchone()
            if winner['request_digest']!=request_digest:raise Fault('IDEMPOTENCY_CONFLICT','Key belongs to a different goal')
            plan_id=winner['plan_id']
    run=service.invoke('execute_plan',plan_id=plan_id,plan_digest=plan_id,
                       idempotency_key='goal:'+digest(idempotency_key))
    identifier=run['run_id']
    try:
        deadline=time.monotonic()+wait_seconds
        while run['status'] not in ('completed','failed','cancelled','budget_exhausted','incomplete'):
            if time.monotonic()>=deadline:
                return {'status':'waiting','missing_parameters':[],'run_id':identifier,'run':run,
                        'result':None,'report':None,'artifacts':[], 'export_skipped':[]}
            time.sleep(min(.1,max(0,deadline-time.monotonic())))
            run=service.invoke('get_run',run_id=identifier)
        result=service.invoke('get_result',result_id=identifier)
        report=service.invoke('get_result_report',result_id=identifier)
        artifacts=[];skipped=[]
        if include_artifacts:
            for artifact in result['result'].get('artifacts',[]):
                if artifact['name'].endswith('.json'):
                    artifacts.append(service.invoke('get_artifact_json',result_id=identifier,name=artifact['name']))
                else:skipped.append({'name':artifact['name'],'reason':'Original binary/JSONL download is separate from exact JSON export','href':artifact['href']})
        return {'status':'terminal','missing_parameters':[],'run_id':identifier,'run':run,
                'result':result,'report':report,'artifacts':artifacts,'export_skipped':skipped}
    except Fault as exc:
        exc.run_id=identifier
        raise


def pending_information(path, reason):
    return {'status':'needs_information','missing_parameters':[{'path':path,'reason':reason}],
            'run_id':None,'run':None,'result':None,'report':None,'artifacts':[],'export_skipped':[]}
