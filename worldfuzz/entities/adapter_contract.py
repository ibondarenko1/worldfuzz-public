"""Target adapter contract and inspection-only conformance kit.

Separate from the working legacy registry v1. No implicit legacy promotion.
"""
from copy import deepcopy
from typing import Protocol
import inspect
from . import EntityError,validate_entity,_validate,_json
from .schemas import obj,arr,ID,TEXT,ref,MODE,DEFS,VERSION,PROPERTIES,schema
from ..platform.adapters import check_schema

CONTRACT_VERSION='2.0.0'
METHODS=('describe','inspect','check_compatibility','prepare','execute','collect','verify')
DESCRIPTOR=obj({'contract_version':{'const':CONTRACT_VERSION},'adapter_id':ID,'version':ID,
 'supported_profiles':arr(ref('ref'),1),'operations':arr(ID,1),
 'input_schema_refs':arr(obj({'operation':ID,'schema_ref':TEXT}),1),
 'output_schema_refs':arr(obj({'operation':ID,'schema_ref':TEXT}),1),
 'environment_lock':ref('ref'),'resource_profile':ref('limits'),'license':TEXT,
 'verification_method':ref('ref'),'execution_modes':arr(MODE,1),'limitations':arr(TEXT)})

class AdapterV2(Protocol):
    def describe(self) -> dict: ...
    def inspect(self,request:dict) -> list[dict]: ...
    def check_compatibility(self,request:dict) -> dict: ...
    def prepare(self,request:dict) -> dict: ...
    def execute(self,plan:dict,context) -> None: ...
    def collect(self,run_id:str) -> list[dict]: ...
    def verify(self,request:dict) -> list[dict]: ...

class UnsupportedOperation(EntityError):pass

def conformance(adapter,profiles,request_schemas,output_schemas):
    """Describe only. Does not invoke inspect/prepare/execute or certify outcomes."""
    for name in METHODS:
        if not callable(getattr(adapter,name,None)):raise EntityError('Missing adapter method: '+name)
        arguments=() if name=='describe' else ({},None) if name=='execute' else ({},)
        try:inspect.signature(getattr(adapter,name)).bind(*arguments)
        except TypeError as exc:raise EntityError('Wrong adapter method signature: '+name) from exc
    d=deepcopy(adapter.describe());_json(d);_validate(d,DESCRIPTOR,DEFS)
    if len(d['operations'])!=len(set(d['operations'])):raise EntityError('Duplicate operation')
    versions=set()
    for profile in profiles:
        validate_entity(profile,'EmbodimentProfile');key=(profile['profile_id'],profile['version'])
        if key in versions:raise EntityError('Duplicate profile version')
        versions.add(key)
    declared=[(r['id'],r['version']) for r in d['supported_profiles']]
    if len(declared)!=len(set(declared)) or set(declared)!=versions:raise EntityError('Profile references do not match validated profiles')
    for field in ('input_schema_refs','output_schema_refs'):
        ops=[r['operation'] for r in d[field]]
        if len(ops)!=len(set(ops)) or set(ops)!=set(d['operations']):raise EntityError('Every operation requires exactly one '+field)
    refs={r['schema_ref'] for r in d['input_schema_refs']}
    if refs!=set(request_schemas):raise EntityError('Input schema documents must be supplied exactly')
    for s in request_schemas.values():
        if s.get('type')!='object':raise EntityError('Operation input must be an object')
        check_schema(s)
    outrefs={r['schema_ref'] for r in d['output_schema_refs']}
    if outrefs!=set(output_schemas):raise EntityError('Output schema documents must be supplied exactly')
    catalog={schema(n)['$id']:schema(n) for n in PROPERTIES}
    for key,s in output_schemas.items():
        if key in catalog:
            if s!=catalog[key]:raise EntityError('Output entity schema differs from pinned version')
        else:check_schema(s)
    return {'contract_version':CONTRACT_VERSION,'adapter_id':d['adapter_id'],'metadata_valid':True,
            'profile_versions_valid':True,'execution_tested':False,'verification_tested':False,
            'limitations':['Metadata conformance is not runtime or semantic outcome certification']}

def require_operation(descriptor,operation):
    if operation not in descriptor['operations']:
        raise UnsupportedOperation('UNSUPPORTED_OPERATION: '+operation+'; supported: '+', '.join(descriptor['operations']))
