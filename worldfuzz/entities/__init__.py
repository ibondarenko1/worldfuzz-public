"""Strict versioned product entities, additive to the legacy v1 service payloads."""
import hashlib
import json
import math
import re
from .schemas import VERSION, PROPERTIES, schema

class EntityError(ValueError):
    def __init__(self,message,path='$'):
        self.path=path
        super().__init__(path+': '+message)

def _json(value,path='$',depth=0):
    if depth>48:raise EntityError('Maximum nesting exceeded',path)
    if value is None or type(value) in (bool,str,int):return
    if type(value) is float:
        if not math.isfinite(value):raise EntityError('Non-finite number',path)
        return
    if type(value) is list:
        for i,v in enumerate(value):_json(v,f'{path}[{i}]',depth+1)
        return
    if type(value) is dict:
        for k,v in value.items():
            if type(k) is not str:raise EntityError('JSON keys must be strings',path)
            _json(v,path+'.'+k,depth+1)
        return
    raise EntityError('Not a JSON value',path)

def _validate(value,s,defs,path='$'):
    if '$ref' in s:
        return _validate(value,defs[s['$ref'].removeprefix('#/$defs/')],defs,path)
    if 'anyOf' in s:
        for branch in s['anyOf']:
            try:_validate(value,branch,defs,path);return
            except EntityError:pass
        raise EntityError('Value does not match any allowed type',path)
    if 'const' in s and (type(value) is not type(s['const']) or value!=s['const']):raise EntityError('Wrong constant or schema version',path)
    kind=s.get('type')
    valid={'object':type(value) is dict,'array':type(value) is list,'string':type(value) is str,
           'number':type(value) in (int,float),'integer':type(value) is int or type(value) is float and value.is_integer(),
           'boolean':type(value) is bool,'null':value is None}
    if kind and not valid[kind]:raise EntityError('Expected '+kind,path)
    if 'enum' in s and value not in s['enum']:raise EntityError('Unsupported enum value',path)
    if kind=='object':
        for key in s['required']:
            if key not in value:raise EntityError('Missing required field',path+'.'+key)
        for key,item in value.items():
            if key not in s['properties']:raise EntityError('Unknown field',path+'.'+key)
            _validate(item,s['properties'][key],defs,path+'.'+key)
    elif kind=='array':
        if not s.get('minItems',0)<=len(value)<=s.get('maxItems',10000):raise EntityError('Array length outside limits',path)
        for i,item in enumerate(value):_validate(item,s['items'],defs,f'{path}[{i}]')
    elif kind=='string':
        if not s.get('minLength',0)<=len(value)<=s.get('maxLength',4096):raise EntityError('Text length outside limits',path)
        if 'pattern' in s and re.search(s['pattern'],value) is None:raise EntityError('Invalid identifier or digest',path)
    elif kind in ('integer','number'):
        if value<s.get('minimum',-math.inf) or value>s.get('maximum',math.inf):raise EntityError('Number outside limits',path)

def validate_entity(value,expected_type=None):
    _json(value)
    if type(value) is not dict:raise EntityError('Expected entity object')
    name=value.get('entity_type')
    if type(name) is not str or name not in PROPERTIES:raise EntityError('Unknown entity_type')
    if expected_type and expected_type!=name:raise EntityError('Unexpected entity_type')
    s=schema(name);_validate(value,s,s['$defs'])
    _semantics(name,value)
    return value

def _unique(items,key,label):
    seen=set()
    for item in items:
        val=item[key]
        if val in seen:raise EntityError('Duplicate '+label)
        seen.add(val)

def _semantics(name,v):
    if 'rights' in v:
        _unique(v['rights'],'subject','rights subject')
        if {r['subject'] for r in v['rights']}!={'data','code','model','scene'}:raise EntityError('All four rights subjects are required')
        for r in v['rights']:
            if r['status']=='unknown' and r['allowed_uses']:raise EntityError('Unknown rights cannot authorize uses')
            if r['status']=='verified' and (not r['license'] or not r['evidence_urls']):raise EntityError('Verified rights require license and evidence')
    if name=='AssetVersion':
        if v['access_status']=='documentation_only' and (v['observed_hash_or_null'] is not None or v['size_or_null'] is not None):raise EntityError('Documentation-only asset cannot claim checked bytes')
        if v['access_status']=='file_verified' and (v['observed_hash_or_null'] is None or v['size_or_null'] is None):raise EntityError('Verified file requires hash and size')
    if name=='CompatibilityDecision':
        if v['status']=='compatible' and (v['missing_fields'] or v['conflicts']):raise EntityError('Compatible decision has unresolved requirements')
        if v['status']=='needs_information' and not v['missing_fields']:raise EntityError('Missing information must be named')
        if v['status']=='incompatible' and not v['conflicts']:raise EntityError('Conflict must be named')
    if name=='Outcome':
        if v['verdict']=='unknown' and not v['unknown_reason']:raise EntityError('Unknown outcome needs a reason')
        if v['verdict'] in ('pass','fail') and (not v['evidence_refs'] or v['basis']=='none' or v['unknown_reason'] is not None):raise EntityError('Decided outcome requires a basis and evidence')
    if name=='Evidence' and v['kind']=='physical_measurement' and v['issuer']['kind']!='physical_observer':raise EntityError('Local or publisher evidence cannot become a physical measurement')
    if name=='SplitManifest':_unique(v['group_assignments'],'group_id','group assignment')
    if name=='Comparison':
        if v['comparable'] and v['mismatches']:raise EntityError('Comparable result cannot have protocol mismatches')
        if v['coverage']['paired']>v['coverage']['requested']:raise EntityError('Paired coverage exceeds requested cases')
    if name=='Run':
        if v['finished_at'] is not None and v['started_at'] is not None and v['finished_at']<v['started_at']:raise EntityError('Finish precedes start')
        if v['status'] in ('queued','running','cancel_requested') and v['finished_at'] is not None:raise EntityError('Active run cannot have a finish time')
    if name=='EmbodimentProfile':
        _unique(v['frames'],'frame_id','frame');frames={f['frame_id'] for f in v['frames']}
        parents={f['frame_id']:f['parent'] for f in v['frames']}
        for frame in frames:
            visited=set();node=frame
            while node is not None:
                if node not in parents or node in visited:raise EntityError('Invalid frame parent or cycle')
                visited.add(node);node=parents[node]
        for signal in v['state_schema']+(v['action_schema'] or [])+v['observation_schema']:
            if signal['frame'] is not None and signal['frame'] not in frames:raise EntityError('Signal refers to unknown frame')
            if signal['unit'] is not None and signal['unit'] not in v['units']:raise EntityError('Signal refers to unknown unit')
    if name=='RunPlan':
        _unique(v['steps'],'step_id','plan step');seen=set()
        for step in v['steps']:
            if not set(step['depends_on'])<=seen:raise EntityError('Plan steps require earlier dependencies')
            seen.add(step['step_id'])
        if v['digest']!=plan_digest(v):raise EntityError('Plan digest mismatch')

def plan_digest(value):
    # plan_id is an opaque locator, never an authorization boundary.
    body={k:v for k,v in value.items() if k not in ('plan_id','digest')}
    _json(body)
    return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()

def loads(raw,expected_type=None):
    if len(raw.encode('utf-8'))>2*1024*1024:raise EntityError('Entity exceeds 2 MiB')
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise EntityError('Duplicate JSON field: '+k)
            d[k]=v
        return d
    try:value=json.loads(raw,object_pairs_hook=pairs)
    except (ValueError,RecursionError) as exc:raise EntityError('Invalid JSON: '+str(exc)) from exc
    return validate_entity(value,expected_type)
