"""Built-in profile metadata, discovery and compatibility."""
import json
from copy import deepcopy
from importlib import metadata
from ...corpus import digest
from ..contracts import PROFILES, PLAN, obj, Fault
from ..source_contracts import m1_contract, requirements
from .procedures import execute_builtin

FIELDS = {'solve_navigation': ('case_indices','max_cases','algorithm'),
          'evaluate_trajectory': ('asset_id','threshold_m'),
          'inspect_sensor_recording': ('asset_id','max_messages'),
          'run_pymunk_basic': ('seed',),
          'verify_episodes': ('filters',), 'export_dataset': ('filters',),
          'view_recorded_states': ('episode_id','start_index','frame_count','stride')}


class BuiltinAdapter:
    def __init__(self, profile):
        self.profile = deepcopy(profile)

    def describe(self):
        domain = self.profile['domain']
        if self.profile['id']=='pymunk.basic-fall.v7.2':
            from ..source_contracts import external_engine_contract
            if metadata.version('pymunk')!='7.2.0':raise Fault('ADAPTER_INVALID','Pymunk 7.2.0 is required')
            source=external_engine_contract()
            source_contract={'kind':'external_engine_v1','operations':{op:requirements(op) for op in self.profile['operations']}}
        elif domain in ('navigation', 'manipulation'):
            source = m1_contract(domain)
            source_contract = {
                'kind': 'm1', 'domain': domain,
                'asset_ref': {'id': source['asset']['asset_id'], 'version': source['asset']['version']},
                'profile_ref': {'id': source['profile']['profile_id'], 'version': source['profile']['version']},
                'operations': {op: requirements(op) for op in self.profile['operations']},
            }
        else:
            source_contract = {'kind': 'registered_sensor_v1' if self.profile['id']=='sensor.mcap.aerial.v1' else 'registered_trajectory_v1',
                               'operations': {op: requirements(op) for op in self.profile['operations']}}
        return {'adapter_id':self.profile['id'], 'version':'1.0.0', 'contract_version':'1.0.0',
                'profile':deepcopy(self.profile), 'license':'MIT',
                'execution_mode':self.profile['execution'],
                'source_contract':source_contract,
                'environment_lock':{'python':'>=3.11', 'runtime':'pymunk==7.2.0; Munk2D==2.0.1-ade7ed72849e60289eefb7a41e79ae6322fefaf3' if self.profile['id']=='pymunk.basic-fall.v7.2' else 'stdlib; included pinned benchmark assets'},
                'limitations':['Offline only; no device control or manipulation physics replay',
                               'Recorded simulator states stay opaque unless a versioned simulator profile defines them'],
                'request_schemas':{op:obj({k:PLAN['properties'][k] for k in FIELDS[op]},
                    ('episode_id',) if op=='view_recorded_states' else FIELDS[op] if op!='solve_navigation' else ()) for op in self.profile['operations']}}

    def snapshot(self, service, request):
        operation=request['operation']
        if operation in ('verify_episodes','export_dataset'):
            if request.get('filters',{}).get('domain')!=self.profile['domain']:
                raise Fault('PROFILE_INCOMPATIBLE','Filters must select the profile domain')
            items=service._episodes(request['filters'])
            return {'episodes':[{'id':i['id'],'sha256':i['payload_sha256'],'split':i['split'],
                         'group_id':i['episode']['group_id'],'source':digest(i['source'])} for i in items]}
        if operation=='view_recorded_states':
            selected=service._episode(request['episode_id'],'manipulation')
            episode=selected['episode'];start=request.get('start_index',0);stride=request.get('stride',1)
            count=request.get('frame_count',1)
            indices=list(range(start,min(len(episode['states']),start+count*stride),stride))
            if not indices:raise Fault('SCHEMA_INVALID','Recorded-state window starts after the episode')
            return {'episode':{'id':selected['id'],'sha256':selected['payload_sha256'],
                'group_id':episode['group_id'],'split':selected['split']},'indices':indices}
        if operation=='evaluate_trajectory':
            from ..trajectories import require_xyz_pair
            asset=require_xyz_pair(service._asset(request['asset_id']))
            return {'asset':digest(asset)}
        if operation=='inspect_sensor_recording':
            asset=service._asset(request['asset_id'])
            if asset.get('import_provenance',{}).get('adapter')!='mcap.ros.recording.v1':
                raise Fault('PROFILE_INCOMPATIBLE','Asset is not a registered MCAP recording')
            return {'asset':digest(asset),'raw_sha256':asset['recording']['raw_sha256'],
                    'message_count':len(asset['recording']['messages'])}
        if operation=='run_pymunk_basic':
            from ..source_contracts import external_engine_contract
            source=external_engine_contract()
            return {'task_manifest':source['manifest'],'asset_ref':{'id':source['asset']['asset_id'],
                    'version':source['asset']['version']},'seed':request.get('seed',0)}
        from ...published_benchmarks import load_cases
        provenance, _, _, cases=load_cases()
        indices=request.get('case_indices',list(range(9)))
        if len(set(indices))!=len(indices):raise Fault('SCHEMA_INVALID','Duplicate case indices')
        if len(indices)>request.get('max_cases',9):raise Fault('BUDGET_EXCEEDED','Selected cases exceed max_cases')
        return {'inputs':provenance,'cases':[cases[i] for i in indices]}

    def resources(self, service):
        domain=self.profile['domain']
        result=[]
        if domain=='navigation':
            from ...published_benchmarks import load_cases
            source=m1_contract('navigation')
            result.extend({'kind':'task','id':f'movingai:{i}','case_index':i,'profile':self.profile['id'],'case':case,
                          'source_ref':{'id':source['asset']['asset_id'],'version':source['asset']['version']},
                          'profile_ref':{'id':source['profile']['profile_id'],'version':source['profile']['version']},
                          'execution_mode':'offline_grid_planning',
                          'verification_scope':'grid cells, adjacency, endpoints and published optimal path length; no body dynamics',
                          'prepare_request':{'profile':self.profile['id'],'operation':'solve_navigation',
                              'case_indices':[i],'max_seconds':30,'verification':{'mode':'metric_recompute'}}}
                          for i,case in enumerate(load_cases()[3]))
        if domain in ('navigation','manipulation'):
            for item in service.corpus.listing()['episodes']:
                if item['domain']!=domain:continue
                resource={'kind':'episode',**item}
                if domain=='manipulation':
                    resource.update({'supported_access':['metadata','recorded_state_view'],
                        'simulator_action_replay':'unavailable: exact simulator state/frame compatibility is not established',
                        'prepare_request':{'profile':self.profile['id'],'operation':'view_recorded_states',
                            'episode_id':item['id'],'start_index':0,'frame_count':1,'stride':1,
                            'max_seconds':30,'verification':{'mode':'integrity'}}})
                result.append(resource)
        if domain=='sensors':
            with service.corpus.connect() as db:
                for row in db.execute('SELECT * FROM wf_assets ORDER BY id'):
                    body=json.loads(row['body']);is_mcap=body.get('import_provenance',{}).get('adapter')=='mcap.ros.recording.v1'
                    if is_mcap!=(self.profile['id']=='sensor.mcap.aerial.v1'):continue
                    result.append({'kind':'sensor_recording' if is_mcap else 'asset','id':row['id'],'name':body['name'],
                        'profile':self.profile['id'],'source_url':body['source_url'],
                        'prepare_request':{'profile':self.profile['id'],
                            'operation':'inspect_sensor_recording' if is_mcap else 'evaluate_trajectory',
                            'asset_id':row['id'],'max_messages':100,'max_seconds':30,
                            'verification':{'mode':'integrity'}} if is_mcap else None})
        if self.profile['id']=='pymunk.basic-fall.v7.2':
            from ..source_contracts import external_engine_contract
            source=external_engine_contract()
            result.append({'kind':'task','id':'pymunk:basic_test:7.2.0','profile':self.profile['id'],
                'source_ref':{'id':source['asset']['asset_id'],'version':source['asset']['version']},
                'source_url':source['manifest']['source_url'],'engine':'pymunk==7.2.0',
                'native_engine':'Munk2D=='+source['manifest']['native_engine_version'],
                'verification_scope':'published headless falling-ball dynamics in pixel units; no real-world equivalence',
                'prepare_request':{'profile':self.profile['id'],'operation':'run_pymunk_basic','seed':42,
                    'max_seconds':30,'resource_limits':{'cpu_seconds':5,'memory_bytes':536870912,'artifact_bytes':1048576},
                    'required_runner_capabilities':['cpu_seconds','memory_bytes','wall_seconds','artifact_bytes'],
                    'verification':{'mode':'metric_recompute'}}})
        return result

    def execute(self, service, plan, records, artifacts, check, artifact):
        execute_builtin(service, plan, records, artifacts, check, artifact)


def builtins():
    return [BuiltinAdapter(p) for p in PROFILES]
