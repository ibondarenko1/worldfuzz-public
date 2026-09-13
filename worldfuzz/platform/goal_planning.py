"""WP22 deterministic typed-goal compiler over the shared planning service."""
import os

from .contracts import Fault


def provider_contract():
    """Describe the existing Nebius layer without loading a key or sending a request."""
    from ..nebius import DEFAULT_BASE_URL, MAX_COMPLETION_TOKENS, _nvidia_nemotron
    model=os.environ.get('NEBIUS_MODEL')
    return {'provider':'Nebius Token Factory','adapter':'worldfuzz.nebius.NemotronPlanner',
        'base_url':DEFAULT_BASE_URL,'configured_model':model if _nvidia_nemotron(model) else None,
        'model_identity_state':'configured' if _nvidia_nemotron(model) else 'unknown',
        'supported_model_family':'nvidia/*nemotron*','max_completion_tokens':MAX_COMPLETION_TOKENS,
        'completion_request_attempted':False,'request_state':'not_sent','usage_state':'not_applicable',
        'contract_validation_is_inference':False}


def _missing(path, reason, values=None):
    value={'path':path,'reason':reason}
    if values:value['accepted_values']=sorted(values)
    return value


def compile_goal(service, goal, provider_mode='disabled'):
    """Compile an already structured goal; provider mode is descriptive and zero-call."""
    provider=provider_contract()
    if service.identity:service.identity.check('compile_goal')
    if goal.get('workflow_kind')=='campaign':
        raise Fault('WORKFLOW_INCOMPATIBLE','A campaign requires a fixed case list and aggregate budget; standalone execution cannot satisfy it',
                    '$.goal.workflow_kind',next_action='create_campaign')
    provider['mode']=provider_mode
    descriptors=service.adapters.descriptors()
    operation=goal.get('operation');profile=goal.get('profile');missing=[]
    if profile is not None:
        descriptor=next((d for d in descriptors if d['profile']['id']==profile),None)
        if descriptor is None:raise Fault('PROFILE_INCOMPATIBLE','Goal profile is not enabled','$.goal.profile')
        operations=descriptor['profile']['operations']
        if operation is None:
            if len(operations)==1:operation=operations[0]
            else:missing.append(_missing('goal.operation','Profile supports multiple operations',operations))
        elif operation not in operations:
            raise Fault('PROFILE_INCOMPATIBLE','Goal operation is unsupported by its profile','$.goal.operation')
    elif operation is not None:
        profiles=[d['profile']['id'] for d in descriptors if operation in d['profile']['operations']]
        if not profiles:raise Fault('UNSUPPORTED_OPERATION','Goal operation has no enabled adapter','$.goal.operation')
        if len(profiles)==1:profile=profiles[0]
        else:missing.append(_missing('goal.profile','Operation has multiple compatible profiles',profiles))
    else:
        missing.extend([_missing('goal.operation','Choose the operation to perform',
            {op for d in descriptors for op in d['profile']['operations']}),
            _missing('goal.profile','Choose the embodiment/profile version',
            {d['profile']['id'] for d in descriptors})])
    parameters=goal.get('parameters',{})
    if profile is not None and operation is not None:
        schema=service.adapters.binding(profile)['descriptor']['request_schemas'][operation]
        for name in schema.get('required',[]):
            if name not in parameters:missing.append(_missing('goal.parameters.'+name,'Required by the selected adapter'))
    if missing:
        return {'status':'needs_information','missing_parameters':missing,'typed_request':None,
            'prepared_plan':None,'provider':provider,'provider_calls':0,
            'scope':'Goal shape only; no source access, reservation, model call or execution occurred'}
    typed={'operation':operation,'profile':profile,'parameters':parameters}
    for name in ('max_seconds','resource_limits','required_runner_capabilities','verification'):
        if name in goal:typed[name]=goal[name]
    prepared=service.invoke('prepare_plan',**typed)
    return {'status':'ready','missing_parameters':[],'typed_request':typed,'prepared_plan':prepared,
        'provider':provider,'provider_calls':0,
        'scope':'Typed request passed adapter schema, source compatibility, owner/data rights and runner budget checks; execution is separate'}
