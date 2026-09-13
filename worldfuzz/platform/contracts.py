"""Operation contracts. Deliberately small JSON Schema subset, no coercion."""
import math
import re
import uuid

VERSION = '1.0.0'
STRING = {'type': 'string', 'minLength': 1, 'maxLength': 256}
ID = {**STRING, 'maxLength': 64}
PROFILE = dict(STRING)


def strict_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('Duplicate JSON field')
        value[key]=item
    return value


class Fault(ValueError):
    def __init__(self, code, message, path='', next_action=None):
        super().__init__(message)
        if next_action is None:
            next_action = {'PLAN_EXPIRED':'prepare_plan', 'RESULT_PENDING':'get_run',
                'QUOTA_EXCEEDED':'get_workspace_usage', 'RESOURCE_BUSY':'get_workspace_usage',
                'SCHEMA_INVALID':'revise_request', 'PROFILE_INCOMPATIBLE':'revise_request',
                'UNSUPPORTED_OPERATION':'describe_operation', 'INCOMPARABLE':'revise_request',
                'ACCESS_DENIED':'stop', 'BUDGET_EXCEEDED':'stop', 'RUNNER_UNSUPPORTED':'stop'}.get(code, 'stop')
        self.code, self.path, self.next_action = code, path, next_action


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def validate(value, schema, path='$', root=None):
    root=schema if root is None else root
    if '$defs' in schema:root=schema
    if '$ref' in schema:
        target=root
        for token in schema['$ref'].removeprefix('#/').split('/'):
            target=target[token.replace('~1','/').replace('~0','~')]
        return validate(value,target,path,root)
    kind = schema.get('type')
    if 'const' in schema and (type(value) is not type(schema['const']) or value != schema['const']):
        raise Fault('RESPONSE_INVALID', 'Value differs from contract constant', path)
    if 'oneOf' in schema:
        matches=0
        for branch in schema['oneOf']:
            try:validate(value,branch,path,root);matches+=1
            except Fault:pass
        if matches!=1:raise Fault('RESPONSE_INVALID','Value must match exactly one response shape',path)
        return
    if isinstance(kind,list):
        for candidate in kind:
            try:validate(value,{**schema,'type':candidate},path,root);return
            except Fault:pass
        raise Fault('RESPONSE_INVALID','Value has unsupported response type',path)
    types = {'object': dict, 'array': list, 'string': str, 'integer': int, 'number': (int, float), 'boolean': bool,
             'null': type(None)}
    if kind and (not isinstance(value, types[kind]) or (kind in ('integer', 'number') and isinstance(value, bool))):
        raise Fault('SCHEMA_INVALID', 'Expected '+kind, path)
    if 'enum' in schema and value not in schema['enum']:
        raise Fault('SCHEMA_INVALID', 'Value not in supported choices', path)
    if isinstance(value, float) and not math.isfinite(value):
        raise Fault('SCHEMA_INVALID', 'Non-finite number', path)
    if kind == 'object':
        if len(value)>schema.get('maxProperties',math.inf):
            raise Fault('RESPONSE_INVALID','Object has too many fields',path)
        for key in schema.get('required', []):
            if key not in value: raise Fault('SCHEMA_INVALID', 'Missing required field', path+'.'+key)
        for key, item in value.items():
            properties=schema.get('properties',{})
            if key not in properties:
                if schema.get('additionalProperties') is not True:raise Fault('SCHEMA_INVALID', 'Unknown field', path+'.'+key)
                continue  # Adapter schema performs the second, strict validation.
            validate(item, properties[key], path+'.'+key,root)
    if kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10000):
            raise Fault('SCHEMA_INVALID', 'Array size outside limits', path)
        for i, item in enumerate(value): validate(item, schema['items'], f'{path}[{i}]',root)
    if kind == 'string' and not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 4096):
        raise Fault('SCHEMA_INVALID', 'String length outside limits', path)
    if kind == 'string' and schema.get('pattern') and re.fullmatch(schema['pattern'],value) is None:
        raise Fault('RESPONSE_INVALID','String does not match response contract',path)
    if kind in ('number', 'integer') and not schema.get('minimum', -math.inf) <= value <= schema.get('maximum', math.inf):
        raise Fault('SCHEMA_INVALID', 'Number outside limits', path)


POINT = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4}
TRACK = {'type': 'array', 'items': POINT, 'minItems': 2, 'maxItems': 10000}
BASE64 = {'type':'string','minLength':12,'maxLength':2800000}
FILTERS = obj({'domain': {'type':'string','enum':['navigation','manipulation']},
               'status': STRING, 'label': STRING, 'split': {'type':'string','enum':['train','validation','test']}}, ['domain'])
PLAN = obj({'operation': STRING, 'parameters': {'type':'object','additionalProperties':True},
            'verification':obj({'mode':{'type':'string','enum':['integrity','metric_recompute','replay']},'protocol':STRING},['mode']),
            'required_runner_capabilities': {'type':'array','items':STRING,'maxItems':16},
            'resource_limits': obj({'cpu_seconds':{'type':'number','minimum':0.1,'maximum':180},
                'memory_bytes':{'type':'integer','minimum':67108864,'maximum':2147483648},
                'artifact_bytes':{'type':'integer','minimum':0,'maximum':134217728}}),
            'profile': PROFILE, 'algorithm':{'type':'string','enum':['astar','dijkstra']}, 'filters': FILTERS, 'asset_id': ID,
            'episode_id': ID,
            'max_messages': {'type':'integer','minimum':1,'maximum':1000},
            'seed': {'type':'integer','minimum':0,'maximum':2147483647},
            'start_index': {'type':'integer','minimum':0,'maximum':9999},
            'frame_count': {'type':'integer','minimum':1,'maximum':10},
            'stride': {'type':'integer','minimum':1,'maximum':100},
            'case_indices': {'type':'array','items':{'type':'integer','minimum':0,'maximum':8},'minItems':1,'maxItems':9,
                'description':'Zero-based Moving AI case indices (0 through 8). Index N selects canonical resource movingai:N, not ordinal N+1. Use find_resources to inspect the pinned case; movingai:8 means case_indices [8].'},
            'max_seconds': {'type':'integer','minimum':1,'maximum':180},
            'max_cases': {'type':'integer','minimum':1,'maximum':9},
            'threshold_m': {'type':'number','minimum':0,'maximum':10000}}, ['operation','profile'])
GOAL_INPUT=obj({'operation':STRING,'profile':PROFILE,'parameters':{'type':'object','additionalProperties':True,
    # Keep adapter-specific validation in the registry; annotate Moving AI here
    # without imposing its numeric range on third-party adapter parameters.
    'properties':{'case_indices':{'description':PLAN['properties']['case_indices']['description']}}},
    'workflow_kind':{'enum':['single_operation','campaign']},
    'max_seconds':PLAN['properties']['max_seconds'],'resource_limits':PLAN['properties']['resource_limits'],
    'required_runner_capabilities':PLAN['properties']['required_runner_capabilities'],
    'verification':PLAN['properties']['verification']})
CAMPAIGN_CASE=obj({'case_id':ID,'goal':GOAL_INPUT,'origin':{'type':'string','enum':['published','derived']},
    'parent_case_id':ID,'mutations':{'type':'array','items':obj({'path':STRING,'value':STRING},['path','value']),'maxItems':16}},
    ['case_id','goal','origin'])
CAMPAIGN_BUDGET=obj({'max_cases':{'type':'integer','minimum':1,'maximum':16},
    'max_attempts':{'type':'integer','minimum':1,'maximum':32},
    'cpu_seconds':{'type':'number','minimum':0.1,'maximum':2880},
    'wall_seconds':{'type':'number','minimum':0.1,'maximum':2880},
    'artifact_bytes':{'type':'integer','minimum':0,'maximum':536870912}},
    ['max_cases','max_attempts','cpu_seconds','wall_seconds','artifact_bytes'])


def op(description, properties=None, required=(), effect='read', route='', method='POST'):
    return {'description': description, 'inputSchema': obj(properties or {}, required),
            'effect': effect, 'route': route, 'method': method}

QUOTA_LIMITS=obj({'max_jobs':{'type':'integer','minimum':0,'maximum':100000},
    'cpu_seconds':{'type':'number','minimum':0,'maximum':86400},'wall_seconds':{'type':'number','minimum':0,'maximum':172800},
    'artifact_bytes':{'type':'integer','minimum':0,'maximum':1073741824},
    'active_memory_bytes':{'type':'integer','minimum':0,'maximum':4294967296},
    'model_calls':{'const':0},'device_calls':{'const':0}},
    ['max_jobs','cpu_seconds','wall_seconds','artifact_bytes','active_memory_bytes','model_calls','device_calls'])


OPERATIONS = {
    'run_goal':op('Compile, prepare, execute and read one typed goal through scoped service tools. Same key resumes the same logical run. Waiting is not task failure. Campaigns require the campaign tools.',
        {'goal':GOAL_INPUT,'idempotency_key':STRING,'resource_id':{**STRING,
            'description':'Exact resource ID returned by find_resources. Moving AI uses zero-based movingai:N; movingai:8 selects case_index 8. Explicit parameters must agree with this resource.'},
         'wait_seconds':{'type':'number','minimum':0,'maximum':30},'include_artifacts':{'type':'boolean'}},
        ['goal','idempotency_key'],'compute','/v1/goals/run'),
    'describe_operation':op('Describe one callable tool with exact input/output schemas. Adapter operations are parameters of compile_goal, not MCP tool names.',
        {'name':{**STRING,'description':'Bare service tool name from tools/list, e.g. get_result_report. Do not include the MCP server name or an adapter operation.'}},['name'],route='/v1/operations/describe'),
    'get_artifact_json':op('Read a hash-checked JSON artifact as nanoseconds-decimal.v1 without modifying original bytes. Not JSONL or binary export.',
        {'result_id':ID,'name':STRING},['result_id','name'],route='/v1/results/{result_id}/artifact-json'),
    'get_workspace_usage':op('Read quota limits, reservations, measured phases and explicit unknown consumption.',route='/v1/workspace/usage',method='GET'),
    'set_workspace_quota':op('Update a workspace lifetime quota with optimistic version matching; no usage reset.',
        {'limits':QUOTA_LIMITS,'expected_version':{'type':'integer','minimum':1}},['limits','expected_version'],'write','/v1/workspace/quota'),
    'get_service_metrics':op('Read persisted service counters scoped to this workspace.',route='/v1/metrics',method='GET'),
    'get_run_audit':op('Read durable run transitions, attempt history and retention state.',{'run_id':ID},['run_id'],route='/v1/runs/{run_id}/audit',method='GET'),
    'create_backup':op('Freeze metadata, history and registered blobs after run reconciliation.',
        {'max_mib':{'type':'integer','minimum':1,'maximum':1024}},effect='write',route='/v1/backups'),
    'restore_backup':op('Restore an owned backup to a separate copy; never overwrite the active catalog.',
        {'backup_id':{'type':'string','pattern':'[a-f0-9]{32}'},'max_mib':{'type':'integer','minimum':1,'maximum':1024}},
        ['backup_id'],'write','/v1/backups/restore'),
    'apply_retention':op('Preview or delete selected old terminal artifacts; retain history and protect suites/campaigns.',
        {'run_ids':{'type':'array','items':ID,'minItems':1,'maxItems':25},'before':{'type':'number','minimum':0},'dry_run':{'type':'boolean'}},
        ['run_ids','before'],'write','/v1/retention'),
    'organization_login':op('Authenticate a provisioned local organization principal and issue a workspace-scoped token.',
        {'organization':ID,'principal':ID,'password':{'type':'string','minLength':16,'maxLength':1024},'workspace':ID,
         'operations':{'type':'array','items':STRING,'minItems':1,'maxItems':100},
         'ttl_seconds':{'type':'integer','minimum':1,'maximum':86400}},
        ['organization','principal','password','workspace','operations'],'write','/v1/organization/login'),
    'create_webhook':op('Create a workspace-local authenticated pull outbox; no external sending.',
        {'name':STRING},['name'],'write','/v1/webhooks'),
    'read_webhook':op('Read authorized durable run events from a workspace-local webhook outbox.',
        {'webhook_id':ID,'after':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100}},
        ['webhook_id'],'read','/v1/webhooks/read'),
    'get_capabilities': op('Discover exact supported profiles, operations and local execution limits.', route='/v1/capabilities', method='GET'),
    'find_resources': op('Find included tasks and stored episodes; returns a bounded page, not episode arrays.',
                         {'profile':PROFILE,'limit':{'type':'integer','minimum':1,'maximum':100},'cursor':{'type':'integer','minimum':0}}, route='/v1/resources/search'),
    'compile_goal': op('Compile a structured goal into an authorized typed plan. Provider contract mode makes zero model calls.',
        {'goal':GOAL_INPUT,
         'provider_mode':{'type':'string','enum':['disabled','contract_only']}}, ['goal'],'write','/v1/goals/compile'),
    'create_campaign':op('Freeze a bounded campaign, allowed mutations, immutable criteria, typed case plans and total budget.',
        {'name':STRING,'cases':{'type':'array','items':CAMPAIGN_CASE,'minItems':1,'maxItems':16},
         'allowed_mutations':{'type':'array','items':STRING,'maxItems':32},
         'immutable_criteria':{'type':'array','items':STRING,'minItems':1,'maxItems':32},
         'budget':CAMPAIGN_BUDGET,'reduction':{'type':'string','enum':['exact_typed_request']}},
        ['name','cases','allowed_mutations','immutable_criteria','budget'],'write','/v1/campaigns'),
    'launch_campaign_case':op('Start one campaign case as an ordinary durable job under the campaign budget.',
        {'campaign_id':ID,'case_id':ID,'retry_key':STRING},['campaign_id','case_id','retry_key'],'compute','/v1/campaigns/{campaign_id}/cases/{case_id}/runs'),
    'get_campaign':op('Read the immutable campaign, attempts and final report.',{'campaign_id':ID},['campaign_id'],route='/v1/campaigns/{campaign_id}',method='GET'),
    'finalize_campaign':op('Reduce terminal case results and freeze the complete phase/attempt ledger.',
        {'campaign_id':ID},['campaign_id'],'write','/v1/campaigns/{campaign_id}/finalize'),
    'find_verified_results':op('Find authorized verified results compatible with an exact dataset generation; never auto-include.',
        {'profile':PROFILE,'operation':STRING,'dataset_result_id':ID,
         'purpose':{'type':'string','enum':['training','evaluation']},
         'limit':{'type':'integer','minimum':1,'maximum':100},'cursor':{'type':'integer','minimum':0}},
        ['profile','operation'],'read','/v1/verified-results/search'),
    'create_suite':op('Explicitly include unique compatible verified results in an immutable dataset-linked suite.',
        {'name':STRING,'purpose':{'type':'string','enum':['training','evaluation']},
         'selected_result_ids':{'type':'array','items':ID,'minItems':1,'maxItems':100},'dataset_result_id':ID},
        ['name','purpose','selected_result_ids','dataset_result_id'],'write','/v1/suites'),
    'get_suite':op('Read and revalidate an immutable task/run/evidence/dataset suite.',
        {'suite_id':ID},['suite_id'],'read','/v1/suites/{suite_id}',method='GET'),
    'register_asset': op('Register timestamped XYZ prediction/reference tracks, already in the same frame and metres. Does not control a device.',
                        {'name':STRING,'source_url':STRING,'data_license':STRING,'code_license':STRING,'frame':STRING,
                         'units':{'type':'string','enum':['m']},'prediction':TRACK,'reference':TRACK},
                        ['name','source_url','data_license','code_license','frame','units','prediction','reference'], 'write', '/v1/assets'),
    'get_asset': op('Read an integrity-checked registered sensor asset including source provenance and preserved input text.', {'asset_id':ID}, ['asset_id'], route='/v1/assets/{asset_id}', method='GET'),
    'register_trajectory': op('Import two TUM-format UTF-8 trajectories with exact matching timestamps. Preserves source text and hashes; evaluates XYZ only, no frame alignment or device control.',
        {'name':STRING,'source_url':STRING,'data_license':STRING,'code_license':STRING,'frame':STRING,
         'units':{'type':'string','enum':['m']},'format':{'type':'string','enum':['tum.v1']},
         'prediction_text':{'type':'string','minLength':1,'maxLength':500000},
         'reference_text':{'type':'string','minLength':1,'maxLength':500000}},
        ['name','source_url','data_license','code_license','frame','units','format','prediction_text','reference_text'], 'write','/v1/trajectory-assets'),
    'register_sensor_recording': op('Register a bounded ROS 1 or ROS 2 MCAP recording. Preserves exact bytes, timestamps, schemas, units and the declared frame; does not infer commands or flight outcomes.',
        {'name':STRING,'source_url':STRING,'source_revision':STRING,'data_license':STRING,'code_license':STRING,
         'platform':{'type':'string','enum':['aerial','ground','manipulator','unknown']},'frame':STRING,
         'units':{'type':'array','items':STRING,'minItems':1,'maxItems':16},
         'ros_version':{'type':'string','enum':['ros1','ros2']},'mcap_base64':BASE64},
        ['name','source_url','source_revision','data_license','code_license','platform','frame','units','ros_version','mcap_base64'],
        'write','/v1/sensor-recordings'),
    'prepare_plan': op('Validate compatibility and freeze inputs, code identity and budget before any computation.',
                       PLAN['properties'], PLAN['required'], 'write','/v1/plans'),
    'execute_plan': op('Start one durable local job using a prepared digest and idempotency key. Poll get_run. Zero provider calls.',
                       {'plan_id':ID,'plan_digest':ID,'idempotency_key':STRING}, ['plan_id','plan_digest','idempotency_key'],'compute','/v1/runs'),
    'get_run': op('Read durable job status. Completion is separate from the task outcome.', {'run_id':ID}, ['run_id'], route='/v1/runs/{run_id}', method='GET'),
    'get_budget': op('Read the atomic reservation, observed phase usage and explicitly unknown usage.', {'run_id':ID}, ['run_id'], route='/v1/runs/{run_id}/budget', method='GET'),
    'cancel_run': op('Request cooperative cancellation; retains completed evidence.', {'run_id':ID}, ['run_id'], 'cancel','/v1/runs/{run_id}/cancel'),
    'reconcile_run': op('Reconcile an expired lease without replaying work; preserve attempts and partial evidence.', {'run_id':ID}, ['run_id'], 'write','/v1/runs/{run_id}/reconcile'),
    'resume_run': op('Explicitly resume one incomplete logical job under a retry key; preserve previous attempts.',
        {'run_id':ID,'idempotency_key':STRING}, ['run_id','idempotency_key'], 'compute','/v1/runs/{run_id}/resume'),
    'get_result': op('Read an integrity-checked result manifest, evidence and artifact references.', {'result_id':ID}, ['result_id'], route='/v1/results/{result_id}', method='GET'),
    'get_result_report': op('Read the same scoped evidence verdict for UI, embed and CI, with JSON and JUnit. Rechecks current access.', {'result_id':ID}, ['result_id'], route='/v1/results/{result_id}/report', method='GET'),
    'read_signed_webhook': op('Read workspace-bound HMAC signed run events. Pull only; no outbound delivery.',
        {'webhook_id':ID,'after':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100}},
        ['webhook_id'],'read','/v1/webhooks/{webhook_id}/signed-events'),
    'get_dataset_generation': op('Read a verified DatasetManifest, immutable split and native-reader round-trip receipt.',
        {'result_id':ID}, ['result_id'], route='/v1/datasets/{result_id}', method='GET'),
    'verify_run_result': op('Independently check stored bytes or supported offline metrics; never imply physical replay.',
        {'result_id':ID,'mode':{'type':'string','enum':['integrity','metric_recompute','replay']},'protocol':STRING},
        ['result_id'],'write','/v1/results/{result_id}/verify'),
    'compare_results': op('Compare completed results only when their task inputs and evaluation protocol match.',
                          {'left_id':ID,'right_id':ID,'mode':{'type':'string','enum':['repeat','algorithms']}}, ['left_id','right_id'], route='/v1/comparisons/query'),
    'verify_result': op('Recompute stored episode checks in a new run plan. Does not claim physics replay.',
                        {'profile':PROFILE,'filters':FILTERS}, ['profile','filters'], 'write','/v1/verification-plans'),
    'export_result': op('Prepare only: freeze a provenance-preserving dataset export plan with immutable group splits. Execution has NOT started; there is no run or export artifact. Execute the returned plan with execute_plan, or use run_goal with goal.operation export_dataset and explicit budgets. Confirm export only from a completed run and its existing hash-checked artifacts.',
                        {'profile':PROFILE,'filters':FILTERS}, ['profile','filters'], 'write','/v1/export-plans'),
}

PROFILES = [
    {'id':'movingai.octile.v1','domain':'navigation','state':'grid x,y; cells','action':'grid displacement dx,dy',
     'observation':'original binary map','outcome':'independent route validity and published shortest-path length',
     'operations':['solve_navigation','verify_episodes','export_dataset'],'execution':'offline_grid_planning','data_license':'ODC-By-1.0','code_license':'MIT'},
    {'id':'robomimic.can-paired.v1','domain':'manipulation','state':'robosuite 1.5.1 simulator state, not policy observation',
     'action':'original environment controller action; no cross-controller conversion','observation':'named source obs channels',
     'outcome':'published reward labels; no new physics replay','operations':['verify_episodes','export_dataset','view_recorded_states'],
     'execution':'offline_data_processing','data_license':'per pinned source manifest','code_license':'MIT'},
    {'id':'sensor.trajectory.xyz.v1','domain':'sensors','state':'timestamp seconds + xyz metres in an explicit shared frame',
     'action':None,'observation':'paired estimated and reference trajectory; identical timestamps required',
     'outcome':'un-aligned position RMSE against supplied reference and explicit threshold, not flight success',
     'operations':['evaluate_trajectory'],'execution':'offline_metric_recompute','data_license':'per registered asset','code_license':'MIT'},
    {'id':'sensor.mcap.aerial.v1','domain':'sensors','state':'recorded ROS message bytes with log and publish timestamps',
     'action':None,'observation':'MCAP channels with ros1msg/ros2msg schemas and caller-declared units and frame',
     'outcome':'recording integrity and message inventory only; no control or flight outcome inferred',
     'operations':['inspect_sensor_recording'],'execution':'offline_data_processing','data_license':'per registered asset','code_license':'MIT'},
    {'id':'pymunk.basic-fall.v7.2','domain':'external_engine','state':'2D position and velocity in the published Pymunk pixel frame',
     'action':None,'observation':'engine position/velocity trace at each published 1/50 s step',
     'outcome':'independently checked removal below y=0 after the published falling-ball simulation',
     'operations':['run_pymunk_basic'],'execution':'closed_loop_simulation','data_license':'none; published task code is MIT','code_license':'MIT'},
]


def envelope(data=None, error=None):
    from .exact_json import project, CONTRACT
    result = {'schema_version':VERSION,'request_id':uuid.uuid4().hex,'status':'error' if error else 'ok',
              'data':project(data or {}),'data_representation':dict(CONTRACT),'limitations':[],'next_actions':[]}
    if error:
        result['error']={'code':error.code,'message':str(error),'field_path':error.path,'retryable':False,'next_action':error.next_action}
        result['next_actions']=[{'kind':'tool' if error.next_action in OPERATIONS else error.next_action,
                                'operation':error.next_action if error.next_action in OPERATIONS else None,
                                'reason':str(error),'automatic_retry':False}]
        if getattr(error,'run_id',None):result['error']['run_id']=error.run_id
    return result


OBJECT={'type':'object','additionalProperties':True}
ARRAY_OBJECT={'type':'array','items':OBJECT}
from .exact_json import DECIMAL_NS
EXACT_MESSAGE={'type':'object','additionalProperties':True,'properties':{
    'log_time_ns':DECIMAL_NS,'publish_time_ns':DECIMAL_NS}}
EXACT_RANGE={'type':'object','additionalProperties':True,'properties':{
    'first_log':DECIMAL_NS,'last_log':DECIMAL_NS}}
EXACT_RECORD={'type':'object','additionalProperties':True,'properties':{
    'messages':{'type':'array','items':EXACT_MESSAGE},
    'returned_messages':{'type':'array','items':EXACT_MESSAGE},'time_range_ns':EXACT_RANGE}}
NULLABLE_ID={'type':['string','null'],'minLength':1,'maxLength':64}
RUN_STATUS={'type':'string','enum':['queued','running','cancel_requested','completed','failed','cancelled','budget_exhausted','incomplete']}
EVENT=obj({'seq':{'type':'integer','minimum':0},'at':{'type':'number'},'status':RUN_STATUS},['seq','at','status'])
VERIFICATION_REPORT=obj({
    'schema_version':{'const':'1.0.0'},'verification_id':ID,
    'requested_mode':{'type':'string','enum':['integrity','metric_recompute']},
    'performed_checks':{'type':'array','items':obj({'mode':{'type':'string','enum':['integrity','metric_recompute']},
        'protocol':STRING,'status':{'const':'passed'}},['mode','protocol','status']),'minItems':1},
    'proven_result':{'type':'string','enum':['not_evaluated','no_violation_found','violation_found']},
    'physics_replayed':{'const':False},'input_snapshot_sha256':ID,
    'artifact_hashes':{'type':'array','items':obj({'name':STRING,'sha256':ID},['name','sha256'])},
    'scope':STRING,'verifier_sha256':ID},
    ['schema_version','verification_id','requested_mode','performed_checks','proven_result',
     'physics_replayed','input_snapshot_sha256','artifact_hashes','scope','verifier_sha256'])
RESULT_BODY={'type':'object','properties':{
    'runner':OBJECT,
    'verification':VERIFICATION_REPORT,
    'schema_version':STRING,'execution_status':{'type':'string','enum':['running','completed','failed','cancelled','budget_exhausted','incomplete']},
    'provider_calls':{'type':'integer','minimum':0},'records':{'type':'array','items':EXACT_RECORD},'artifacts':ARRAY_OBJECT,
    'operation':STRING,'elapsed_seconds':{'type':'number','minimum':0},'input_snapshot':OBJECT},
    'required':['schema_version','execution_status','provider_calls'],'additionalProperties':False}
COMPARE={'type':'object','properties':{
    'left_id':ID,'right_id':ID,'comparable':{'type':'boolean'},'same_records':{'type':'boolean'},
    'scope':STRING,'equivalent_outcomes':{'type':'boolean'},'summary':OBJECT,'protocol':OBJECT,
    'paired_results':ARRAY_OBJECT,'coverage':OBJECT,'procedures':ARRAY_OBJECT},
    'required':['left_id','right_id','comparable','same_records','scope'],'additionalProperties':False}
from ..entities.schemas import schema as entity_schema
SUCCESS_DATA = {
    'run_goal':obj({'status':{'enum':['needs_information','waiting','terminal']},'missing_parameters':ARRAY_OBJECT,
        'run_id':NULLABLE_ID,'run':{'type':['object','null'],'additionalProperties':True},
        'result':{'type':['object','null'],'additionalProperties':True},'report':{'type':['object','null'],'additionalProperties':True},
        'artifacts':ARRAY_OBJECT,'export_skipped':ARRAY_OBJECT},
        ['status','missing_parameters','run_id','run','result','report','artifacts','export_skipped']),
    'describe_operation':obj({'name':STRING,'namespace':{'const':'service_tool'},'input_schema':OBJECT,'output_schema':OBJECT,'description':{'type':'string','maxLength':4096}},['name','namespace','input_schema','output_schema','description']),
    'get_artifact_json':obj({'result_id':ID,'name':STRING,'original_sha256':ID,'representation_sha256':ID,
        'representation':OBJECT,'content':OBJECT},['result_id','name','original_sha256','representation_sha256','representation','content']),
    'get_workspace_usage':obj({'workspace_id':ID,'version':{'type':'integer'},'limits':QUOTA_LIMITS,
        'available':OBJECT,'committed':OBJECT,'measured':OBJECT,'conservative_unknown_holds':OBJECT,
        'unknown':ARRAY_OBJECT,'reservations':ARRAY_OBJECT,'cost_usd':{'const':None},'scope':STRING},
        ['workspace_id','version','limits','available','committed','measured','conservative_unknown_holds','unknown','reservations','cost_usd','scope']),
    'get_service_metrics':obj({'workspace_id':ID,'requests':ARRAY_OBJECT,'runs':ARRAY_OBJECT,'retained_out_runs':{'type':'integer'},
        'attempts_with_unknown_tail':{'type':'integer'},'scope':STRING},['workspace_id','requests','runs','retained_out_runs','attempts_with_unknown_tail','scope']),
    'get_run_audit':obj({'run_id':ID,'events':ARRAY_OBJECT,'attempts':ARRAY_OBJECT,'entries':ARRAY_OBJECT,
        'retention':{'type':['object','null'],'additionalProperties':True}},['run_id','events','attempts','entries','retention']),
    'create_backup':obj({'backup_id':ID,'manifest_sha256':ID,'files':{'type':'integer'},'bytes':{'type':'integer'},'scope':STRING},
        ['backup_id','manifest_sha256','files','bytes','scope']),
    'restore_backup':obj({'restore_id':ID,'workspace_id':{'type':['string','null']},'database':{'type':'string','maxLength':4096},
        'artifact_root':{'type':'string','maxLength':4096},'files_verified':{'type':'integer'},'jobs_restarted':{'const':0},'scope':STRING},
        ['restore_id','workspace_id','database','artifact_root','files_verified','jobs_restarted','scope']),
    'apply_retention':obj({'run_ids':{'type':'array','items':ID},'deleted_files':{'type':'integer'},'dry_run':{'type':'boolean'},
        'state':{'enum':['preview','complete']}},['run_ids','deleted_files','dry_run','state']),
    'organization_login':obj({'token':STRING,'workspace_id':ID,'principal_id':ID,'expires_at':{'type':'number'}},
        ['token','workspace_id','principal_id','expires_at']),
    'create_webhook':obj({'webhook_id':ID,'name':STRING,'delivery':{'const':'authenticated_pull'},'external_delivery':{'const':False}},
        ['webhook_id','name','delivery','external_delivery']),
    'read_webhook':obj({'webhook_id':ID,'events':ARRAY_OBJECT,'next_cursor':{'type':'integer'},
        'delivery':{'const':'authenticated_pull'},'external_delivery':{'const':False}},
        ['webhook_id','events','next_cursor','delivery','external_delivery']),
    'get_capabilities':obj({'profiles':ARRAY_OBJECT,'adapters':ARRAY_OBJECT,
        'operations':{'type':'array','items':STRING},'limits':OBJECT,'schema_version':STRING,
        'deployment':STRING,'unsupported':{'type':'array','items':STRING},
        'discovery':OBJECT,'evidence_rules':{'type':'array','items':{'type':'string','maxLength':4096}}},
        ['profiles','adapters','operations','limits','schema_version','deployment','unsupported']),
    'find_resources':obj({'matches':ARRAY_OBJECT,'next_cursor':{'type':['integer','null']},
        'total':{'type':'integer','minimum':0},'profiles':ARRAY_OBJECT},
        ['matches','next_cursor','total','profiles']),
    'compile_goal':obj({'status':{'type':'string','enum':['ready','needs_information']},
        'missing_parameters':ARRAY_OBJECT,'typed_request':{'type':['object','null'],'additionalProperties':True},
        'prepared_plan':{'type':['object','null'],'additionalProperties':True},'provider':OBJECT,
        'provider_calls':{'const':0},'scope':STRING},
        ['status','missing_parameters','typed_request','prepared_plan','provider','provider_calls','scope']),
    'create_campaign':obj({'campaign_id':ID,'campaign':OBJECT,'status':{'const':'prepared'},'attempts':ARRAY_OBJECT,'scope':STRING},
        ['campaign_id','campaign','status','attempts','scope']),
    'launch_campaign_case':obj({'campaign_id':ID,'case_id':ID,'run':OBJECT,'idempotent_replay':{'type':'boolean'}},
        ['campaign_id','case_id','run','idempotent_replay']),
    'get_campaign':obj({'campaign_id':ID,'campaign':OBJECT,
        'status':{'type':'string','enum':['prepared','running','ready_to_finalize','finalized']},
        'attempts':ARRAY_OBJECT,'report':{'type':['object','null'],'additionalProperties':True}},
        ['campaign_id','campaign','status','attempts','report']),
    'finalize_campaign':OBJECT,
    'find_verified_results':obj({'matches':ARRAY_OBJECT,'next_cursor':{'type':['integer','null']},
        'total':{'type':'integer','minimum':0},'automatic_inclusion':{'const':False},'scope':STRING},
        ['matches','next_cursor','total','automatic_inclusion','scope']),
    'create_suite':OBJECT,
    'get_suite':OBJECT,
    'register_asset':obj({'asset_id':ID,'sha256':ID,'evidence':STRING,
        'label_origin':STRING,'control_actions_present':{'type':'boolean'}},
        ['asset_id','sha256','evidence','label_origin','control_actions_present']),
    'get_asset':obj({'asset_id':ID,'sha256':ID,'asset':{'type':'object','additionalProperties':True,
        'properties':{'recording':EXACT_RECORD}}},['asset_id','sha256','asset']),
    'prepare_plan':obj({'plan_id':ID,'plan_digest':ID,'compatibility':{'const':'compatible'},
        'plan':{**OBJECT,'properties':{'preparation':obj({'stage':{'const':'prepared'},'execution_started':{'const':False},
            'run_id':{'const':None},'run':{'const':None},'result_id':{'const':None},
            'export_artifact_created':{'const':False}},
            ['stage','execution_started','run_id','run','result_id','export_artifact_created'])},'required':['preparation']},
        'run_plan':entity_schema('RunPlan'),
        'compatibility_decision':entity_schema('CompatibilityDecision'),
        'authorization':{'type':'array','items':OBJECT,'minItems':1}},
        ['plan_id','plan_digest','compatibility','plan','run_plan','compatibility_decision','authorization']),
    'execute_plan':obj({'run_id':ID,'plan_id':ID,'status':RUN_STATUS,'progress':OBJECT,'attempts':ARRAY_OBJECT,
        'created_at':{'type':'number'},'updated_at':{'type':'number'},'result_id':NULLABLE_ID,
        'error':{'type':['string','null']},'events':{'type':'array','items':EVENT}},
        ['run_id','plan_id','status','progress','created_at','updated_at','result_id','error','events']),
    'get_run':obj({'run_id':ID,'plan_id':ID,'status':RUN_STATUS,'progress':OBJECT,'attempts':ARRAY_OBJECT,
        'created_at':{'type':'number'},'updated_at':{'type':'number'},'result_id':NULLABLE_ID,
        'error':{'type':['string','null']},'events':{'type':'array','items':EVENT}},
        ['run_id','plan_id','status','progress','created_at','updated_at','result_id','error','events']),
    'cancel_run':None,
    'get_result':obj({'result_id':ID,'result_sha256':ID,'plan_id':ID,'result':RESULT_BODY},
        ['result_id','result_sha256','plan_id','result']),
    'get_dataset_generation':obj({'result_id':ID,'dataset_manifest':entity_schema('DatasetManifest'),
        'split_manifest':entity_schema('SplitManifest'),'roundtrip':OBJECT},
        ['result_id','dataset_manifest','split_manifest','roundtrip']),
    'compare_results':COMPARE,
}
SUCCESS_DATA['register_trajectory']=SUCCESS_DATA['register_asset']
SUCCESS_DATA['set_workspace_quota']=SUCCESS_DATA['get_workspace_usage']
SUCCESS_DATA['register_sensor_recording']=SUCCESS_DATA['register_asset']
SUCCESS_DATA['cancel_run']=SUCCESS_DATA['get_run']
SUCCESS_DATA['verify_run_result']=VERIFICATION_REPORT
SUCCESS_DATA['reconcile_run']=SUCCESS_DATA['get_run']
SUCCESS_DATA['resume_run']=SUCCESS_DATA['get_run']
SUCCESS_DATA['get_budget']=obj({'reservation':entity_schema('BudgetReservation'),'attempt_accounting':ARRAY_OBJECT,'scope':STRING},
    ['reservation','attempt_accounting','scope'])
SUCCESS_DATA['verify_result']=SUCCESS_DATA['prepare_plan']
SUCCESS_DATA['export_result']=SUCCESS_DATA['prepare_plan']
RESULT_SUMMARY=obj({
    'schema_version':{'const':'1.0.0'},'result_id':ID,'result_sha256':ID,
    'profile':PROFILE,'operation':STRING,'execution_status':RUN_STATUS,
    'task_outcome':VERIFICATION_REPORT['properties']['proven_result'],
    'ci_status':{'enum':['passed','violation','error','incomplete']},
    'exit_code':{'type':'integer','enum':[0,1,2,3]},'required_check':STRING,
    'performed_checks':{'type':'array','items':VERIFICATION_REPORT['properties']['performed_checks']['items']},
    'verification_scope':STRING,'physics_replayed':{'const':False},'recording':{'const':True},
    'meaning':{**STRING,'description':'Short evidence statement. Moving AI lists canonical movingai:N and actual zero-based case_index. Dataset export is confirmed only after completed execution with existing hash-checked payload and manifest. Artifact references remain in get_result.'},
},['schema_version','result_id','result_sha256','profile','operation','execution_status','task_outcome',
   'ci_status','exit_code','required_check','performed_checks','verification_scope','physics_replayed','recording','meaning'])
SUCCESS_DATA['get_result_report']=obj({'summary':RESULT_SUMMARY,'junit_xml':{'type':'string','maxLength':65536}},['summary','junit_xml'])
SIGNED_EVENT=obj({'delivery_id':ID,'payload':obj({'schema_version':{'const':'1.0.0'},'workspace_id':ID,
    'webhook_id':ID,'event':obj({**EVENT['properties'],'run_id':ID},['seq','at','status','run_id'])},
    ['schema_version','workspace_id','webhook_id','event']),'signed_at':{'type':'integer'},'signature':ID},
    ['delivery_id','payload','signed_at','signature'])
SUCCESS_DATA['read_signed_webhook']=obj({'webhook_id':ID,'events':{'type':'array','items':SIGNED_EVENT},
    'next_cursor':{'type':'integer'},'delivery':{'const':'authenticated_signed_pull'},'external_delivery':{'const':False}},
    ['webhook_id','events','next_cursor','delivery','external_delivery'])

REQUEST_EXAMPLES = {
    'run_goal':{'goal':{'operation':'solve_navigation','profile':'movingai.octile.v1','parameters':{'case_indices':[0]},'max_seconds':20},
        'idempotency_key':'published-case-0','wait_seconds':20,'include_artifacts':True},
    'describe_operation':{'name':'compile_goal'},
    'get_artifact_json':{'result_id':'replace-with-run-id','name':'sensor-recording-inventory.json'},
    'get_workspace_usage':{},
    'set_workspace_quota':{'limits':{'max_jobs':10,'cpu_seconds':300,'wall_seconds':600,'artifact_bytes':16777216,
        'active_memory_bytes':536870912,'model_calls':0,'device_calls':0},'expected_version':1},
    'get_service_metrics':{},
    'get_run_audit':{'run_id':'replace-with-run-id'},
    'create_backup':{'max_mib':256},
    'restore_backup':{'backup_id':'0123456789abcdef0123456789abcdef','max_mib':256},
    'apply_retention':{'run_ids':['replace-with-run-id'],'before':1789171200,'dry_run':True},
    'organization_login':{'organization':'demo-org','principal':'demo-user','workspace':'demo-workspace',
        'password':'<supply-through-local-protected-input>','operations':['get_capabilities'],'ttl_seconds':3600},
    'create_webhook':{'name':'local-events'},
    'read_webhook':{'webhook_id':'replace-with-webhook-id','after':0,'limit':25},
    'get_capabilities': {},
    'find_resources': {'profile':'movingai.octile.v1','limit':10,'cursor':0},
    'compile_goal': {'goal':{'operation':'solve_navigation','profile':'movingai.octile.v1',
        'parameters':{'case_indices':[0],'max_cases':1,'algorithm':'astar'},'max_seconds':30},
        'provider_mode':'disabled'},
    'create_campaign': {'name':'Bounded published checks','cases':[{'case_id':'nav-0','origin':'published',
        'goal':{'operation':'solve_navigation','profile':'movingai.octile.v1','parameters':{'case_indices':[0],'max_cases':1,'algorithm':'astar'}}}],
        'allowed_mutations':['goal.parameters.algorithm'],'immutable_criteria':['published source','verification protocol'],
        'budget':{'max_cases':2,'max_attempts':2,'cpu_seconds':60,'wall_seconds':60,'artifact_bytes':2097152}},
    'launch_campaign_case': {'campaign_id':'replace-with-campaign-id','case_id':'nav-0','retry_key':'nav-0-attempt-1'},
    'get_campaign': {'campaign_id':'replace-with-campaign-id'},
    'finalize_campaign': {'campaign_id':'replace-with-campaign-id'},
    'find_verified_results': {'profile':'movingai.octile.v1','operation':'solve_navigation',
        'dataset_result_id':'replace-with-dataset-run-id','purpose':'evaluation','limit':25,'cursor':0},
    'create_suite': {'name':'Verified navigation suite','purpose':'evaluation',
        'selected_result_ids':['replace-with-verified-run-id'],'dataset_result_id':'replace-with-dataset-run-id'},
    'get_suite': {'suite_id':'replace-with-suite-id'},
    'register_asset': {'name':'Authored trajectory fixture','source_url':'urn:example:trajectory',
        'data_license':'private test data','code_license':'MIT','frame':'world','units':'m',
        'prediction':[[0,0,0,0],[1,1,0,0]],'reference':[[0,0,0,0],[1,1,0,0]]},
    'get_asset': {'asset_id':'replace-with-returned-asset-id'},
    'register_trajectory': {'name':'Authored TUM fixture','source_url':'urn:example:tum',
        'data_license':'private test data','code_license':'MIT','frame':'world','units':'m','format':'tum.v1',
        'prediction_text':'0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 1',
        'reference_text':'0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 1'},
    'register_sensor_recording': {'name':'Published ROS 2 recording','source_url':'https://example.invalid/source',
        'source_revision':'replace-with-pinned-revision','data_license':'replace-with-source-license',
        'code_license':'MIT','platform':'aerial','frame':'replace-with-declared-frame','units':['m','s'],
        'ros_version':'ros2','mcap_base64':'replace-with-base64-mcap'},
    'prepare_plan': {'operation':'solve_navigation','profile':'movingai.octile.v1',
        'case_indices':[0],'max_seconds':30},
    'execute_plan': {'plan_id':'replace-with-plan-id','plan_digest':'replace-with-plan-digest',
        'idempotency_key':'project-case-0-v1'},
    'get_run': {'run_id':'replace-with-run-id'},
    'get_budget': {'run_id':'replace-with-run-id'},
    'cancel_run': {'run_id':'replace-with-run-id'},
    'reconcile_run': {'run_id':'replace-with-run-id'},
    'resume_run': {'run_id':'replace-with-run-id','idempotency_key':'explicit-recovery-1'},
    'get_result': {'result_id':'replace-with-run-id'},
    'get_result_report': {'result_id':'replace-with-run-id'},
    'read_signed_webhook': {'webhook_id':'replace-with-webhook-id','after':0,'limit':25},
    'get_dataset_generation': {'result_id':'replace-with-dataset-run-id'},
    'verify_run_result': {'result_id':'replace-with-run-id','mode':'integrity'},
    'compare_results': {'left_id':'replace-with-left-run-id','right_id':'replace-with-right-run-id','mode':'repeat'},
    'verify_result': {'profile':'movingai.octile.v1','filters':{'domain':'navigation'}},
    'export_result': {'profile':'movingai.octile.v1','filters':{'domain':'navigation'}},
}


def validate_output(name, data):
    """Validate every successful operation result before a transport sees it."""
    schema=SUCCESS_DATA.get(name)
    if schema is None:raise Fault('RESPONSE_INVALID','Operation has no output contract')
    try:
        validate(data,schema,'$.data')
        if name in ('prepare_plan','verify_result','export_result'):
            from ..entities import validate_entity
            validate_entity(data['run_plan'],'RunPlan')
            validate_entity(data['compatibility_decision'],'CompatibilityDecision')
    except Exception as exc:
        if isinstance(exc,Fault) and exc.code=='RESPONSE_INVALID':raise
        raise Fault('RESPONSE_INVALID','Operation produced data outside its output contract',
                    getattr(exc,'path','$.data')) from exc
    return data


def openapi():
    from .exact_json import DECIMAL_NS
    paths={}
    for name, spec in OPERATIONS.items():
        operation={'operationId':name,'description':spec['description'],
                   'responses':{'200':{'description':'Validated structured operation result','content':{'application/json':{'schema':output_schema(name)}}},
                                '400':{'description':'Structured domain or schema error'},'401':{'description':'Missing local bearer token'},
                                '403':{'description':'Policy grant denied or revoked'}},
                   'security':[{'LocalBearer':[]}]}
        if name=='organization_login':operation['security']=[]
        props=spec['inputSchema']['properties']
        path_fields=[k for k in props if '{'+k+'}' in spec['route']]
        if path_fields:
            operation['parameters']=[{'in':'path','name':k,'required':True,'schema':props[k]} for k in path_fields]
        if set(props)-set(path_fields):
            operation['requestBody']={'required':True,'content':{'application/json':{
                'schema':obj({k:v for k,v in props.items() if k not in path_fields},
                    [k for k in spec['inputSchema']['required'] if k not in path_fields]),
                'example':{k:v for k,v in REQUEST_EXAMPLES[name].items() if k not in path_fields}}}}
        paths.setdefault(spec['route'],{})[spec['method'].lower()]=operation
    from ..entities.schemas import PROPERTIES, schema as entity_schema
    return {'openapi':'3.1.2','info':{'title':'WorldFuzz local task API','version':VERSION,
            'description':'M2 local API. Successful responses are validated before delivery; plans pin versioned entities and policy decisions.'},
            'paths':paths,'components':{'securitySchemes':{'LocalBearer':{'type':'http','scheme':'bearer'}},
            'schemas':{**{name:entity_schema(name) for name in PROPERTIES},
                       'ExactNanoseconds':DECIMAL_NS}}}


def output_schema(name):
    """Output fields are discoverable without reading implementation code."""
    if name not in SUCCESS_DATA:raise ValueError('Unknown operation output')
    data={'oneOf':[SUCCESS_DATA[name],{'type':'object','maxProperties':0}]}
    return {'type':'object','required':['schema_version','request_id','status','data','limitations','next_actions'],
            'properties':{'schema_version':{'type':'string'},'request_id':{'type':'string'},'status':{'enum':['ok','error']},
                          'data':data,'limitations':{'type':'array','items':{'type':'string'}},
                          'next_actions':{'type':'array','items':{'type':'object','additionalProperties':True}},
                          'data_representation':obj({'version':{'const':'nanoseconds-decimal.v1'},
                              'encoding':{'const':'base10_integer_string'},'unit':{'const':'ns'},'hash_scope':STRING},
                              ['version','encoding','unit','hash_scope']),
                          'error':{'type':'object','additionalProperties':True}}}
