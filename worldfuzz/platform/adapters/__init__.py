"""Versioned, explicitly enabled local adapter registry.

Adapters are trusted installed Python packages, not sandboxed uploaded code.
Dataset contents and API requests never select Python imports.
"""
from copy import deepcopy
import hashlib
import inspect
from importlib import metadata
import os
from pathlib import Path
from typing import Protocol
from ...corpus import digest
from ..contracts import Fault, validate


class Adapter(Protocol):
    def describe(self) -> dict: ...
    def resources(self, service) -> list: ...
    def snapshot(self, service, request: dict) -> dict: ...
    def execute(self, service, plan, records, artifacts, check, artifact) -> None: ...


def check_schema(schema):
    """Reject schema keywords unsupported by the runtime instead of ignoring them."""
    allowed = {'type','properties','required','additionalProperties','items','enum',
               'minLength','maxLength','minimum','maximum','minItems','maxItems','description'}
    if not isinstance(schema,dict) or set(schema)-allowed:
        raise Fault('ADAPTER_INVALID','Unsupported request schema keyword')
    if 'description' in schema and (not isinstance(schema['description'],str) or len(schema['description'])>4096):
        raise Fault('ADAPTER_INVALID','Schema description must be text of at most 4096 characters')
    if schema.get('type') not in ('object','array','string','number','integer','boolean'):
        raise Fault('ADAPTER_INVALID','Every adapter field requires a supported type')
    if schema['type']=='object':
        props=schema.get('properties')
        if not isinstance(props,dict) or schema.get('additionalProperties') is not False:
            raise Fault('ADAPTER_INVALID','Adapter objects must declare properties and reject unknown fields')
        if not set(schema.get('required',[])) <= set(props):
            raise Fault('ADAPTER_INVALID','Required field absent from properties')
        for child in props.values():check_schema(child)
    if schema['type']=='array':check_schema(schema.get('items'))


def _check_descriptor(adapter):
    for method in ('describe','resources','snapshot','execute'):
        if not callable(getattr(adapter,method,None)):
            raise Fault('ADAPTER_INVALID','Missing adapter method: '+method)
    d=deepcopy(adapter.describe())
    required={'adapter_id','version','contract_version','profile','license','execution_mode','source_contract',
              'environment_lock','limitations','request_schemas'}
    if not required<=d.keys() or d['contract_version']!='1.0.0':
        raise Fault('ADAPTER_INVALID','Adapter descriptor or contract version unsupported')
    for key in ('adapter_id','version','license','execution_mode'):
        validate(d[key],{'type':'string','minLength':1,'maxLength':256})
    p=d['profile']
    if not isinstance(p,dict) or not {'id','domain','state','action','observation','outcome','operations'}<=p.keys():
        raise Fault('ADAPTER_INVALID','Profile lacks signal or outcome semantics')
    validate(p['id'],{'type':'string','minLength':1,'maxLength':256})
    if not isinstance(p['operations'],list) or not p['operations'] or len(set(p['operations']))!=len(p['operations']):
        raise Fault('ADAPTER_INVALID','Operations must be unique and nonempty')
    if set(d['request_schemas'])!=set(p['operations']):
        raise Fault('ADAPTER_INVALID','Every operation requires an exact input schema')
    for schema in d['request_schemas'].values():
        if schema.get('type')!='object':raise Fault('ADAPTER_INVALID','Operation inputs must be objects')
        check_schema(schema)
    source=d['source_contract']
    if not isinstance(source,dict) or source.get('kind') not in ('m1','registered_trajectory_v1','registered_sensor_v1','external_engine_v1','can_replay_v1'):
        raise Fault('ADAPTER_INVALID','Adapter requires an explicit supported source contract')
    if source['kind']=='m1':
        if source.get('domain')!=p['domain'] or set(source)!={'kind','domain','asset_ref','profile_ref','operations'}:
            raise Fault('ADAPTER_INVALID','M1 source contract requires exact domain and version references')
        for reference in (source['asset_ref'],source['profile_ref']):
            if not isinstance(reference,dict) or set(reference)!={'id','version'}:
                raise Fault('ADAPTER_INVALID','M1 source reference is malformed')
            for value in reference.values():validate(value,{'type':'string','minLength':1,'maxLength':256})
    elif set(source)!={'kind','operations'}:
        raise Fault('ADAPTER_INVALID','Registered source contract has unknown fields')
    policies=source.get('operations')
    if not isinstance(policies,dict) or set(policies)!=set(p['operations']):
        raise Fault('ADAPTER_INVALID','Every adapter operation requires a source policy')
    subjects={'data','code','model','scene'}
    profile_fields={'components','state_schema','action_schema','observation_schema','frames','units','time_model'}
    uses={'inspect','evaluate','train','export','redistribute'}
    modes={'metadata_inspection','offline_evaluation','observation_replay','closed_loop_simulation','external_result_ingestion'}
    for policy in policies.values():
        if not isinstance(policy,dict) or set(policy)!={'required_rights','profile_fields','require_known_profile','accepted_rights_statuses','execution_mode'}:
            raise Fault('ADAPTER_INVALID','Operation source policy is malformed')
        rights=policy['required_rights']
        if not isinstance(rights,dict) or set(rights)!=subjects or any(v is not None and v not in uses for v in rights.values()):
            raise Fault('ADAPTER_INVALID','Operation source rights are incomplete or unsupported')
        fields=policy['profile_fields']
        if not isinstance(fields,list) or len(fields)!=len(set(fields)) or not set(fields)<=profile_fields:
            raise Fault('ADAPTER_INVALID','Operation profile requirements are invalid')
        statuses=policy['accepted_rights_statuses']
        if not isinstance(statuses,list) or not statuses or len(statuses)!=len(set(statuses)) or not set(statuses)<={'declared','verified'}:
            raise Fault('ADAPTER_INVALID','Operation source-rights statuses are invalid')
        if type(policy['require_known_profile']) is not bool or policy['execution_mode'] not in modes:
            raise Fault('ADAPTER_INVALID','Operation semantic or execution requirement is invalid')
    digest(d)  # Must be finite, JSON-serializable metadata.
    return d


def check_descriptor(adapter):
    try:
        return _check_descriptor(adapter)
    except Fault:
        raise
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise Fault('ADAPTER_INVALID','Malformed adapter descriptor: '+type(exc).__name__) from exc


class Registry:
    def __init__(self, adapters=()):
        self._adapters={};self._descriptors={};self._pins={};self._frozen=None
        for adapter in adapters:self.add(adapter)

    def add(self, adapter, pin=None):
        if self._frozen is not None:raise Fault('ADAPTER_INVALID','Registry is frozen; restart to change adapters')
        d=check_descriptor(adapter); key=d['profile']['id']
        if key in self._adapters or any(x['adapter_id']==d['adapter_id'] for x in self._descriptors.values()):
            raise Fault('ADAPTER_CONFLICT','Duplicate profile or adapter identity')
        self._adapters[key]=adapter;self._descriptors[key]=d;self._pins[key]=pin or {}

    def resolve(self, profile, operation=None):
        if profile not in self._adapters:raise Fault('PROFILE_INCOMPATIBLE','Profile is not enabled', '$.profile')
        if operation and operation not in self._descriptors[profile]['profile']['operations']:
            raise Fault('PROFILE_INCOMPATIBLE','Operation unsupported for this profile', '$.operation')
        return self._adapters[profile]

    def descriptors(self):
        return [deepcopy(self._descriptors[k]) for k in sorted(self._descriptors)]

    def binding(self, profile):
        self.resolve(profile)
        return {'descriptor':deepcopy(self._descriptors[profile]), 'package':deepcopy(self._pins[profile])}

    def profiles(self):
        return [d['profile'] for d in self.descriptors()]

    def validate_request(self, request):
        self.resolve(request['profile'],request['operation'])
        schema=self._descriptors[request['profile']]['request_schemas'][request['operation']]
        common=set(common_fields())
        payload={k:v for k,v in request.items() if k not in common}
        if 'parameters' in payload:
            if set(payload)!={'parameters'}:raise Fault('SCHEMA_INVALID','Do not mix parameters and legacy flat fields')
            payload=payload['parameters']
        validate(payload,schema,'$.parameters' if 'parameters' in request else '$')
        return payload

    def normalized(self, request):
        payload=self.validate_request(request)
        # Plugins receive their values in parameters. Builtins keep their established fields.
        if self._pins[request['profile']]:
            return {k:request[k] for k in common_fields() if k in request} | {'parameters':payload}
        return {k:request[k] for k in common_fields() if k in request} | payload

    def identity(self):
        result={}
        for key,adapter in self._adapters.items():
            path=inspect.getsourcefile(type(adapter))
            if not path:raise Fault('ADAPTER_INVALID','Adapter requires inspectable Python source')
            files={'implementation':hashlib.sha256(Path(path).read_bytes()).hexdigest()}
            pin=self._pins[key]
            if pin:
                dist=metadata.distribution(pin['distribution'])
                if dist.version!=pin['version']:raise Fault('PLAN_CHANGED','Installed adapter version changed')
                # Includes metadata, code and declared package data. Excludes generated bytecode.
                for entry in sorted(dist.files or [],key=str):
                    if str(entry).endswith(('.pyc','.pyo')):continue
                    resolved=Path(dist.locate_file(entry))
                    if resolved.is_file():files[str(entry)]=hashlib.sha256(resolved.read_bytes()).hexdigest()
            result[key]={'descriptor':self._descriptors[key],'pin':pin,'files':files}
        current=digest(result)
        if self._frozen is not None and current!=self._frozen:
            raise Fault('PLAN_CHANGED','Adapter files changed; restart before preparing new plans')
        return current

    def freeze(self):
        self._frozen=self.identity()
        return self


def load_registry(*, include_plugins=True):
    from .builtin import builtins
    registry=Registry(builtins())
    from .can_replay import CanReplayAdapter
    registry.add(CanReplayAdapter())
    from .dataset_build import DatasetAdapter, PROFILES as DATASET_PROFILES
    for profile in DATASET_PROFILES:
        registry.add(DatasetAdapter(profile))
    enabled=os.environ.get('WORLDFUZZ_ADAPTERS','').strip() if include_plugins else ''
    if not enabled:return registry.freeze()
    entries=list(metadata.entry_points(group='worldfuzz.adapters'))
    for spec in enabled.split(','):
        name,separator,version=spec.strip().partition('==')
        if not separator or not name or not version:
            raise Fault('ADAPTER_INVALID','Enable adapters as entrypoint-name==distribution-version')
        matches=[e for e in entries if e.name==name]
        if len(matches)!=1 or matches[0].dist.version!=version:
            raise Fault('ADAPTER_INVALID','Enabled adapter is absent, ambiguous or has a different version')
        entry=matches[0]
        registry.add(entry.load()(), {'entrypoint':name,'distribution':entry.dist.metadata['Name'], 'version':version})
    return registry.freeze()


def common_fields():
    return ('profile','operation','max_seconds','resource_limits','required_runner_capabilities','verification')
