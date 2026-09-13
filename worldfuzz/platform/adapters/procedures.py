"""Built-in offline procedures, separate from job orchestration."""
import math
from ..contracts import Fault
from ...corpus import digest, encoded


def execute_builtin(service, plan, records, artifacts, check, artifact):
    request=plan['request'];operation=request['operation']
    if operation=='solve_navigation':
        from ...published_benchmarks import load_cases
        from .planners import solve
        _, _, maps, cases=load_cases()
        for index in request.get('case_indices',list(range(9))):
            check();case=cases[index]
            route=solve(maps[case['map']],case,check,request.get('algorithm','astar'))
            artifact(f'route-{index}.json',encoded({'case':case,'route':route}).encode())
            records.append({'case_index':index,'evidence':'candidate_route','outcome':'unverified'})
    elif operation in ('verify_episodes','export_dataset'):
        items=service._episodes(request['filters'])
        if {'episodes':[{'id':i['id'],'sha256':i['payload_sha256'],'split':i['split'],'group_id':i['episode']['group_id'],
                          'source':digest(i['source'])} for i in items]}!=plan['snapshot']:
            raise Fault('PLAN_CHANGED','Dataset selection changed during preparation')
        groups={}
        for item in items:
            check();episode=item['episode'];group=episode['group_id']
            if group in groups and groups[group]!=item['split']:raise Fault('SPLIT_LEAKAGE','A group crosses split boundaries')
            groups[group]=item['split']
            row={'episode_id':item['id'],'payload_sha256':item['payload_sha256'],'group_id':group,'split':item['split'],
                 'source_status':episode['status'],'source_label':episode['label'],'evidence':'stored_payload_integrity',
                 'physics_replayed':False}
            if episode['domain']=='navigation':
                from ...published_benchmarks import load_cases, check_route
                maps=load_cases()[2]
                row['check']=check_route(maps[episode['case']['map']],episode['case'],episode['states'])
                row['evidence']='independent_grid_outcome'
            records.append(row)
        if operation=='export_dataset':
            payload=('\n'.join(encoded(item) for item in items)+'\n').encode()
            artifact('episodes.jsonl',payload)
            manifest={'schema_version':'1.0.0','profile':request['profile'],'filters':request['filters'],
                      'episodes':plan['snapshot']['episodes'],'groups':groups,'split_policy':'existing immutable group assignments',
                      'source_rights':[{'source_id':digest(i['source']),'data_license':i['source'].get('license','unknown'),
                                       'adapter_code_license':'MIT','source_evidence':i['source'],
                                       'assessment':'source declaration; no new legal clearance'}
                                      for i in {digest(x['source']):x for x in items}.values()],
                      'training_performed':False,'artifact':artifacts[0]}
            artifact('dataset-manifest.json',encoded(manifest).encode())
    elif operation=='view_recorded_states':
        item=service._episode(request['episode_id'],'manipulation')
        episode=item['episode'];indices=plan['snapshot']['indices']
        if item['payload_sha256']!=plan['snapshot']['episode']['sha256']:
            raise Fault('PLAN_CHANGED','Recorded episode changed after preparation')
        frames=[]
        for index in indices:
            check()
            frames.append({'source_index':index,'simulator_state':episode['states'][index],
                'source_action':episode['actions'][index],
                'source_reward':episode['rewards'][index],'source_done':episode['dones'][index]})
        record={'episode_id':item['id'],'source_key':episode['source_key'],'group_id':episode['group_id'],
            'split':item['split'],'frames':frames,'control_frequency_hz':episode['environment']['env_kwargs'].get('control_freq'),
            'controller_config':episode['environment']['env_kwargs'].get('controller_configs'),
            'state_semantics':episode['state_semantics'],'action_semantics':episode['action_semantics'],
            'state_vector_width':len(episode['states'][0]),'action_vector_width':len(episode['actions'][0]),
            'evidence':'exact recorded source values','physics_replayed':False,
            'task_outcome_independently_verified':False,
            'unknowns':['simulator state element mapping','complete simulator frame conventions',
                        'physical calibration','action replay result']}
        artifact('recorded-state-window.json',encoded(record).encode())
        records.append(record)
    elif operation=='evaluate_trajectory':
        from ..trajectories import require_xyz_pair
        asset=require_xyz_pair(service._asset(request['asset_id']))
        squared=[]
        for prediction,reference in zip(asset['prediction'],asset['reference']):
            if len(squared)%256==0:check()
            value=sum((a-b)**2 for a,b in zip(prediction[1:],reference[1:]))
            if not math.isfinite(value):raise Fault('SCHEMA_INVALID','Trajectory range exceeds numerical bounds')
            squared.append(value)
        rmse=math.sqrt(math.fsum(squared)/len(squared))
        records.append({'rmse_m':rmse,'samples':len(squared),'threshold_m':request['threshold_m'],
                        'outcome':'pass' if rmse<=request['threshold_m'] else 'fail',
                        'reference_origin':'user_supplied','frame':asset['frame'],
                        'protocol':'same_timestamp_xyz_no_alignment_v1','control_outcome_verified':False})
    elif operation=='inspect_sensor_recording':
        asset=service._asset(request['asset_id']);recording=asset['recording']
        if digest(asset)!=plan['snapshot']['asset'] or recording['raw_sha256']!=plan['snapshot']['raw_sha256']:
            raise Fault('PLAN_CHANGED','Registered MCAP bytes changed after preparation')
        selected=recording['messages'][:request.get('max_messages',100)]
        for index in range(0,len(selected),128):check()
        inventory={'asset_id':request['asset_id'],'source_url':asset['source_url'],'source_revision':asset['source_revision'],
            'platform':asset['platform'],'ros_version':asset['ros_version'],'frame':asset['frame'],'units':asset['units'],
            'raw_sha256':recording['raw_sha256'],'raw_bytes':recording['raw_bytes'],
            'mcap_library':recording['mcap_library'],'schemas':recording['schemas'],'channels':recording['channels'],
            'total_messages':len(recording['messages']),'returned_messages':selected,
            'time_range_ns':recording['time_range_ns'],'evidence':'recorded message inventory and byte integrity',
            'control_actions_present':False,'flight_outcome':None,'physics_replayed':False,
            'unknowns':['sensor calibration','external transform tree','vehicle commands','flight outcome']}
        from ..exact_json import project, CONTRACT
        inventory=project(inventory)
        inventory['data_representation']=dict(CONTRACT)
        artifact('sensor-recording-inventory.json',encoded(inventory).encode());records.append(inventory)
    elif operation=='run_pymunk_basic':
        from .pymunk_engine import run_basic
        result=run_basic(request.get('seed',0),plan['snapshot']['task_manifest']['parameters'],check)
        artifact('pymunk-basic-trace.json',encoded(result).encode())
        records.append({key:value for key,value in result.items() if key!='trace'})
