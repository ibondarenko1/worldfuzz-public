"""M2 bridge from the local task service to versioned entities and policy gates."""
from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path

from ..entities import EntityError, plan_digest, validate_entity
from ..entities.compatibility import check_compatibility
from ..entities.policy import PolicyAuthority, PolicyDenied
from ..entities.schemas import VERSION as ENTITY_VERSION
from ..m1_catalog import canonical, identity
from .contracts import Fault
from .source_contracts import external_engine_contract, m1_contract, registered_sensor_contract, registered_trajectory_contract


LOCAL_ACTIONS = ['inspect', 'prepare', 'execute', 'cancel', 'verify', 'export']
DATA_ACTIONS = ['inspect', 'evaluate', 'export']
RESOURCE_LIMITS = {
    'wall_seconds': 180,
    'cpu_seconds': 180,
    'memory_bytes': 2 * 1024 * 1024 * 1024,
    'artifact_bytes': 128 * 1024 * 1024,
    'model_calls': 0,
    'device_calls': 0,
}


def principal_grant_id(workspace_id,principal_id):
    return 'worldfuzz.principal.'+hashlib.sha256((workspace_id+':'+principal_id).encode()).hexdigest()[:32]


class ServiceAuthorization:
    """Persist trusted local access and resolve exact catalog versions.

    The SQLite mirror lets the existing subprocess worker use the same immutable
    contracts.  When an M1 ``VersionedCatalog`` is supplied it remains the
    authoritative resolver and receives the same entities and snapshot blob.
    """

    def __init__(self, service, *, catalog=None, workspace_id='local-workspace',
                 principal_id='local-owner', allowed_actions=None):
        self.service = service
        self.catalog = catalog
        self.workspace_id = workspace_id
        self.principal_id = principal_id
        self.grant_id = (principal_grant_id(workspace_id,principal_id)
                         if service.identity else 'worldfuzz.local-owner')
        self.allowed_actions = list(allowed_actions or LOCAL_ACTIONS)
        with service.corpus.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS wf_contract_entities(
                    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                    version TEXT NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(entity_type,entity_id,version));
                CREATE TABLE IF NOT EXISTS wf_policy_grants(
                    grant_id TEXT PRIMARY KEY, body TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
            ''')
            row = db.execute('SELECT body FROM wf_policy_grants WHERE grant_id=?',
                             (self.grant_id,)).fetchone()
            if row is None:
                grant = self._grant([])
                db.execute('INSERT INTO wf_policy_grants(grant_id,body,revoked) VALUES (?,?,0)',
                           (self.grant_id, canonical(grant)))
            db.execute("INSERT OR IGNORE INTO wf_metadata VALUES('policy_auto_scope','1')")

    def _grant(self, scopes):
        return {
            'entity_type': 'PolicyGrant', 'schema_version': ENTITY_VERSION,
            'grant_id': self.grant_id, 'workspace_id': self.workspace_id,
            'principal': {'principal_id': self.principal_id, 'kind': 'user'},
            'allowed_actions': list(self.allowed_actions), 'data_scopes': scopes,
            'resource_limits': deepcopy(RESOURCE_LIMITS), 'expires_at': 32503680000,
        }

    def revoke(self):
        with self.service.corpus.connect() as db:
            db.execute('UPDATE wf_policy_grants SET revoked=1 WHERE grant_id=?',
                       (self.grant_id,))

    def replace_grant(self, grant):
        """Install trusted owner configuration; request payloads cannot call this."""
        validate_entity(grant, 'PolicyGrant')
        if grant['grant_id'] != self.grant_id:
            raise EntityError('Configured grant id differs from this service authority')
        with self.service.corpus.connect() as db:
            db.execute('UPDATE wf_policy_grants SET body=?,revoked=0 WHERE grant_id=?',
                       (canonical(grant), self.grant_id))
            db.execute("UPDATE wf_metadata SET value='0' WHERE key='policy_auto_scope'")

    def _add_scope(self, reference):
        with self.service.corpus.connect() as db:
            mode = db.execute("SELECT value FROM wf_metadata WHERE key='policy_auto_scope'").fetchone()
            if mode is None or mode['value'] != '1':
                return
            row = db.execute('SELECT body,revoked FROM wf_policy_grants WHERE grant_id=?',
                             (self.grant_id,)).fetchone()
            if row is None or row['revoked']:
                return
            grant = json.loads(row['body'])
            key = (reference['id'], reference['version'])
            known = {(item['asset_ref']['id'], item['asset_ref']['version'])
                     for item in grant['data_scopes']}
            if key not in known:
                grant['data_scopes'].append({'asset_ref': deepcopy(reference),
                                             'actions': list(DATA_ACTIONS)})
                validate_entity(grant, 'PolicyGrant')
                db.execute('UPDATE wf_policy_grants SET body=? WHERE grant_id=?',
                           (canonical(grant), self.grant_id))

    def _register_local(self, entity):
        validate_entity(entity)
        kind, entity_id, version = identity(entity)
        body = canonical(entity)
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self.service.corpus.connect() as db:
            db.execute('INSERT OR IGNORE INTO wf_contract_entities VALUES (?,?,?,?,?)',
                       (kind, entity_id, version, body, digest))
            prior = db.execute(
                'SELECT body,sha256 FROM wf_contract_entities WHERE entity_type=? AND entity_id=? AND version=?',
                (kind, entity_id, version)).fetchone()
        if prior['body'] != body or prior['sha256'] != digest:
            raise Fault('PLAN_CHANGED', 'Immutable service entity version conflict')
        if self.catalog is not None:
            self.catalog.register(deepcopy(entity))

    def _resolve(self, reference, kind):
        if self.catalog is not None:
            return self.catalog.get(kind, reference['id'], reference['version'])
        with self.service.corpus.connect() as db:
            row = db.execute(
                'SELECT body,sha256 FROM wf_contract_entities WHERE entity_type=? AND entity_id=? AND version=?',
                (kind, reference['id'], reference['version'])).fetchone()
        if row is None:
            raise KeyError('Unknown service entity version')
        if hashlib.sha256(row['body'].encode()).hexdigest() != row['sha256']:
            raise ValueError('Service entity integrity check failed')
        entity = json.loads(row['body'])
        validate_entity(entity, kind)
        return entity

    def _source_contract(self, request, descriptor):
        if descriptor['kind'] == 'can_replay_v1':
            from .adapters.can_replay import replay_contract
            return replay_contract(), 'm1'
        if descriptor['kind'] == 'm1':
            source = m1_contract(descriptor['domain'])
            _, asset_ref = self._ref(source['asset'])
            _, profile_ref = self._ref(source['profile'])
            if asset_ref != descriptor['asset_ref'] or profile_ref != descriptor['profile_ref']:
                raise Fault('PROFILE_INCOMPATIBLE', 'Adapter source versions differ from the M1 contract')
            return source, 'm1'
        if descriptor['kind']=='external_engine_v1':
            source=external_engine_contract();source.pop('manifest')
            return source,'external_engine'
        values = request.get('parameters', request)
        asset_id = values.get('asset_id')
        if not asset_id:
            raise Fault('PROFILE_INCOMPATIBLE', 'Sensor operation has no registered source asset')
        asset=self.service._asset(asset_id)
        if descriptor['kind']=='registered_sensor_v1':
            return registered_sensor_contract(asset_id,asset),'validated_registration'
        return registered_trajectory_contract(asset_id,asset), 'validated_registration'

    def _ensure_external_blob(self, asset, source_kind):
        if self.catalog is None or asset['access_status'] != 'file_verified':
            return
        try:
            self.catalog.blobs.describe(asset['observed_hash_or_null'], verify=True)
            return
        except FileNotFoundError:
            pass
        if source_kind in ('m1','external_engine'):
            source = Path(__file__).resolve().parents[2] / asset['source_locator']
            if not source.is_file():
                raise Fault('SOURCE_UNAVAILABLE', 'Pinned M1 source bytes are absent from the configured blob store')
            self.catalog.blobs.put_file(source,
                expected_sha256=asset['observed_hash_or_null'], expected_size=asset['size_or_null'])
            return
        raw = canonical(self.service._asset(asset['asset_id'])).encode()
        with tempfile.NamedTemporaryFile(prefix='worldfuzz-source-', delete=False) as stream:
            stream.write(raw)
            source = Path(stream.name)
        try:
            self.catalog.blobs.put_file(source,
                expected_sha256=asset['observed_hash_or_null'], expected_size=asset['size_or_null'])
        finally:
            source.unlink(missing_ok=True)

    @staticmethod
    def _ref(entity):
        kind, entity_id, version = identity(entity)
        return kind, {'id': entity_id, 'version': version}

    @staticmethod
    def _hash(entity):
        return hashlib.sha256(canonical(entity).encode()).hexdigest()

    def contracts(self, request, snapshot, expires_at):
        source_descriptor = self.service.adapters.binding(request['profile'])['descriptor']['source_contract']
        source, source_kind = self._source_contract(request, source_descriptor)
        family, asset, profile = (source[name] for name in ('family', 'asset', 'profile'))
        assets = [asset] + source.get('supplements', [])
        operation_requirements = deepcopy(source_descriptor['operations'][request['operation']])
        limits = deepcopy(RESOURCE_LIMITS)
        limits['wall_seconds'] = request.get('max_seconds', 180)
        limits['cpu_seconds'] = limits['wall_seconds']
        limits.update(request.get('resource_limits', {}))
        decision = check_compatibility(
            profile, deepcopy(profile), assets,
            required_rights=operation_requirements['required_rights'],
            required_resources=limits, offered_resources=deepcopy(RESOURCE_LIMITS), checked_at=0,
            required_profile_fields=operation_requirements['profile_fields'],
            require_known_profile=operation_requirements['require_known_profile'])
        _, profile_ref = self._ref(profile)
        decision['evidence_refs'] = [profile_ref]
        decision['decision_id'] = 'pending'
        decision['decision_id'] = hashlib.sha256(canonical(decision).encode()).hexdigest()
        validate_entity(decision, 'CompatibilityDecision')

        self._register_local(family)
        for item in assets:
            self._ensure_external_blob(item, source_kind)
            self._register_local(item)
        self._register_local(profile)
        self._register_local(decision)
        references = [{'id': item['asset_id'], 'version': item['version']} for item in assets]
        for reference in references:
            self._add_scope(reference)

        run_plan = {
            'entity_type': 'RunPlan', 'schema_version': ENTITY_VERSION,
            'plan_id': 'plan-' + hashlib.sha256(canonical(snapshot).encode()).hexdigest()[:32],
            'digest': '0' * 64,
            'task_ref': {'id': 'task.' + request['operation'], 'version': ENTITY_VERSION},
            'input_versions': references,
            'steps': [{'step_id': 'execute',
                       'procedure_ref': {'id': 'procedure.' + request['operation'],
                                         'version': ENTITY_VERSION},
                       'depends_on': [], 'parameters': []}],
            'runner_requirements': {
                'execution_mode': operation_requirements['execution_mode'],
                'profile_refs': [{'id': profile['profile_id'], 'version': profile['version']}],
                'environment_ref': {'id': 'worldfuzz.local-python', 'version': ENTITY_VERSION}},
            'resource_limits': limits,
            'rights_decision': {'id': decision['decision_id'], 'version': decision['schema_version']},
            'estimated_usage': [], 'expires_at': expires_at,
        }
        run_plan['digest'] = plan_digest(run_plan)
        validate_entity(run_plan, 'RunPlan')
        hashes = []
        for entity in (family, *assets, profile, decision):
            kind, entity_ref = self._ref(entity)
            hashes.append({'entity_type': kind, 'ref': entity_ref, 'sha256': self._hash(entity)})
        context = {
            'schema_version': '1.0.0', 'operation': request['operation'],
            'source_kind': source_kind, 'entity_hashes': hashes,
            'asset_refs': references, 'profile_ref': profile_ref,
            'required_rights': deepcopy(operation_requirements['required_rights']),
            'profile_fields': deepcopy(operation_requirements['profile_fields']),
            'require_known_profile': operation_requirements['require_known_profile'],
            'accepted_rights_statuses': deepcopy(operation_requirements['accepted_rights_statuses']),
            'required_resources': limits, 'offered_resources': deepcopy(RESOURCE_LIMITS),
            'checked_at': 0,
        }
        return run_plan, decision, context

    def _validate_context(self, plan):
        context = plan.get('authorization_contract')
        if type(context) is not dict or context.get('schema_version') != '1.0.0':
            raise PolicyDenied('Plan has no corrected source authorization context')
        if context.get('operation') != plan.get('request', {}).get('operation'):
            raise PolicyDenied('Operation differs from source authorization context')
        resolved = {}
        for pinned in context.get('entity_hashes', []):
            kind = pinned.get('entity_type')
            if kind not in ('SourceFamily', 'AssetVersion', 'EmbodimentProfile', 'CompatibilityDecision'):
                raise PolicyDenied('Authorization context contains an unsupported entity')
            entity = self._resolve(pinned['ref'], kind)
            if self._hash(entity) != pinned.get('sha256'):
                raise PolicyDenied('Pinned source or profile entity changed')
            resolved[(kind, pinned['ref']['id'], pinned['ref']['version'])] = entity
        assets = []
        for reference in context.get('asset_refs', []):
            key = ('AssetVersion', reference['id'], reference['version'])
            if key not in resolved:
                raise PolicyDenied('Pinned source asset is absent from authorization context')
            assets.append(resolved[key])
        profile_ref = context.get('profile_ref', {})
        profile = resolved.get(('EmbodimentProfile', profile_ref.get('id'), profile_ref.get('version')))
        if profile is None:
            raise PolicyDenied('Pinned source profile is absent from authorization context')
        if plan['contract_plan']['input_versions'] != context['asset_refs']:
            raise PolicyDenied('Run plan source versions differ from authorization context')
        if plan['contract_plan']['runner_requirements']['profile_refs'] != [profile_ref]:
            raise PolicyDenied('Run plan profile version differs from authorization context')
        recalculated = check_compatibility(
            profile, deepcopy(profile), assets,
            required_rights=context['required_rights'],
            required_resources=context['required_resources'],
            offered_resources=context['offered_resources'], checked_at=context['checked_at'],
            required_profile_fields=context['profile_fields'],
            require_known_profile=context['require_known_profile'])
        recalculated['evidence_refs'] = [deepcopy(profile_ref)]
        recalculated['decision_id'] = 'pending'
        recalculated['decision_id'] = hashlib.sha256(canonical(recalculated).encode()).hexdigest()
        decision_ref = plan['contract_plan']['rights_decision']
        decision = resolved.get(('CompatibilityDecision', decision_ref['id'], decision_ref['version']))
        if decision is None or canonical(decision) != canonical(recalculated):
            raise PolicyDenied('Compatibility decision no longer matches source facts')
        return context

    def authorize(self, plan, action):
        if self.service.identity: self.service.identity.check_plan(plan,action)
        contract = plan.get('contract_plan')
        if contract is None:
            raise Fault('ACCESS_DENIED', 'Plan has no versioned authorization contract')
        try:
            context = self._validate_context(plan)
            if self.service.identity:
                for ref in context['asset_refs']:self._add_scope(ref)
            with self.service.corpus.connect() as db:
                row = db.execute('SELECT body,revoked FROM wf_policy_grants WHERE grant_id=?',
                                 (self.grant_id,)).fetchone()
            grants = [] if row is None or row['revoked'] else [json.loads(row['body'])]
            authority = PolicyAuthority(
                grants, asset_resolver=lambda ref: self._resolve(ref, 'AssetVersion'),
                decision_resolver=lambda ref: self._resolve(ref, 'CompatibilityDecision'),
                clock=self.service.clock)
            receipt = authority.authorize(contract, grant_id=self.grant_id,
                workspace_id=self.workspace_id, principal_id=self.principal_id, action=action,
                data_use=('inspect' if action in ('inspect', 'cancel') else
                          context['required_rights']['data']),
                accepted_rights_statuses=context['accepted_rights_statuses'])
            validate_entity(contract, 'RunPlan')
            return receipt
        except (PolicyDenied, EntityError, KeyError, ValueError) as exc:
            raise Fault('ACCESS_DENIED', str(exc), next_action='get_capabilities') from exc

    def authorize_operation(self, plan, action):
        receipts = [self.authorize(plan, action)]
        operation = plan['request']['operation']
        extra = 'export' if operation in ('export_dataset','build_dataset') else 'verify' if operation == 'verify_episodes' else None
        if extra and extra != action:
            receipts.append(self.authorize(plan, extra))
        return receipts
