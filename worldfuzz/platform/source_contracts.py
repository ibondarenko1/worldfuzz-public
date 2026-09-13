"""Immutable source/profile contracts used by the platform authorization gate.

The two published bundles are the exact M1 entities accepted in WP06-WP08.
Registered trajectory entities are derived only from preserved registration
metadata and remain explicitly declared rather than legally verified.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from ..corpus import encoded
from ..entities import validate_entity
from ..entities.schemas import VERSION


def _right(subject, *, license_name=None, status='unknown', uses=(), evidence=(), limitations=()):
    return {'subject': subject, 'license': license_name, 'status': status,
            'allowed_uses': list(uses), 'evidence_urls': list(evidence),
            'limitations': list(limitations)}


def _m1_rights(license_name, evidence, uses, *, scene=False):
    result = []
    for subject in ('data', 'code', 'model', 'scene'):
        covered = subject == 'data' or scene and subject == 'scene'
        result.append(_right(
            subject, license_name=license_name if covered else None,
            status='verified' if covered else 'unknown', uses=uses if covered else (),
            evidence=(evidence,) if covered else (),
            limitations=('Declaration applies to the pinned source bytes',) if covered else
                        ('This subject is outside the source-data declaration',)))
    return result


M1 = {
    'navigation': {
        'family': {
            'entity_type': 'SourceFamily', 'schema_version': VERSION,
            'family_id': 'movingai.street', 'name': 'Moving AI city/street benchmarks',
            'canonical_sources': ['https://www.movingai.com/benchmarks/street/index.html'],
            'lineage': [], 'domain': ['navigation'],
        },
        'asset': {
            'entity_type': 'AssetVersion', 'schema_version': VERSION,
            'asset_id': '902dcd0856dede0e7a7b003e3170d718dabc99ff4fae3eba51842e9837a69400',
            'version': 'pinned-original-selection-v1', 'family_id': 'movingai.street',
            'source_locator': 'examples/published-street-cases-20260910/manifest.json',
            'observed_hash_or_null': '686ce89d7abd3480f73d5a7dedcb8680c77eabbb5ebf96415523417d31e57644',
            'size_or_null': 984, 'access_status': 'file_verified',
            'rights': _m1_rights('ODC-By-1.0',
                'https://opendatacommons.org/licenses/by/1-0/',
                ('inspect', 'evaluate', 'export'), scene=True),
            'observed_at': 1789084800,
        },
        'profile': {
            'entity_type': 'EmbodimentProfile', 'schema_version': VERSION,
            'profile_id': 'worldfuzz.legacy.navigation', 'version': VERSION,
            'components': [{'component_id': 'component', 'kind': 'navigation'}],
            'state_schema': [{'name': 'state', 'dtype': 'float64', 'shape': [2],
                'unit': 'cell', 'frame': 'world', 'semantic': 'grid position', 'rate_hz': None}],
            'action_schema': [{'name': 'action', 'dtype': 'float64', 'shape': [2],
                'unit': 'cell', 'frame': 'world', 'semantic': 'grid displacement', 'rate_hz': None}],
            'observation_schema': [{'name': 'observation', 'dtype': 'float64', 'shape': [2],
                'unit': 'cell', 'frame': 'world', 'semantic': 'binary occupancy map', 'rate_hz': None}],
            'frames': [{'frame_id': 'world', 'parent': None,
                        'convention': 'Explicit example frame; not inferred from data'}],
            'units': ['cell', 's'],
            'time_model': {'unit': 's', 'clock': 'example clock',
                           'synchronization': 'exact timestamp pairs'},
            'unknown_fields': [],
        },
    },
    'manipulation': {
        'family': {
            'entity_type': 'SourceFamily', 'schema_version': VERSION,
            'family_id': 'robomimic.can-paired', 'name': 'robomimic Can Paired low dimensional',
            'canonical_sources': ['https://huggingface.co/datasets/amandlek/robomimic/tree/74fa018461f479cd9fd15b924a16103012096203/v1.5/can/paired'],
            'lineage': [], 'domain': ['manipulation'],
        },
        'asset': {
            'entity_type': 'AssetVersion', 'schema_version': VERSION,
            'asset_id': 'e1aaaf76fb22353fa4c8a4fbae163031158da9a549fc5bc585e7ad1cdaa176ad',
            'version': 'v1.5-74fa018461f479cd9fd15b924a16103012096203',
            'family_id': 'robomimic.can-paired',
            'source_locator': 'https://huggingface.co/datasets/amandlek/robomimic/resolve/74fa018461f479cd9fd15b924a16103012096203/v1.5/can/paired/low_dim_v15.hdf5',
            'observed_hash_or_null': '5ad06c3591d929eadddc8a7511200a08a58686bfbb22691e966398f0fa005d66',
            'size_or_null': 41692232, 'access_status': 'file_verified',
            'rights': _m1_rights('MIT',
                'https://huggingface.co/datasets/amandlek/robomimic/blob/74fa018461f479cd9fd15b924a16103012096203/README.md',
                ('inspect', 'evaluate', 'train', 'export')),
            'observed_at': 1789084800,
        },
        'profile': {
            'entity_type': 'EmbodimentProfile', 'schema_version': VERSION,
            'profile_id': 'worldfuzz.legacy.manipulation', 'version': VERSION,
            'components': [{'component_id': 'component', 'kind': 'manipulation'}],
            'state_schema': [{'name': 'state', 'dtype': 'float64', 'shape': [7],
                'unit': 'rad', 'frame': None, 'semantic': 'joint positions', 'rate_hz': None}],
            'action_schema': [{'name': 'action', 'dtype': 'float64', 'shape': [7],
                'unit': 'rad', 'frame': None, 'semantic': 'joint position target', 'rate_hz': None}],
            'observation_schema': [{'name': 'observation', 'dtype': 'float64', 'shape': [7],
                'unit': 'rad', 'frame': None, 'semantic': 'measured joint positions', 'rate_hz': None}],
            'frames': [{'frame_id': 'world', 'parent': None,
                        'convention': 'Explicit example frame; not inferred from data'}],
            'units': ['rad', 's'],
            'time_model': {'unit': 's', 'clock': 'example clock',
                           'synchronization': 'exact timestamp pairs'},
            'unknown_fields': ['controller configuration', 'simulator frame conventions',
                               'physical calibration and replay state'],
        },
    },
}


def m1_contract(domain):
    """Return validated copies of the exact M1 source entities."""
    result = deepcopy(M1[domain])
    for value in result.values():
        validate_entity(value)
    return result


def registered_trajectory_contract(asset_id, asset):
    """Describe a locally registered trajectory without upgrading its evidence."""
    body = encoded(asset).encode()
    frame_id = 'declared-frame-' + hashlib.sha256(asset['frame'].encode()).hexdigest()[:16]
    family = {
        'entity_type': 'SourceFamily', 'schema_version': VERSION,
        'family_id': 'worldfuzz.registered-trajectory.' + asset_id,
        'name': 'Registered trajectory: ' + asset['name'],
        'canonical_sources': [asset['source_url']], 'lineage': [], 'domain': ['sensors'],
    }
    asset_entity = {
        'entity_type': 'AssetVersion', 'schema_version': VERSION,
        'asset_id': asset_id, 'version': 'registered-v1',
        'family_id': family['family_id'], 'source_locator': asset['source_url'],
        'observed_hash_or_null': asset_id, 'size_or_null': len(body),
        'access_status': 'file_verified', 'observed_at': 0,
        'rights': [
            _right('data', license_name=asset['data_license'], status='declared',
                   uses=('inspect', 'evaluate'), evidence=(asset['source_url'],),
                   limitations=('Caller declaration; license was not independently verified',
                                'No export, redistribution or training permission inferred')),
            _right('code', license_name=asset['code_license'], status='declared',
                   evidence=(asset['source_url'],),
                   limitations=('Code license declaration is preserved but not required by this operation',)),
            _right('model', limitations=('No model rights declaration supplied',)),
            _right('scene', limitations=('No scene rights declaration supplied',)),
        ],
    }
    profile_basis = {'frame': asset['frame'], 'units': asset['units'],
                     'import': asset.get('import_provenance', {}).get('adapter', 'direct.xyz.v1')}
    profile = {
        'entity_type': 'EmbodimentProfile', 'schema_version': VERSION,
        'profile_id': 'worldfuzz.registered-trajectory.' + hashlib.sha256(encoded(profile_basis).encode()).hexdigest(),
        'version': VERSION,
        'components': [{'component_id': 'registered-trajectory', 'kind': 'sensors'}],
        'state_schema': [{'name': 'reference_xyz', 'dtype': 'float64', 'shape': [3],
            'unit': 'm', 'frame': frame_id, 'semantic': 'caller-supplied reference position',
            'rate_hz': None}],
        'action_schema': None,
        'observation_schema': [{'name': 'prediction_xyz', 'dtype': 'float64', 'shape': [3],
            'unit': 'm', 'frame': frame_id, 'semantic': 'registered prediction position',
            'rate_hz': None}],
        'frames': [{'frame_id': frame_id, 'parent': None,
                    'convention': 'Caller-declared shared frame: ' + asset['frame']}],
        'units': ['m', 's'],
        'time_model': {'unit': 's', 'clock': 'source timestamps',
                       'synchronization': 'exact timestamp pairs; no interpolation or alignment'},
        'unknown_fields': ['physical platform identity', 'sensor calibration',
                           'absolute frame convention beyond the caller declaration'],
    }
    result = {'family': family, 'asset': asset_entity, 'profile': profile}
    for value in result.values():
        validate_entity(value)
    return result


def registered_sensor_contract(asset_id, asset):
    """Describe exact registered MCAP bytes without interpreting ROS payloads."""
    recording=asset['recording'];source=asset['source_url'];frame_id='declared-frame-'+hashlib.sha256(asset['frame'].encode()).hexdigest()[:16]
    family={'entity_type':'SourceFamily','schema_version':VERSION,
        'family_id':'worldfuzz.registered-sensor.'+asset_id,'name':'Registered MCAP: '+asset['name'],
        'canonical_sources':[source],'lineage':[],'domain':['sensors']}
    asset_entity={'entity_type':'AssetVersion','schema_version':VERSION,'asset_id':asset_id,
        'version':'mcap-'+recording['raw_sha256'],'family_id':family['family_id'],'source_locator':source,
        'observed_hash_or_null':asset_id,'size_or_null':len(encoded(asset).encode()),'access_status':'file_verified',
        'observed_at':0,'rights':[
            _right('data',license_name=asset['data_license'],status='declared',uses=('inspect','evaluate'),evidence=(source,),
                limitations=('Caller declaration applies to the registered bytes; redistribution and training are not inferred',)),
            _right('code',license_name=asset['code_license'],status='declared',evidence=(source,),
                limitations=('Code license is preserved but is not required for recording inspection',)),
            _right('model',limitations=('No model is present in the registered recording contract',)),
            _right('scene',limitations=('Scene rights were not declared',))]}
    profile={'entity_type':'EmbodimentProfile','schema_version':VERSION,
        'profile_id':'worldfuzz.mcap.'+hashlib.sha256(encoded({'ros':asset['ros_version'],'platform':asset['platform'],
            'frame':asset['frame'],'units':asset['units']}).encode()).hexdigest(),'version':VERSION,
        'components':[{'component_id':asset['platform'],'kind':'sensors'}],'state_schema':[],'action_schema':None,
        'observation_schema':[{'name':'serialized_message_byte','dtype':'uint8','shape':[1],'unit':None,'frame':frame_id,
            'semantic':'one byte of an opaque variable-length '+asset['ros_version']+' serialized payload','rate_hz':None}],
        'frames':[{'frame_id':frame_id,'parent':None,'convention':'Caller-declared recording frame: '+asset['frame']}],
        'units':list(asset['units'])+(['ns'] if 'ns' not in asset['units'] else []),
        'time_model':{'unit':'ns','clock':'MCAP log_time and publish_time','synchronization':'preserved per message; no alignment inferred'},
        'unknown_fields':['sensor calibration','transform tree outside the recording','command semantics',
                          'vehicle state and flight outcome']}
    result={'family':family,'asset':asset_entity,'profile':profile}
    for value in result.values():validate_entity(value)
    return result


def external_engine_contract():
    """Pin the published Pymunk task and its exact simulation semantics."""
    manifest_path=Path(__file__).resolve().parents[1]/'benchmark_data'/'pymunk-basic-test-7.2.0.json'
    raw=manifest_path.read_bytes();manifest=json.loads(raw);asset_id=hashlib.sha256(raw).hexdigest()
    family={'entity_type':'SourceFamily','schema_version':VERSION,'family_id':'pymunk.basic-test.7.2.0',
        'name':manifest['name'],'canonical_sources':[manifest['source_url']],'lineage':[],'domain':['composite']}
    asset={'entity_type':'AssetVersion','schema_version':VERSION,'asset_id':asset_id,
        'version':'7.2.0-'+manifest['source_git_blob'],'family_id':family['family_id'],
        'source_locator':'worldfuzz/benchmark_data/pymunk-basic-test-7.2.0.json',
        'observed_hash_or_null':asset_id,'size_or_null':len(raw),'access_status':'file_verified','observed_at':0,
        'rights':[_right('data',license_name='MIT',status='verified',uses=('inspect','evaluate'),
                evidence=(manifest['source_url'],),limitations=('The packaged task manifest is derived from the pinned published example',)),
            _right('code',license_name='MIT',status='verified',uses=('inspect','evaluate'),
                evidence=(manifest['source_url'],'https://github.com/viblo/pymunk/blob/7.2.0/LICENSE.txt'),
                limitations=('Rights apply to the pinned published example and engine code',)),
            _right('model',limitations=('No model is used',)),_right('scene',limitations=('No external scene asset is used',))]}
    profile={'entity_type':'EmbodimentProfile','schema_version':VERSION,'profile_id':'worldfuzz.pymunk.basic-fall',
        'version':'7.2.0','components':[{'component_id':'pymunk-7.2.0-munk2d-2.0.1','kind':'simulation_engine'}],
        'state_schema':[{'name':'position_xy','dtype':'float64','shape':[2],'unit':'px','frame':'pymunk_world_2d',
            'semantic':'2D simulated body center','rate_hz':50},
            {'name':'velocity_xy','dtype':'float64','shape':[2],'unit':'px_per_s','frame':'pymunk_world_2d',
             'semantic':'2D simulated body velocity','rate_hz':50}],
        'action_schema':None,'observation_schema':[{'name':'engine_trace','dtype':'float64','shape':[4],
            'unit':'px','frame':'pymunk_world_2d','semantic':'x y vx vy values recorded before each engine step','rate_hz':50}],
        'frames':[{'frame_id':'pymunk_world_2d','parent':None,'convention':'Published 2D example coordinates; positive y is upward'}],
        'units':['px','px_per_s','s'],'time_model':{'unit':'s','clock':'fixed simulation step',
            'synchronization':'single Pymunk Space at exactly 1/50 second'},'unknown_fields':[]}
    result={'family':family,'asset':asset,'profile':profile,'manifest':manifest}
    for value in (family,asset,profile):validate_entity(value)
    return result


def requirements(operation):
    """Return the operation facts needed for compatibility and authorization."""
    all_semantics = ['components', 'state_schema', 'action_schema', 'observation_schema',
                     'frames', 'units', 'time_model']
    table = {
        'solve_navigation': {'data_use': 'evaluate', 'scene_use': 'evaluate',
                             'profile_fields': all_semantics, 'require_known_profile': True,
                             'accepted_rights_statuses': ['verified'], 'execution_mode': 'offline_evaluation'},
        'verify_episodes': {'data_use': 'inspect', 'scene_use': None,
                            'profile_fields': ['components'], 'require_known_profile': False,
                            'accepted_rights_statuses': ['verified'], 'execution_mode': 'metadata_inspection'},
        'export_dataset': {'data_use': 'export', 'scene_use': None,
                           'profile_fields': [], 'require_known_profile': False,
                            'accepted_rights_statuses': ['verified'], 'execution_mode': 'metadata_inspection'},
        'view_recorded_states': {'data_use': 'inspect', 'scene_use': None,
                                 'profile_fields': ['components'], 'require_known_profile': False,
                                 'accepted_rights_statuses': ['verified'],
                                 'execution_mode': 'metadata_inspection'},
        'evaluate_trajectory': {'data_use': 'evaluate', 'scene_use': None,
                                'profile_fields': all_semantics, 'require_known_profile': False,
                                'accepted_rights_statuses': ['declared', 'verified'],
                                'execution_mode': 'offline_evaluation'},
        'summarize_trajectory': {'data_use': 'inspect', 'scene_use': None,
                                 'profile_fields': ['components', 'observation_schema', 'frames', 'units'],
                                 'require_known_profile': False,
                                 'accepted_rights_statuses': ['declared', 'verified'],
                                 'execution_mode': 'metadata_inspection'},
        'inspect_sensor_recording': {'data_use': 'inspect', 'scene_use': None,
                                     'profile_fields': ['components','observation_schema','frames','units','time_model'],
                                     'require_known_profile': False,'accepted_rights_statuses': ['declared','verified'],
                                     'execution_mode': 'observation_replay'},
        'run_pymunk_basic': {'data_use': 'evaluate','code_use': 'evaluate','scene_use': None,'profile_fields': all_semantics,
                             'require_known_profile': True,'accepted_rights_statuses': ['verified'],
                             'execution_mode': 'closed_loop_simulation'},
    }
    if operation not in table:
        raise ValueError('Operation has no source authorization contract: ' + operation)
    value = deepcopy(table[operation])
    value['required_rights'] = {'data': value.pop('data_use'), 'code': value.pop('code_use',None),
                                'model': None, 'scene': value.pop('scene_use')}
    return value
