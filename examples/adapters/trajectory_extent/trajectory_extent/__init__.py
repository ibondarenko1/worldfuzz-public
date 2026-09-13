"""An external package using only declared service calls and adapter callbacks."""
from worldfuzz.platform.contracts import Fault, obj, ID
from worldfuzz.platform.trajectories import require_xyz_pair
import json

__version__ = '0.2.0'


class ExtentAdapter:
    def describe(self):
        return {
            'adapter_id':'example.trajectory-extent', 'version':__version__, 'contract_version':'1.0.0',
            'profile':{'id':'sensor.xyz.extent.v1','domain':'sensors',
                'state':'timestamp seconds; XYZ metres in the registered frame', 'action':None,
                'observation':'registered prediction trajectory',
                'outcome':'axis-aligned coordinate bounds; no success or accuracy judgement',
                'operations':['summarize_trajectory']},
            'license':'MIT','execution_mode':'offline_data_processing',
            'source_contract':{'kind':'registered_trajectory_v1','operations':{
                'summarize_trajectory':{
                    'required_rights':{'data':'inspect','code':None,'model':None,'scene':None},
                    'profile_fields':['components','observation_schema','frames','units'],
                    'require_known_profile':False,
                    'accepted_rights_statuses':['declared','verified'],
                    'execution_mode':'metadata_inspection'}}},
            'environment_lock':{'python':'>=3.11','dependencies':['worldfuzz>=0.4.0rc4,<0.5']},
            'limitations':['No alignment, localization accuracy, control or flight-success claim'],
            'request_schemas':{'summarize_trajectory':obj({'asset_id':ID}, ['asset_id'])}}

    def resources(self, service):
        return []  # Inputs are registered through the existing asset API.

    @staticmethod
    def source(service, asset_id):
        # Nested access must cross the scoped dispatcher in every execution phase.
        result=service.invoke('get_asset', asset_id=asset_id)
        asset=result['asset']
        if (asset.get('import_provenance',{}).get('adapter','direct.xyz.v1') not in ('direct.xyz.v1','tum.xyz.v1')
                or not all(k in asset for k in ('prediction','reference'))):
            raise Fault('PROFILE_INCOMPATIBLE',
                'summarize_trajectory requires a registered XYZ trajectory pair; recordings are unsupported',
                '$.parameters.asset_id', next_action='register_trajectory')
        require_xyz_pair(asset)
        return result

    def snapshot(self, service, request):
        result=self.source(service, request['parameters']['asset_id'])
        return {'asset_id':result['asset_id'],'sha256':result['sha256']}

    def execute(self, service, plan, records, artifacts, check, artifact):
        check()
        result=self.source(service, plan['request']['parameters']['asset_id'])
        if {'asset_id':result['asset_id'],'sha256':result['sha256']}!=plan['snapshot']:
            raise Fault('PLAN_CHANGED','Trajectory source changed')
        track=result['asset']['prediction']
        lower=[float('inf')]*3;upper=[-float('inf')]*3
        for i,row in enumerate(track):
            if i%256==0:check()
            for axis,value in enumerate(row[1:]):
                lower[axis]=min(lower[axis],value);upper[axis]=max(upper[axis],value)
        record={'samples':len(track),'min_xyz_m':lower,'max_xyz_m':upper,
                        'frame':result['asset']['frame'],'evidence':'coordinate_summary',
                        'control_outcome_verified':False}
        artifact('extent.json', (json.dumps(record,sort_keys=True,allow_nan=False)+'\n').encode())
        records.append(record)
