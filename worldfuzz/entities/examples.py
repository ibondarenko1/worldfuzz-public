"""Authored contract examples, never represented as executed robotics results."""
from .schemas import VERSION,PROPERTIES
from . import plan_digest

def examples(domain):
    if domain not in ('navigation','manipulation','sensors'):raise ValueError('Unsupported example domain')
    def r(name):return {'id':domain+'.example.'+name,'version':'1.0.0'}
    def entity(kind,/,**kw):return {'entity_type':kind,'schema_version':VERSION,**kw}
    limits={'wall_seconds':30,'cpu_seconds':None,'memory_bytes':None,'artifact_bytes':1048576,'model_calls':0,'device_calls':0}
    rights=[{'subject':s,'license':None,'status':'unknown','allowed_uses':[],'evidence_urls':[],
             'limitations':['Authored schema example; no dataset permission claimed']} for s in ('data','code','model','scene')]
    specs={
     'navigation':('cell',2,'grid position','grid displacement','binary occupancy map','shortest_path','path_length',10,'le'),
     'manipulation':('rad',7,'joint positions','joint position target','measured joint positions','reach_joint_target','joint_error',0.01,'le'),
     'sensors':('m',3,'estimated position',None,'timestamped estimated position','evaluate_localization','position_rmse',0.1,'le')}
    unit,n,state,action,obs,objective,metric,target,operator=specs[domain]
    def signal(name,semantic):return {'name':name,'dtype':'float64','shape':[n],'unit':unit,'frame':None if domain=='manipulation' else 'world','semantic':semantic,'rate_hz':None}
    values=[
     entity('SourceFamily',family_id=r('family')['id'],name='Authored '+domain+' contract example',canonical_sources=['urn:worldfuzz:authored-example'],lineage=[],domain=[domain]),
     entity('AssetVersion',asset_id=r('asset')['id'],version=VERSION,family_id=r('family')['id'],source_locator='urn:example:unobserved',observed_hash_or_null=None,size_or_null=None,access_status='documentation_only',rights=rights,observed_at=0),
     entity('EmbodimentProfile',profile_id=r('profile')['id'],version=VERSION,components=[{'component_id':'component','kind':domain}],state_schema=[signal('state',state)],action_schema=None if action is None else [signal('action',action)],observation_schema=[signal('observation',obs)],frames=[{'frame_id':'world','parent':None,'convention':'Explicit example frame; not inferred from data'}],units=[unit,'s'],time_model={'unit':'s','clock':'example clock','synchronization':'exact timestamp pairs'},unknown_fields=['hardware identity']),
     entity('TaskSpec',task_id=r('task')['id'],version=VERSION,profile_constraints=[r('profile')],input_refs=[r('asset')],objective={'template':objective,'parameters':[]},criteria=[{'criterion_id':'criterion','metric':metric,'operator':operator,'target':target,'unit':unit,'protocol_ref':r('protocol')}],execution_mode='offline_evaluation',allowed_variations=[],required_evidence=['metric_recompute']),
     entity('ProcedureVersion',procedure_id=r('procedure')['id'],version=VERSION,adapter_id=r('adapter')['id'],input_schema_ref='urn:worldfuzz:entity:TaskSpec:1.0.0',output_schema_ref='urn:worldfuzz:entity:Outcome:1.0.0',environment_lock={'runtime':'python-3.12','dependencies':[],'image_digest':None,'unresolved':['Contract example; no executor installed']},resource_profile=limits,verification_method=r('verifier')),
     entity('Episode',episode_id=r('episode')['id'],asset_ref=r('asset'),profile_ref=r('profile'),source_episode_id='example-0',payload_ref={'locator':'urn:example:payload','sha256':'0'*64,'bytes':0},group_refs=['example-group'],outcome_sources=[]),
     entity('CompatibilityDecision',decision_id=r('decision')['id'],status='needs_information',input_versions=[r('asset')],matched_requirements=[],missing_fields=['data usage rights','observed input bytes'],conflicts=[],evidence_refs=[],checked_at=0),
     entity('RunPlan',plan_id=r('plan')['id'],digest='0'*64,task_ref=r('task'),input_versions=[r('asset')],steps=[{'step_id':'evaluate','procedure_ref':r('procedure'),'depends_on':[],'parameters':[]}],runner_requirements={'execution_mode':'offline_evaluation','profile_refs':[r('profile')],'environment_ref':r('environment')},resource_limits=limits,rights_decision=r('decision'),estimated_usage=[],expires_at=1),
     entity('Run',run_id=r('run')['id'],plan_digest='0'*64,workspace_id='example-workspace',status='queued',attempts=[],usage=[],event_cursor=0,artifact_refs=[],started_at=None,finished_at=None),
     entity('Evidence',evidence_id=r('evidence')['id'],kind='source_label',scope='Authored schema example, no executed measurement',input_hashes=[{'input_ref':r('payload'),'sha256':'0'*64}],procedure_ref=r('procedure'),issuer={'identity':'example-author','kind':'publisher'},measured_values=[],tolerances=[],limitations=['Placeholder hashes demonstrate types, not verified bytes'],created_at=0),
     entity('Outcome',criterion_ref=r('criterion'),verdict='unknown',value=None,unit=unit,basis='none',evidence_refs=[],unknown_reason='No experiment executed'),
     entity('Comparison',comparison_id=r('comparison')['id'],run_refs=[r('run'),r('other-run')],protocol_ref=r('protocol'),comparable=False,mismatches=['No completed runs'],paired_results=[],coverage={'requested':1,'paired':0,'missing':['example-0']},selection_rule='All example cases'),
     entity('SplitManifest',split_id=r('split')['id'],version=VERSION,grouping_rule=r('grouping'),group_assignments=[{'group_id':'example-group','split':'test'}],source_lineage=[r('asset')],seed_or_rule='Fixed authored group assignment',heldout_policy={'training_allowed':False,'automatic_reassignment':False,'description':'Do not train on held-out groups'}),
     entity('DatasetManifest',dataset_id=r('dataset')['id'],version=VERSION,episode_refs=[r('episode')],split_ref=r('split'),filters=[],transform_refs=[],rights=rights,target_schema='urn:worldfuzz:entity:Episode:1.0.0',checksums=[{'locator':'urn:example:payload','sha256':'0'*64}]),
     entity('PolicyGrant',grant_id=r('grant')['id'],workspace_id='example-workspace',principal={'principal_id':'example-user','kind':'user'},allowed_actions=['inspect'],data_scopes=[{'asset_ref':r('asset'),'actions':['inspect']}],resource_limits=limits,expires_at=1),
     entity('BudgetReservation',reservation_id=r('reservation')['id'],workspace_id='example-workspace',plan_digest='0'*64,limits=limits,estimated_usage=[],actual_usage=[],unknown_usage=[],expires_at=1,state='expired')]
    result={v['entity_type']:v for v in values}
    result['RunPlan']['digest']=plan_digest(result['RunPlan'])
    for kind in ('Run','BudgetReservation'):result[kind]['plan_digest']=result['RunPlan']['digest']
    assert set(result)==set(PROPERTIES)
    return result
