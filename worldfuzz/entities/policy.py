"""Local policy decision gate. Grants originate from a trusted owner configuration.

This module is the WP04 contract implementation, not organizational identity
management. A submitted grant document cannot register or authorize itself.
"""
from copy import deepcopy
import math
import time
from . import EntityError,validate_entity

class PolicyDenied(EntityError):pass

class PolicyAuthority:
    def __init__(self,grants=(),*,asset_resolver,decision_resolver,clock=time.time):
        self._clock=clock;self._grants={};self._asset_resolver=asset_resolver;self._decision_resolver=decision_resolver
        for grant in grants:
            validate_entity(grant,'PolicyGrant')
            if grant['grant_id'] in self._grants:raise EntityError('Duplicate grant id')
            self._grants[grant['grant_id']]=deepcopy(grant)

    def revoke(self,grant_id):self._grants.pop(grant_id,None)

    def authorize(self,plan,*,grant_id,workspace_id,principal_id,action,
                  data_use=None,accepted_rights_statuses=('verified',)):
        validate_entity(plan,'RunPlan')
        decision=deepcopy(self._decision_resolver(deepcopy(plan['rights_decision'])))
        validate_entity(decision,'CompatibilityDecision')
        grant=self._grants.get(grant_id)
        if grant is None:raise PolicyDenied('Grant not issued by this authority')
        now=self._clock()
        if type(now) not in (int,float) or not math.isfinite(now):raise PolicyDenied('Invalid authority clock')
        if now>=grant['expires_at']:raise PolicyDenied('Grant expired')
        if action in ('prepare','execute','verify','export','train','publish','transfer') and now>=plan['expires_at']:
            raise PolicyDenied('Plan expired')
        if grant['workspace_id']!=workspace_id or grant['principal']['principal_id']!=principal_id:raise PolicyDenied('Principal or workspace mismatch')
        if action not in grant['allowed_actions']:raise PolicyDenied('Action outside grant')
        # The decision is checked against the exact plan input versions. Authority
        # also independently checks asset rights below, not just a status string.
        if decision['decision_id']!=plan['rights_decision']['id'] or plan['rights_decision']['version']!=decision['schema_version'] or decision['status']!='compatible':raise PolicyDenied('Plan has no matching compatible decision')
        def refs(values):return {(r['id'],r['version']) for r in values}
        wanted=refs(plan['input_versions'])
        if len(wanted)!=len(plan['input_versions']) or refs(decision['input_versions'])!=wanted:raise PolicyDenied('Decision does not cover exact input versions')
        available={}
        for reference in plan['input_versions']:
            asset=deepcopy(self._asset_resolver(deepcopy(reference)))
            validate_entity(asset,'AssetVersion');key=(asset['asset_id'],asset['version'])
            if key in available:raise PolicyDenied('Duplicate asset version')
            available[key]=asset
        if set(available)!=wanted:raise PolicyDenied('Exact input assets required')
        usage=data_use or {'inspect':'inspect','prepare':'evaluate','execute':'evaluate','verify':'evaluate','cancel':'inspect',
                           'export':'export','train':'train','publish':'redistribute','transfer':'redistribute'}[action]
        if usage not in ('inspect','evaluate','train','export','redistribute'):
            raise PolicyDenied('Unsupported data use')
        if (not accepted_rights_statuses or
                not set(accepted_rights_statuses)<= {'declared','verified'}):
            raise PolicyDenied('Invalid accepted source-rights statuses')
        scopes={}
        for entry in grant['data_scopes']:
            key=(entry['asset_ref']['id'],entry['asset_ref']['version'])
            scopes.setdefault(key,set()).update(entry['actions'])
        source_statuses={}
        for key,asset in available.items():
            if usage not in scopes.get(key,set()):raise PolicyDenied('Asset use outside grant')
            rights=next(r for r in asset['rights'] if r['subject']=='data')
            if rights['status'] not in accepted_rights_statuses or usage not in rights['allowed_uses']:
                raise PolicyDenied('Data usage rights status or allowed use does not satisfy operation policy')
            if asset['access_status']!='file_verified':raise PolicyDenied('Input file not verified')
            source_statuses[key[0]+'@'+key[1]]=rights['status']
        for name,requested in plan['resource_limits'].items():
            allowed=grant['resource_limits'][name]
            if requested is None and allowed is not None:raise PolicyDenied('Unknown requested '+name+' cannot meet finite grant')
            if requested is not None and allowed is not None and requested>allowed:raise PolicyDenied('Resource limit exceeds grant: '+name)
        return {'authorized':True,'plan_digest':plan['digest'],'grant_id':grant_id,'action':action,
                'workspace_id':workspace_id,'principal_id':principal_id,'checked_at':now,
                'data_use':usage,'source_rights_statuses':source_statuses,
                'scope':'local decision; runtime must enforce limits and recheck before side effects'}

    def execute(self,plan,*,runner,**context):
        # A trusted caller supplies the runner, never serialized code from inputs.
        pinned=deepcopy(plan)
        authorization=self.authorize(pinned,action='execute',**context)
        return runner(pinned,authorization)
