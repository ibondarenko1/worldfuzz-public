"""Versioned data contracts. No simulator, database or provider imports."""
from copy import deepcopy
VERSION = '1.0.0'
PREFIX = 'urn:worldfuzz:entity:'

def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
def arr(items,minimum=0):
    return {'type':'array','items':items,'minItems':minimum,'maxItems':10000}
def ref(name):return {'$ref':'#/$defs/'+name}
def nullable(schema):return {'anyOf':[schema,{'type':'null'}]}
def choice(*values):return {'type':'string','enum':list(values)}
TEXT={'type':'string','minLength':1,'maxLength':4096}
ID={'type':'string','minLength':1,'maxLength':256}
NUMBER={'type':'number'}
POSITIVE={'type':'number','minimum':0}
INTEGER={'type':'integer','minimum':0}
HASH={'type':'string','pattern':'^[0-9a-f]{64}$'}
REF=obj({'id':ID,'version':ID})
# Explicit typed values, not arbitrary extra object keys.
VALUE={'anyOf':[{'type':'null'},{'type':'boolean'},NUMBER,TEXT,arr(NUMBER)]}
PARAM=obj({'name':ID,'value':VALUE,'unit':nullable(ID)})
LIMITS=obj({'wall_seconds':POSITIVE,'cpu_seconds':nullable(POSITIVE),'memory_bytes':nullable(INTEGER),
            'artifact_bytes':INTEGER,'model_calls':INTEGER,'device_calls':INTEGER})
USAGE=arr(obj({'phase':ID,'resource':ID,'value':nullable(POSITIVE),'unit':ID,'basis':TEXT}))
RIGHT=obj({'subject':choice('data','code','model','scene'),'license':nullable(TEXT),
           'status':choice('declared','verified','unknown','conflict'),
           'allowed_uses':arr(choice('inspect','evaluate','train','export','redistribute')),
           'evidence_urls':arr(TEXT),'limitations':arr(TEXT)})
RIGHTS=arr(RIGHT,4)
SIGNAL=obj({'name':ID,'dtype':choice('float32','float64','int32','int64','uint8','bool','text'),
            'shape':arr({'type':'integer','minimum':1}),'unit':nullable(ID),'frame':nullable(ID),
            'semantic':TEXT,'rate_hz':nullable(POSITIVE)})
DEFS={'ref':REF,'parameter':PARAM,'limits':LIMITS,'usage':USAGE,'rights':RIGHTS,'signal':SIGNAL}
MODE=choice('metadata_inspection','offline_evaluation','observation_replay','closed_loop_simulation','external_result_ingestion')
SPLIT=choice('train','validation','test','excluded')
PROPERTIES={
'SourceFamily':{'family_id':ID,'name':TEXT,'canonical_sources':arr(TEXT,1),'lineage':arr(ref('ref')),'domain':arr(choice('navigation','manipulation','aerial','sensors','composite'),1)},
'AssetVersion':{'asset_id':ID,'version':ID,'family_id':ID,'source_locator':TEXT,'observed_hash_or_null':nullable(HASH),
 'size_or_null':nullable(INTEGER),'access_status':choice('documentation_only','available','file_verified','restricted','unavailable'),
 'rights':ref('rights'),'observed_at':POSITIVE},
'EmbodimentProfile':{'profile_id':ID,'version':ID,'components':arr(obj({'component_id':ID,'kind':ID}),1),
 'state_schema':arr(ref('signal')),'action_schema':nullable(arr(ref('signal'),1)), 'observation_schema':arr(ref('signal'),1),
 'frames':arr(obj({'frame_id':ID,'parent':nullable(ID),'convention':TEXT})), 'units':arr(ID),
 'time_model':obj({'unit':ID,'clock':TEXT,'synchronization':TEXT}), 'unknown_fields':arr(TEXT)},
'TaskSpec':{'task_id':ID,'version':ID,'profile_constraints':arr(ref('ref'),1),'input_refs':arr(ref('ref'),1),
 'objective':obj({'template':ID,'parameters':arr(ref('parameter'))}),
 'criteria':arr(obj({'criterion_id':ID,'metric':ID,'operator':choice('eq','le','ge'),'target':VALUE,'unit':nullable(ID),'protocol_ref':ref('ref')}),1),
 'execution_mode':MODE,'allowed_variations':arr(obj({'field':ID,'values':arr(VALUE,1)})),
 'required_evidence':arr(choice('integrity','metric_recompute','simulation_replay','physical_measurement','source_label'),1)},
'ProcedureVersion':{'procedure_id':ID,'version':ID,'adapter_id':ID,'input_schema_ref':TEXT,'output_schema_ref':TEXT,
 'environment_lock':obj({'runtime':ID,'dependencies':arr(obj({'name':ID,'version':ID,'sha256':nullable(HASH)})),
                          'image_digest':nullable({'type':'string','pattern':'^sha256:[0-9a-f]{64}$'}),'unresolved':arr(TEXT)}),
 'resource_profile':ref('limits'),'verification_method':ref('ref')},
'Episode':{'episode_id':ID,'asset_ref':ref('ref'),'profile_ref':ref('ref'),'source_episode_id':ID,
 'payload_ref':obj({'locator':TEXT,'sha256':HASH,'bytes':INTEGER}),'group_refs':arr(ID,1),
 'outcome_sources':arr(obj({'criterion_ref':ref('ref'),'label':VALUE,'origin':choice('publisher','simulation','physical','unknown'),'evidence_refs':arr(ref('ref'))}))},
'CompatibilityDecision':{'decision_id':ID,'status':choice('compatible','incompatible','needs_information'),
 'input_versions':arr(ref('ref'),1),'matched_requirements':arr(ID),'missing_fields':arr(TEXT),
 'conflicts':arr(obj({'field':ID,'expected':VALUE,'observed':VALUE,'reason':TEXT})), 'evidence_refs':arr(ref('ref')),'checked_at':POSITIVE},
'RunPlan':{'plan_id':ID,'digest':HASH,'task_ref':ref('ref'),'input_versions':arr(ref('ref'),1),
 'steps':arr(obj({'step_id':ID,'procedure_ref':ref('ref'),'depends_on':arr(ID),'parameters':arr(ref('parameter'))}),1),
 'runner_requirements':obj({'execution_mode':MODE,'profile_refs':arr(ref('ref'),1),'environment_ref':ref('ref')}),
 'resource_limits':ref('limits'),'rights_decision':ref('ref'),'estimated_usage':ref('usage'),'expires_at':POSITIVE},
'Run':{'run_id':ID,'plan_digest':HASH,'workspace_id':ID,'status':choice('queued','running','cancel_requested','completed','failed','cancelled','budget_exhausted','incomplete'),
 'attempts':arr(obj({'attempt_id':ID,'status':ID,'started_at':nullable(POSITIVE),'finished_at':nullable(POSITIVE),'error':nullable(TEXT)})),
 'usage':ref('usage'),'event_cursor':INTEGER,'artifact_refs':arr(ref('ref')),'started_at':nullable(POSITIVE),'finished_at':nullable(POSITIVE)},
'Evidence':{'evidence_id':ID,'kind':choice('integrity','metric_recompute','simulation_replay','physical_measurement','source_label'),
 'scope':TEXT,'input_hashes':arr(obj({'input_ref':ref('ref'),'sha256':HASH}),1),'procedure_ref':ref('ref'),
 'issuer':obj({'identity':ID,'kind':choice('local_verifier','publisher','physical_observer')}),'measured_values':arr(ref('parameter')),
 'tolerances':arr(ref('parameter')),'limitations':arr(TEXT),'created_at':POSITIVE},
'Outcome':{'criterion_ref':ref('ref'),'verdict':choice('pass','fail','unknown','not_applicable'),
 'value':VALUE,'unit':nullable(ID),'basis':choice('source_label','integrity','metric_recompute','simulation_replay','physical_measurement','none'),
 'evidence_refs':arr(ref('ref')),'unknown_reason':nullable(TEXT)},
'Comparison':{'comparison_id':ID,'run_refs':arr(ref('ref'),2),'protocol_ref':ref('ref'),'comparable':{'type':'boolean'},
 'mismatches':arr(TEXT),'paired_results':arr(obj({'case_ref':ref('ref'),'left':VALUE,'right':VALUE,'unit':nullable(ID)})),
 'coverage':obj({'requested':INTEGER,'paired':INTEGER,'missing':arr(ID)}),'selection_rule':TEXT},
'SplitManifest':{'split_id':ID,'version':ID,'grouping_rule':ref('ref'),
 'group_assignments':arr(obj({'group_id':ID,'split':SPLIT}),1),'source_lineage':arr(ref('ref'),1),'seed_or_rule':TEXT,
 'heldout_policy':obj({'training_allowed':{'type':'boolean'},'automatic_reassignment':{'const':False},'description':TEXT})},
'DatasetManifest':{'dataset_id':ID,'version':ID,'episode_refs':arr(ref('ref'),1),'split_ref':ref('ref'),
 'filters':arr(ref('parameter')),'transform_refs':arr(ref('ref')),'rights':ref('rights'),'target_schema':TEXT,
 'checksums':arr(obj({'locator':TEXT,'sha256':HASH}),1)},
'PolicyGrant':{'grant_id':ID,'workspace_id':ID,'principal':obj({'principal_id':ID,'kind':choice('user','service','agent')}),
 'allowed_actions':arr(choice('inspect','prepare','execute','cancel','verify','export','train','publish','transfer'),1),
 'data_scopes':arr(obj({'asset_ref':ref('ref'),'actions':arr(choice('inspect','evaluate','train','export','redistribute'),1)})),
 'resource_limits':ref('limits'),'expires_at':POSITIVE},
'BudgetReservation':{'reservation_id':ID,'workspace_id':ID,'plan_digest':HASH,'limits':ref('limits'),'estimated_usage':ref('usage'),
 'actual_usage':ref('usage'),'unknown_usage':arr(obj({'phase':ID,'resource':ID,'reason':TEXT})),
 'expires_at':POSITIVE,'state':choice('reserved','consuming','reconciling','released','expired')}
}

def schema(name,version=VERSION):
    if version!=VERSION:raise ValueError('Unsupported entity schema version: '+version)
    if name not in PROPERTIES:raise ValueError('Unknown entity type: '+name)
    return deepcopy({'$schema':'https://json-schema.org/draft/2020-12/schema',
        '$id':PREFIX+name+':'+VERSION,'$defs':DEFS,
        **obj({'entity_type':{'const':name},'schema_version':{'const':VERSION},**PROPERTIES[name]})})
