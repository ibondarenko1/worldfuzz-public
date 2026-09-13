"""Independent verification modes; integrity never implies simulation or physics."""
import hashlib
import math
from pathlib import Path

from ..corpus import digest
from .contracts import Fault

PROTOCOLS={'solve_navigation':'movingai.octile.route-check.v1',
           'evaluate_trajectory':'same_timestamp_xyz_no_alignment_v1',
           'run_pymunk_basic':'pymunk.basic-fall.discrete-check.v1',
           'replay_can_actions':'pickplacecan.geometry.v1',
           'build_dataset':'worldfuzz.dataset-roundtrip.v1'}


def prepare(request):
    spec=request.get('verification',{})
    mode=spec.get('mode','metric_recompute' if request['operation'] in PROTOCOLS else 'integrity')
    protocol=spec.get('protocol','sha256-artifacts-v1' if mode=='integrity' else PROTOCOLS.get(request['operation']))
    if mode=='replay':raise Fault('VERIFICATION_UNSUPPORTED','This local runner has no replay verifier')
    expected='sha256-artifacts-v1' if mode=='integrity' else PROTOCOLS.get(request['operation'])
    if expected is None or protocol!=expected:
        raise Fault('VERIFICATION_INCOMPATIBLE','Requested metric protocol is not supported by this operation')
    return {'mode':mode,'protocol':protocol,
            'verifier_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope':'stored bytes and declared offline metrics; no physical replay'}


def verify(service,run_id,plan,records,artifacts,checkpoint,*,spec=None,accumulate=False):
    spec=spec or plan['verification_contract']
    request={**plan['request'],'verification':{'mode':spec['mode'],'protocol':spec['protocol']}}
    if prepare(request)!=spec:raise Fault('VERIFICATION_INCOMPATIBLE','Verifier identity or protocol changed')
    if service._snapshot(plan['request'])!=plan['snapshot']:
        raise Fault('PLAN_CHANGED','Verification source snapshot changed')
    hashes=[]
    for item in artifacts:
        checkpoint()
        raw=service.artifact(run_id,item['name'],result={'artifacts':artifacts})
        if len(raw)!=item['bytes']:raise Fault('INTEGRITY_FAILED','Artifact length differs from manifest')
        hashes.append({'name':item['name'],'sha256':hashlib.sha256(raw).hexdigest()})
    checks=[{'mode':'integrity','protocol':'sha256-artifacts-v1','status':'passed'}]
    outcome='not_evaluated'
    if spec['mode']=='metric_recompute':
        operation=plan['request']['operation']
        if operation=='solve_navigation':
            from .verification import verify_navigation
            verify_navigation(service,run_id,plan,records,artifacts,checkpoint,accumulate=accumulate)
            outcome='no_violation_found' if all(r['outcome']=='success' for r in records) else 'violation_found'
        elif operation=='evaluate_trajectory':
            # Recompute from preserved inputs, never accept the adapter's score.
            source=service._asset(plan['request']['asset_id'])
            squared=[]
            for p,r in zip(source['prediction'],source['reference']):
                if len(squared)%256==0:checkpoint()
                squared.append(sum((a-b)**2 for a,b in zip(p[1:],r[1:])))
            rmse=math.sqrt(math.fsum(squared)/len(squared))
            if not math.isfinite(rmse):raise Fault('VERIFICATION_FAILED','Non-finite recomputed metric')
            threshold=plan['request']['threshold_m']
            records[:]=[{'rmse_m':rmse,'samples':len(squared),'threshold_m':threshold,
                         'outcome':'pass' if rmse<=threshold else 'fail','frame':source['frame'],
                         'protocol':spec['protocol'],'reference_origin':'user_supplied',
                         'control_outcome_verified':False}]
            outcome='no_violation_found' if rmse<=threshold else 'violation_found'
        elif operation=='run_pymunk_basic':
            import json
            raw=service.artifact(run_id,'pymunk-basic-trace.json',result={'artifacts':artifacts})
            observed=json.loads(raw);parameters=plan['snapshot']['task_manifest']['parameters']
            trace=observed['trace'];spawn=parameters['spawn_tick'];dt=parameters['dt_seconds']
            gravity=parameters['gravity_px_per_s2'][1];initial=parameters['initial_y_px']
            expected_remove=next(k for k in range(parameters['steps'])
                if initial+gravity*dt*dt*k*(k-1)/2<parameters['remove_below_y_px'])+spawn
            if (not observed['simulation_executed'] or observed['device_calls']!=0
                    or observed['engine']!={'name':'pymunk','version':'7.2.0','native_name':'Munk2D',
                        'native_version':'2.0.1-ade7ed72849e60289eefb7a41e79ae6322fefaf3'}
                    or observed['removed_tick']!=expected_remove or len(trace)!=expected_remove-spawn):
                raise Fault('VERIFICATION_FAILED','External engine outcome differs from the pinned task')
            x=trace[0]['position'][0]
            if not parameters['x_min_px']<=x<=parameters['x_max_px']:
                raise Fault('VERIFICATION_FAILED','Published spawn range was not respected')
            for k,item in enumerate(trace):
                checkpoint();expected_y=initial+gravity*dt*dt*k*(k-1)/2;expected_v=gravity*dt*k
                if (item['tick']!=spawn+k or item['position'][0]!=x or item['velocity'][0]!=0
                        or not math.isclose(item['position'][1],expected_y,abs_tol=1e-9)
                        or not math.isclose(item['velocity'][1],expected_v,abs_tol=1e-9)):
                    raise Fault('VERIFICATION_FAILED','Pymunk trace failed independent discrete dynamics check')
            records[:]=[{'seed':observed['seed'],'steps':observed['steps'],'removed_tick':observed['removed_tick'],
                'engine':observed['engine'],'simulation_executed':True,'task_outcome':'completed',
                'outcome':'success','evidence':'independent discrete dynamics and removal check',
                'real_world_equivalence':False,'physics_replayed':False}]
            outcome='no_violation_found'
        elif operation=='replay_can_actions':
            from .adapters.can_checker import verify as verify_can
            records[:]=[verify_can(service,run_id,plan,artifacts,checkpoint)]
            outcome='no_violation_found' if records[0]['outcome']=='success' else 'violation_found'
        elif operation=='build_dataset':
            from .adapters.dataset_build import verify_generation
            verify_generation(service,run_id,plan,records,artifacts,checkpoint)
            outcome='no_violation_found'
        else:raise Fault('VERIFICATION_UNSUPPORTED','No metric verifier for this operation')
        checks.append({'mode':'metric_recompute','protocol':spec['protocol'],'status':'passed'})
    report={'schema_version':'1.0.0','requested_mode':spec['mode'],'performed_checks':checks,
            'proven_result':outcome,'physics_replayed':False,'input_snapshot_sha256':digest(plan['snapshot']),
            'artifact_hashes':hashes,'scope':spec['scope'],'verifier_sha256':spec['verifier_sha256']}
    report['verification_id']=digest(report)
    return report
