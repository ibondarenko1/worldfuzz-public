"""Planner-independent verification of recorded candidates on pinned grid tasks."""
import hashlib
import json
from pathlib import Path
from .contracts import Fault
from ..corpus import digest
from ..published_benchmarks import check_route, load_cases, sha, DATA


def evaluation_contract(request, snapshot):
    if request['profile']!='movingai.octile.v1' or request['operation']!='solve_navigation':
        return None
    return {'protocol':'movingai.octile.route-check.v1', 'profile':request['profile'],
            'inputs_sha256':digest(snapshot),'units':'grid_cells','length_tolerance':1e-5,
            'rules':['free_cells','adjacent_steps','no_corner_cutting','fixed_endpoints','published_optimal_length'],
            'max_seconds':request['max_seconds'],
            'verifier_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'checker_sha256':sha(Path(__file__).parents[1]/'published_benchmarks.py')}


def verify_navigation(service, run_id, plan, records, artifacts, checkpoint, *, accumulate=True):
    """Reopen hash-checked route bytes. Ignore all candidate outcome assertions."""
    contract=plan['evaluation_contract']
    if contract!=evaluation_contract(plan['request'],plan['snapshot']):
        raise Fault('PLAN_CHANGED','Evaluation protocol changed')
    provenance, _, maps, cases=load_cases()
    indices=plan['request'].get('case_indices',list(range(9)))
    records.clear()
    for index in indices:
        checkpoint()
        raw=service.artifact(run_id,f'route-{index}.json',result={'artifacts':artifacts})
        candidate=json.loads(raw)
        case=cases[index]
        if candidate.get('case')!=case or not isinstance(candidate.get('route'),list):
            raise Fault('VERIFICATION_FAILED','Candidate does not match the frozen source task')
        route=candidate['route']
        verdict=check_route(maps[case['map']],case,route,contract['length_tolerance'])
        row={'case_index':index,'check':verdict,'evidence':'independent_grid_outcome',
             'outcome':'success' if verdict['route_valid'] and verdict['matches_published_optimum'] else 'failed',
             'verification':{'protocol_sha256':digest(contract),'candidate_sha256':hashlib.sha256(raw).hexdigest(),
                             'method':'reopened_artifact_metric_recompute'}}
        records.append(row)
        # Only verified outcomes enter the reusable episode catalog.
        if row['outcome']=='success' and accumulate:
            episode={'source_key':f"{case['scenario_file']}:{case['data_row']}",'domain':'navigation',
                     'task':'octile_shortest_path','status':'OUTCOME_VERIFIED','label':'optimal_route',
                     'label_origin':'independent_grid_check_against_published_length',
                     'group_id':'movingai:'+sha(DATA/case['map']),'case':case,'states':route,
                     'actions':[[b[0]-a[0],b[1]-a[1]] for a,b in zip(route,route[1:])],
                     'state_semantics':'grid_x_y','action_semantics':'grid_delta_x_y','check':verdict}
            source={'name':'Moving AI street cases','version':'pinned-original-selection-v1',
                    'source_url':'https://www.movingai.com/benchmarks/street/index.html','license':'ODC-By-1.0',
                    'attribution':provenance,'adapter':'movingai-v1',
                    'procedure':'worldfuzz.'+plan['request'].get('algorithm','astar')+'.v1',
                    'procedure_sha256':plan['code']['adapters/planners.py']}
            row['accumulation']=service.ingest_verified(run_id,source,[episode])
    checkpoint()


def compare_navigation(left, right, a, b):
    contract=a.get('evaluation_contract')
    if not contract or contract!=b.get('evaluation_contract') or a['snapshot']!=b['snapshot'] or a['python']!=b['python']:
        raise Fault('INCOMPARABLE','Tasks, evaluation protocol, budget or Python version differ')
    if contract.get('protocol')!='movingai.octile.route-check.v1':
        raise Fault('INCOMPARABLE','Algorithm comparison unsupported for this protocol')
    indices=a['request'].get('case_indices',list(range(9)))
    if indices!=b['request'].get('case_indices',list(range(9))):raise Fault('INCOMPARABLE','Case order differs')
    paired=[]
    rows=[x['result'].get('records',[]) for x in (left,right)]
    for records in rows:
        if [r.get('case_index') for r in records]!=indices:
            raise Fault('INCOMPARABLE','Incomplete or duplicate case coverage')
        if any(r.get('verification',{}).get('protocol_sha256')!=digest(contract) for r in records):
            raise Fault('INCOMPARABLE','Missing independent verification under the frozen protocol')
    for l,r in zip(*rows):
        lengths=[row['check']['measured_length'] for row in (l,r)]
        paired.append({'case_index':l['case_index'],'left_outcome':l['outcome'],'right_outcome':r['outcome'],
                       'left_length':lengths[0],'right_length':lengths[1],
                       'length_delta_right_minus_left':None if None in lengths else lengths[1]-lengths[0]})
    return {'comparable':True,
            'same_records':[{k:v for k,v in row.items() if k!='accumulation'} for row in rows[0]]==
                           [{k:v for k,v in row.items() if k!='accumulation'} for row in rows[1]],
            'equivalent_outcomes':all(p['left_outcome']==p['right_outcome'] and
                p['length_delta_right_minus_left'] is not None and
                abs(p['length_delta_right_minus_left'])<=contract['length_tolerance'] for p in paired),
            'scope':'paired navigation outcomes; not a statistical speed benchmark',
            'summary':{'left_successes':sum(p['left_outcome']=='success' for p in paired),
                       'right_successes':sum(p['right_outcome']=='success' for p in paired),
                       'regressions':sum(p['left_outcome']=='success' and p['right_outcome']!='success' for p in paired),
                       'improvements':sum(p['left_outcome']!='success' and p['right_outcome']=='success' for p in paired)},
            'protocol':contract,'paired_results':paired,'coverage':{'expected':len(indices),'compared':len(paired)},
            'procedures':[{'algorithm':p['request'].get('algorithm','astar'),
                           'sha256':p['code'].get('adapters/planners.py')} for p in (a,b)]}
