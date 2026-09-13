"""Exact semantic matching. No vector-shape or natural-language conversion guesses."""
from copy import deepcopy
import hashlib
import json
import math
from . import EntityError, validate_entity
from .schemas import VERSION

def check_profiles(required,offered,*,checked_at):
    validate_entity(required,'EmbodimentProfile');validate_entity(offered,'EmbodimentProfile')
    missing=list(dict.fromkeys(required['unknown_fields']+offered['unknown_fields']))
    conflicts=[];matched=[]
    def summary(value):
        encoded=json.dumps(value,sort_keys=True)
        return encoded if len(encoded)<=4096 else 'sha256:'+hashlib.sha256(encoded.encode()).hexdigest()
    # Identity does not substitute for explicit signal/frame/time semantics.
    for field in ('components','state_schema','action_schema','observation_schema','frames','units','time_model'):
        if required[field]!=offered[field]:
            conflicts.append({'field':field,'expected':summary(required[field]),
                              'observed':summary(offered[field]),
                              'reason':'Exact semantics differ; explicit versioned conversion required'})
        else:matched.append(field)
    status='incompatible' if conflicts else 'needs_information' if missing else 'compatible'
    body={'entity_type':'CompatibilityDecision','schema_version':VERSION,'decision_id':'pending','status':status,
          'input_versions':[{'id':p['profile_id'],'version':p['version']} for p in (required,offered)],
          'matched_requirements':matched,'missing_fields':missing,'conflicts':conflicts,'evidence_refs':[],
          'checked_at':checked_at}
    body['decision_id']=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    validate_entity(body,'CompatibilityDecision')
    return deepcopy(body)


def check_compatibility(required, offered, assets, *, required_rights,
                        required_resources, offered_resources, checked_at,
                        required_profile_fields=None, require_known_profile=True):
    """Check exact profile semantics, declared rights and finite resources.

    ``required_rights`` must name all four subjects and explicitly uses ``None``
    when a subject does not participate in this operation. This avoids inferring
    that model or scene rights apply merely because their fields exist.
    """
    validate_entity(required, 'EmbodimentProfile')
    validate_entity(offered, 'EmbodimentProfile')
    for asset in assets:
        validate_entity(asset, 'AssetVersion')
    subjects = {'data', 'code', 'model', 'scene'}
    if set(required_rights) != subjects:
        raise EntityError('Required rights must explicitly name data, code, model and scene')
    uses = {'inspect', 'evaluate', 'train', 'export', 'redistribute'}
    if any(value is not None and value not in uses for value in required_rights.values()):
        raise EntityError('Unsupported required rights use')
    limits = {'wall_seconds', 'cpu_seconds', 'memory_bytes', 'artifact_bytes', 'model_calls', 'device_calls'}
    if set(required_resources) != limits or set(offered_resources) != limits:
        raise EntityError('Resource profiles must contain the complete limits contract')
    for profile in (required_resources, offered_resources):
        for name, value in profile.items():
            nullable = name in ('cpu_seconds', 'memory_bytes')
            integer = name in ('memory_bytes', 'artifact_bytes', 'model_calls', 'device_calls')
            if value is None and nullable:
                continue
            if (type(value) not in ((int,) if integer else (int, float)) or
                    not math.isfinite(value) or value < 0):
                raise EntityError('Invalid resource value: ' + name)
    refs = {(asset['asset_id'], asset['version']) for asset in assets}
    if len(refs) != len(assets):
        raise EntityError('Duplicate asset version')

    profile_fields = ('components', 'state_schema', 'action_schema', 'observation_schema',
                      'frames', 'units', 'time_model')
    if required_profile_fields is None:
        required_profile_fields = list(profile_fields)
    if (type(required_profile_fields) is not list or
            not set(required_profile_fields) <= set(profile_fields) or
            len(set(required_profile_fields)) != len(required_profile_fields)):
        raise EntityError('Required profile fields must be a unique explicit subset')
    if type(require_known_profile) is not bool:
        raise EntityError('require_known_profile must be boolean')

    conflicts = []
    missing = (list(dict.fromkeys(required['unknown_fields'] + offered['unknown_fields']))
               if require_known_profile else [])
    matched = []
    for field in required_profile_fields:
        if required[field] == offered[field]:
            matched.append('profile.' + field)
        else:
            conflicts.append({'field': 'profile.' + field,
                              'expected': summary_value(required[field]),
                              'observed': summary_value(offered[field]),
                              'reason': 'Exact semantics differ; explicit versioned conversion required'})
    if not require_known_profile:
        matched.append('profile.unknown_fields.preserved_not_required')
    for name in sorted(limits):
        wanted = required_resources[name]
        available = offered_resources[name]
        if wanted is None:
            missing.append('resource.' + name + '.required')
        elif available is None:
            missing.append('resource.' + name + '.offered')
        elif available < wanted:
            conflicts.append({'field': 'resource.' + name, 'expected': wanted,
                              'observed': available, 'reason': 'Offered resource limit is below requirement'})
        else:
            matched.append('resource.' + name)
    for asset in assets:
        rights = {item['subject']: item for item in asset['rights']}
        for subject in sorted(subjects):
            use = required_rights[subject]
            field = f"rights.{asset['asset_id']}@{asset['version']}.{subject}"
            if use is None:
                matched.append(field + '.not_required')
                continue
            right = rights[subject]
            if right['status'] == 'unknown':
                missing.append(field)
            elif right['status'] == 'conflict' or use not in right['allowed_uses']:
                conflicts.append({'field': field, 'expected': use,
                                  'observed': right['status'],
                                  'reason': 'Declared rights do not allow the required use'})
            else:
                matched.append(field + '.' + use)
        if asset['access_status'] != 'file_verified':
            missing.append(f"asset.{asset['asset_id']}@{asset['version']}.verified_bytes")

    status = 'incompatible' if conflicts else 'needs_information' if missing else 'compatible'
    body = {'entity_type': 'CompatibilityDecision', 'schema_version': VERSION,
            'decision_id': 'pending', 'status': status,
            'input_versions': [{'id': asset['asset_id'], 'version': asset['version']} for asset in assets],
            'matched_requirements': matched, 'missing_fields': missing,
            'conflicts': conflicts, 'evidence_refs': [], 'checked_at': checked_at}
    body['decision_id'] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    validate_entity(body, 'CompatibilityDecision')
    return deepcopy(body)


def summary_value(value):
    """Represent large structured values inside the scalar decision contract."""
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
    return encoded if len(encoded) <= 4096 else 'sha256:' + hashlib.sha256(encoded.encode()).hexdigest()
