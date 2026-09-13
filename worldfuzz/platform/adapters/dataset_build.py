"""WP21 immutable grouped generations; source data never changes in place."""
from copy import deepcopy
import hashlib
from importlib import metadata
import io
import json
from pathlib import Path
import tempfile
import zipfile

from ...corpus import digest, encoded
from ...entities import validate_entity
from ..contracts import Fault, ID, obj
from ..source_contracts import m1_contract, requirements

OPERATION = 'build_dataset'
PROFILES = {'dataset.can.lerobot.v3': ('manipulation', 'lerobot.v3.0'),
            'dataset.navigation.jsonl.v1': ('navigation', 'worldfuzz.jsonl.v1')}
BUILD_SCHEMA = obj({'episode_ids': {'type':'array','items':ID,'minItems':1,'maxItems':200},
    'purpose': {'type':'string','enum':['training','evaluation']},
    'normalization_split': {'type':'string','enum':['train']}}, ['episode_ids','purpose','normalization_split'])
PINS = {'lerobot':'0.6.1','pyarrow':'23.0.1','datasets':'4.8.4','torch':'2.10.0','numpy':'2.2.6'}


def selected(service, request, domain):
    ids = request['episode_ids']
    if len(set(ids)) != len(ids):raise Fault('SCHEMA_INVALID','Duplicate episode IDs')
    listing = {item['id']:item for item in service.corpus.listing()['episodes']}
    if any(i not in listing or listing[i]['domain'] != domain for i in ids):
        raise Fault('PROFILE_INCOMPATIBLE','Select existing episodes from one explicit domain')
    groups = {listing[i]['group_id'] for i in ids}
    whole = {i for i,item in listing.items() if item['group_id'] in groups}
    if set(ids) != whole:raise Fault('GROUP_INCOMPLETE','Selection must include every member of each linked group')
    if request['purpose']=='training' and any(listing[i]['split']!='train' for i in ids):
        raise Fault('HELD_OUT_PROTECTED','Training generation may contain only the original train split')
    if request.get('normalization_split')!='train':
        raise Fault('HELD_OUT_PROTECTED','Normalization may use train episodes only')
    return sorted(ids,key=lambda i: (['train','validation','test'].index(listing[i]['split']),listing[i]['group_id'],i))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(encoded(value).encode())


def checksum(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def archive(root):
    result = io.BytesIO()
    with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                entry = zipfile.ZipInfo(path.relative_to(root).as_posix(), (1980,1,1,0,0,0))
                entry.compress_type=zipfile.ZIP_DEFLATED; entry.external_attr=0o600<<16
                bundle.writestr(entry,path.read_bytes())
    return result.getvalue()


def unpack(raw, root):
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        members=bundle.infolist()
        if len(members)>1000 or sum(i.file_size for i in members)>128*1024**2:
            raise Fault('INTEGRITY_FAILED','Dataset archive exceeds its extraction bound')
        for item in members:
            target=(root/item.filename).resolve()
            if not target.is_relative_to(root.resolve()) or '\\' in item.filename or item.is_dir():
                raise Fault('INTEGRITY_FAILED','Invalid dataset archive member')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(bundle.read(item))


class DatasetAdapter:
    def __init__(self, profile):self.profile=profile;self.domain,self.format=PROFILES[profile]

    def describe(self):
        source=m1_contract(self.domain); policy=requirements('export_dataset')
        if self.domain=='manipulation':
            from .can_replay import replay_contract
            source=replay_contract()
        return {'adapter_id':self.profile,'version':'1.0.0','contract_version':'1.0.0','license':'MIT',
            'profile':{'id':self.profile,'domain':self.domain,'state':'preserved source coordinates',
                'action':'preserved source actions','observation':'named original observations, never full simulator state',
                'outcome':'verified dataset round trip, not robotics execution', 'operations':[OPERATION],
                'execution':'metadata_inspection','data_license':source['asset']['rights'][0]['license'],'code_license':'MIT'},
            'execution_mode':'metadata_inspection',
            'source_contract':({'kind':'can_replay_v1','operations':{OPERATION:policy}}
                if self.domain=='manipulation' else {'kind':'m1','domain':self.domain,
                'asset_ref':{'id':source['asset']['asset_id'],'version':source['asset']['version']},
                'profile_ref':{'id':source['profile']['profile_id'],'version':source['profile']['version']},
                'operations':{OPERATION:policy}}),
            'environment_lock':{'runtime':'stdlib' if self.domain=='navigation' else '; '.join(k+'=='+v for k,v in PINS.items())},
            'limitations':['Single compatible domain per generation; groups and held-out split immutable',
                'Source and owner export rights checked; no model training or publication',
                'LeRobot consumer must use float64 for exact source-value round trip'],
            'request_schemas':{OPERATION:BUILD_SCHEMA}}

    def resources(self, service):return []

    def snapshot(self, service, request):
        ids=selected(service,request,self.domain)
        if self.domain=='manipulation':
            for package, version in PINS.items():
                try:actual=metadata.version(package)
                except metadata.PackageNotFoundError:raise Fault('RUNNER_UNSUPPORTED','Install pinned LeRobot reader runtime') from None
                if actual!=version:raise Fault('RUNNER_UNSUPPORTED',package+' must be '+version)
        source=m1_contract(self.domain); episodes=[]
        if self.domain=='manipulation':
            from .can_replay import replay_contract
            source=replay_contract()
        rights=next(r for r in source['asset']['rights'] if r['subject']=='data')
        if request['purpose']=='training' and 'train' not in rights['allowed_uses']:
            raise Fault('ACCESS_DENIED','Source rights do not allow a training generation')
        for identifier in ids:
            item=service._episode(identifier,self.domain)
            if self.domain=='manipulation' and item['source'].get('sha256')!=source['asset']['observed_hash_or_null']:
                raise Fault('PROFILE_INCOMPATIBLE','Can source bytes differ from M1')
            episodes.append({'id':identifier,'payload_sha256':item['payload_sha256'],'group_id':item['episode']['group_id'],
                'split':item['split'],'source_sha256':digest(item['source'])})
        transform_files=[Path(__file__)]
        if self.domain=='manipulation':transform_files.append(Path(__file__).with_name('lerobot_v3.py'))
        transform_hash=digest({p.name:checksum(p) for p in transform_files})
        body={'domain':self.domain,'format':self.format,'episodes':episodes,'purpose':request['purpose'],
            'normalization_split':'train','source_rights':source['asset']['rights'],
            'transform_version':transform_hash,'format_runtime':PINS if self.domain=='manipulation' else {}}
        return {**body,'generation_version':digest(body)}

    def execute(self,service,plan,records,artifacts,check,artifact):
        snapshot=plan['snapshot']; source=m1_contract(self.domain)
        with tempfile.TemporaryDirectory(prefix='worldfuzz-generation-') as tmp:
            root=Path(tmp); items=[]
            # Read one original episode at a time; never open HDF5 or restore M1.
            if self.domain=='manipulation':
                from .lerobot_v3 import write_can
                write_can(service,snapshot,root,check)
            else:
                path=root/'episodes.jsonl'
                with path.open('w',encoding='utf-8',newline='\n') as stream:
                    for pinned in snapshot['episodes']:
                        check();stream.write(encoded(service._episode(pinned['id'],self.domain))+'\n')
            groups={p['group_id']:p['split'] for p in snapshot['episodes']}
            version=snapshot['generation_version']
            split={'entity_type':'SplitManifest','schema_version':'1.0.0','split_id':'split-'+version,'version':'1.0.0',
                'grouping_rule':{'id':'original-corpus-groups','version':'1.0.0'},
                'group_assignments':[{'group_id':k,'split':v} for k,v in sorted(groups.items())],
                'source_lineage':plan['contract_plan']['input_versions'], 'seed_or_rule':'original immutable groups; no reassignment',
                'heldout_policy':{'training_allowed':snapshot['purpose']=='training','automatic_reassignment':False,
                    'description':'Only original train records may train or compute normalization. Evaluation/test inclusion grants no training use.'}}
            manifest={'entity_type':'DatasetManifest','schema_version':'1.0.0','dataset_id':'dataset-'+version,'version':'1.0.0',
                'episode_refs':[{'id':p['id'],'version':'1.0.0'} for p in snapshot['episodes']],
                'split_ref':{'id':split['split_id'],'version':split['version']},
                'filters':[{'name':'purpose','value':snapshot['purpose'],'unit':None},
                    {'name':'normalization_split','value':'train','unit':None}],
                'transform_refs':[{'id':self.profile,'version':snapshot['transform_version']}],
                'rights':deepcopy(snapshot['source_rights']),'target_schema':self.format,
                'checksums':[{'locator':p.relative_to(root).as_posix(),'sha256':checksum(p)} for p in sorted(root.rglob('*')) if p.is_file()]}
            validate_entity(split);validate_entity(manifest)
            write_json(root/'worldfuzz'/'split.json',split);write_json(root/'worldfuzz'/'manifest.json',manifest)
            artifact('dataset.zip',archive(root));artifact('dataset-manifest.json',encoded(manifest).encode())
            records.append({'dataset_manifest':manifest,'split_manifest':split,'generation_version':version,
                'episodes':len(snapshot['episodes']),'groups':len(groups),'training_performed':False,
                'evidence_scope':'pending independent dataset round trip','physics_replayed':False})


def verify_generation(service,run_id,plan,records,artifacts,check):
    raw=service.artifact(run_id,'dataset.zip',result={'artifacts':artifacts})
    with tempfile.TemporaryDirectory(prefix='worldfuzz-read-generation-') as tmp:
        root=Path(tmp);unpack(raw,root)
        manifest=json.loads((root/'worldfuzz/manifest.json').read_text());split=json.loads((root/'worldfuzz/split.json').read_text())
        validate_entity(manifest);validate_entity(split)
        if manifest!=records[0]['dataset_manifest'] or split!=records[0]['split_manifest']:
            raise Fault('VERIFICATION_FAILED','Dataset manifest differs from recorded result')
        for item in manifest['checksums']:
            check()
            path=(root/item['locator']).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file() or checksum(path)!=item['sha256']:
                raise Fault('INTEGRITY_FAILED','Dataset shard checksum differs')
        expected=plan['snapshot']['episodes']
        if manifest['episode_refs'] != [{'id':p['id'],'version':'1.0.0'} for p in expected]:
            raise Fault('VERIFICATION_FAILED','Dataset episode versions differ from accepted M1 entities')
        expected_groups={p['group_id']:p['split'] for p in expected}
        if {p['group_id']:p['split'] for p in split['group_assignments']} != expected_groups:
            raise Fault('VERIFICATION_FAILED','Dataset group or held-out assignment changed')
        if manifest['rights'] != plan['snapshot']['source_rights']:
            raise Fault('VERIFICATION_FAILED','Dataset rights differ from the pinned source rights')
        if plan['snapshot']['domain']=='manipulation':
            from .lerobot_v3 import verify_can
            details=verify_can(service,plan['snapshot'],root,check)
        else:
            exported=[json.loads(s) for s in (root/'episodes.jsonl').read_text().splitlines()]
            for item,pinned in zip(exported,plan['snapshot']['episodes'],strict=True):
                check()
                if item!=service._episode(pinned['id'],'navigation'):raise Fault('VERIFICATION_FAILED','Navigation export changed values')
            details={'reader':'WorldFuzz JSONL','episodes':len(exported),'exact_roundtrip':True}
        records[0].update(roundtrip=details,evidence_scope='source bytes and typed dataset round trip; no new robotics evidence')


def get_generation(service, result_id):
    result=service.get_result(result_id)
    if result['result'].get('operation')!='build_dataset' or result['result']['execution_status']!='completed':
        raise Fault('RESULT_NOT_READY','A verified, completed dataset build is required')
    return {'result_id':result_id,'dataset_manifest':result['result']['records'][0]['dataset_manifest'],
            'split_manifest':result['result']['records'][0]['split_manifest'],
            'roundtrip':result['result']['records'][0]['roundtrip']}
