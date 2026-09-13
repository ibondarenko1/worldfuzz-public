import {renderForm} from './adapter-form.mjs';
import {renderRecord,renderComparison,outcomeText} from './result-summary.mjs';
import {parseResponseText} from './response-json.mjs';
import {mountImport} from './asset-import.mjs';
import {renderRunDetails} from './run-details.mjs';
const el=id=>document.getElementById(id);
const terminal=new Set(['completed','failed','cancelled','budget_exhausted','incomplete']);
let token,profiles=[],adapters=[],readParameters=()=>({}),plan,runId,currentResult,cursor=0,busy=false,selection=0,resourceRequest=0;
let runHistory=[];
let planHints={},viewRevision=0;
let currentRun=null,pollTimer=null,pollRevision=0,runAction=false,recoveryEnabled=false;

function message(text){el('status').textContent=text;}
function invalidate(){selection++;plan=null;el('execute').disabled=true;el('plan').textContent='';el('compatibility').replaceChildren();el('plan-summary').textContent='Inputs changed. Check compatibility and limits again.';}
function profile(){return profiles.find(p=>p.id===el('profile').value);}
function adapter(){return adapters.find(a=>a.profile.id===profile().id);}
function number(id,label){const value=Number(el(id).value);if(!Number.isFinite(value)||value<=0)throw new Error(`${label} must be greater than zero`);return value;}
function limits(){return {cpu_seconds:number('cpu-seconds','CPU limit'),memory_bytes:number('memory-mib','Memory limit')*1024*1024,artifact_bytes:number('artifact-mib','Artifact limit')*1024*1024};}
function filters(){const f={domain:profile().domain};if(el('split').value)f.split=el('split').value;return f;}

async function request(path,args,authenticated=true){
 const headers={'Content-Type':'application/json'};if(authenticated&&token)headers.Authorization=`Bearer ${token}`;
 const response=await fetch(path,{method:args===undefined?'GET':'POST',headers,body:args===undefined?undefined:JSON.stringify(args)});
 let result;try{result=parseResponseText(await response.text());}catch{throw new Error(`HTTP ${response.status}: response was not JSON`);}
 if(!response.ok||result.status==='error') {const error=new Error(result.error?.message||`Request failed (${response.status})`);error.code=result.error?.code||`HTTP_${response.status}`;throw error;}
 return result.data??result;
}
function clearResult(){viewRevision++;currentResult=null;el('results').replaceChildren();el('downloads').replaceChildren();el('saved-recording').hidden=true;el('export-manifest').disabled=true;el('task-outcome').textContent='Not available';el('verification-level').textContent='Not evaluated';el('ci-verdict').textContent='';}
async function guard(fn){try{await fn();}catch(error){if(error.code==='ACCESS_DENIED'){clearResult();stopPolling();runId=null;currentRun=null;busy=false;el('run-monitor').hidden=true;el('run-details').replaceChildren();updateRunControls();el('comparison').replaceChildren();el('run-state').textContent='Access denied';invalidate();}message(`${error.code?error.code+': ':''}${error.message}`);}}

function operationChanged(initial={},hints={}){invalidate();planHints=hints;el('selected-resource').textContent='';const schema=adapter().request_schemas[el('operation').value];readParameters=renderForm(el('adapter-fields'),schema,invalidate,initial);}
const importer=mountImport(el('import-recording'),{request,onChange:invalidate,onImported:async({profile:chosen,operation,assetId})=>{
 if(chosen!==profile().id)return;el('operation').value=operation;
 operationChanged(operation==='inspect_sensor_recording'?{asset_id:assetId,max_messages:100}:{asset_id:assetId});
 el('selected-resource').textContent='Selected the added recording. Its source terms and metadata remain as supplied.';
 await resources();
},onError:error=>{if(error.code==='ACCESS_DENIED'){clearResult();invalidate();message('Adding recordings requires permission in this workspace and data import enabled at sign-in.');}}});

function useResource(item){
 const suggested=item.prepare_request;if(!suggested||suggested.profile!==profile().id||!profile().operations.includes(suggested.operation))return;
 el('operation').value=suggested.operation;
 const schema=adapter().request_schemas[suggested.operation],parameters=Object.fromEntries(Object.keys(schema.properties??{}).filter(k=>Object.hasOwn(suggested,k)).map(k=>[k,suggested[k]]));
 const hints=Object.fromEntries(['verification','required_runner_capabilities'].filter(k=>Object.hasOwn(suggested,k)).map(k=>[k,suggested[k]]));
 operationChanged(parameters,hints);
 el('selected-resource').textContent=`Selected ${item.kind==='task'?'published task':'saved recording'}: ${item.source_key??item.name??item.id}. Check compatibility before running.`;
 // Choosing data must not silently change the user's CPU/RAM or wall ceilings.
 el('prepare-adapter').focus?.();
}

async function resources(reset=true){
 const version=++resourceRequest;if(reset){cursor=0;el('resources').replaceChildren();}
 const result=await request('/v1/resources/search',{profile:profile().id,limit:25,cursor});if(version!==resourceRequest)return;
 for(const item of result.matches){const li=document.createElement('li');
  if(item.kind==='task'&&item.case)li.textContent=`Published task · ${item.id}: ${item.case.map}, published length ${item.case.published_length}`;
  else if(item.kind==='task')li.textContent=`Published task · ${item.id}`;
  else li.textContent=`Saved recording · ${item.source_key??item.name??item.id} · ${item.task??item.kind} · source label: ${item.label??'not supplied'} · ${item.split??''}`;
  if(item.prepare_request){const select=document.createElement('button');select.type='button';select.textContent=item.kind==='task'?'Use this task':'Use this recording';select.onclick=()=>guard(()=>useResource(item));li.append(select);}
  el('resources').append(li);
 }
 cursor=result.next_cursor;el('more').disabled=cursor===null;el('count').textContent=`${result.total} tasks and saved records. Source labels retain their original meaning.`;
}

function showCompatibility(compiled){
 const decision=compiled.prepared_plan.compatibility_decision,runPlan=compiled.prepared_plan.run_plan;
 const facts=[`Decision: ${decision.status}`,`Wall: ${runPlan.resource_limits.wall_seconds} s`,`CPU: ${runPlan.resource_limits.cpu_seconds} s`,`Memory: ${(runPlan.resource_limits.memory_bytes/1048576).toFixed(0)} MiB`,`Artifacts: ${(runPlan.resource_limits.artifact_bytes/1048576).toFixed(1)} MiB`,`Provider calls: ${compiled.provider_calls}`];
 el('compatibility').replaceChildren();for(const value of facts){const span=document.createElement('span');span.textContent=value;el('compatibility').append(span);}
 const pinned=document.createElement('dl');pinned.className='record-facts';
 const rows=[['Execution',compiled.prepared_plan.plan.execution],['Requested verification',compiled.prepared_plan.plan.verification_contract.mode],...(runPlan.input_versions??[]).map(ref=>['Pinned source',`${ref.id} · version ${ref.version}`])];
 for(const [label,value] of rows){const name=document.createElement('dt'),content=document.createElement('dd');name.textContent=label;content.textContent=value;pinned.append(name,content);}el('compatibility').append(pinned);
}

async function prepare(operation,parameters={}){
 invalidate();const pending=selection;
 const goal={...planHints,operation,profile:profile().id,parameters,max_seconds:number('max-seconds','Wall limit'),resource_limits:limits()};
 const compiled=await request('/v1/goals/compile',{goal,provider_mode:'disabled'});if(pending!==selection)return;
 if(compiled.status!=='ready')throw new Error(compiled.missing_parameters.map(x=>`${x.path}: ${x.reason}`).join('; '));
 plan=compiled.prepared_plan;showCompatibility(compiled);el('plan').textContent=JSON.stringify(plan.plan,null,2);
 el('plan-summary').textContent=`Compatible and authorized for ${operation.replaceAll('_',' ')}. Review the exact pinned inputs and enforced ceilings before execution.`;el('execute').disabled=busy;
}

function verificationLevel(result){
 const report=result.verification;if(!report)return 'No verifier report.';
 const checks=(report.performed_checks??[]).map(x=>x.mode).join(', ')||'none';
 return `${report.proven_result} · checks: ${checks} · physics replay: ${report.physics_replayed?'yes':'no'}`;
}
function rememberRun(id,result){
 const prior=runHistory.find(x=>x.id===id);const label=`${result.operation??'operation'} · ${id.slice(0,10)}`;
 if(prior)prior.label=label;else runHistory.push({id,label});
 sessionStorage.setItem('worldfuzz-run-history-v1',JSON.stringify(runHistory.slice(-20)));refreshComparison();
}
function refreshComparison(){
 for(const id of ['compare-left','compare-right','saved-result']){const selected=el(id).value;el(id).replaceChildren();for(const run of runHistory){const option=document.createElement('option');option.value=run.id;option.textContent=run.label;el(id).append(option);}if(runHistory.some(x=>x.id===selected))el(id).value=selected;}
 if(runHistory.length>1&&el('compare-left').value===el('compare-right').value)el('compare-right').value=runHistory.at(-1).id;
 el('compare').disabled=runHistory.length<2;
 el('open-result').disabled=runHistory.length===0||busy||runAction;
}
async function renderResult(value){
 const revision=++viewRevision;
 const report=await request(`/v1/results/${value.result_id}/report`),summary=report.summary;
 if(revision!==viewRevision)return;
 currentResult=value;const result=value.result;el('results').replaceChildren();el('downloads').replaceChildren();
 el('run-state').textContent=summary.execution_status;el('task-outcome').textContent=outcomeText(summary);el('verification-level').textContent=verificationLevel(result);el('ci-verdict').textContent=`CI: ${summary.ci_status} · ${summary.task_outcome}. ${summary.meaning}`;
 el('saved-recording').hidden=false;el('saved-recording').textContent=`Saved result record ${value.result_id}. Its manifest hash is ${value.result_sha256}.`;
 (result.records??[]).forEach((record,index)=>renderRecord(el('results'),record,index));
 for(const artifact of result.artifacts??[]){const button=document.createElement('button');button.textContent=`Download ${artifact.name}`;button.onclick=()=>guard(async()=>{const response=await fetch(artifact.href,{headers:{Authorization:`Bearer ${token}`}});if(!response.ok)throw new Error('Artifact integrity or access check failed');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=artifact.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});el('downloads').append(button);}
 const junit=document.createElement('button');junit.textContent='Download CI JUnit';junit.onclick=()=>guard(async()=>{const checked=await request(`/v1/results/${value.result_id}/report`);const url=URL.createObjectURL(new Blob([checked.junit_xml],{type:'application/xml'})),a=document.createElement('a');a.href=url;a.download='worldfuzz.junit.xml';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});el('downloads').append(junit);
 for(const artifact of result.artifacts??[]){if(!artifact.name.endsWith('.json'))continue;const button=document.createElement('button');button.textContent=`Export exact JSON ${artifact.name}`;button.onclick=()=>guard(async()=>{const view=await request(`/v1/results/${value.result_id}/artifact-json`,{name:artifact.name});const url=URL.createObjectURL(new Blob([JSON.stringify(view,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`exact-${artifact.name}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});el('downloads').append(button);}
 el('export-manifest').disabled=false;rememberRun(value.result_id,result);
}

function stopPolling(){clearTimeout(pollTimer);pollTimer=null;pollRevision++;}
function updateRunControls(){
 el('refresh-run').disabled=!runId||runAction;
 el('reconcile-run').disabled=!runId||runAction||!recoveryEnabled||!['queued','running','cancel_requested','incomplete'].includes(currentRun?.status);
 el('resume-run').disabled=!runId||runAction||!recoveryEnabled||currentRun?.status!=='incomplete';
 el('cancel').disabled=runAction||!['queued','running'].includes(currentRun?.status);
 el('execute').disabled=!plan||busy||runAction;refreshComparison();
 el('recovery-scope').textContent=recoveryEnabled?'Recovery uses the existing run and its submitting session. The service rechecks access, pinned inputs and budget. A resumed attempt may restart the operation; it does not imply physics replay.':'Recovery was not enabled for this session. Status and usage remain readable with current access.';
}
function selectRun(id){stopPolling();runId=id;currentRun=null;el('run-monitor').hidden=false;el('run-details').replaceChildren();sessionStorage.setItem('worldfuzz-run',id);updateRunControls();}
async function poll(){
 if(!runId)return;stopPolling();const revision=pollRevision,expected=runId,valid=()=>revision===pollRevision&&expected===runId;
 const run=await request(`/v1/runs/${expected}`);if(!valid())return;
 currentRun=run;busy=!terminal.has(run.status);el('run-monitor').hidden=false;el('run-state').textContent=run.status;updateRunControls();
 message(`${run.status.replaceAll('_',' ')} · ${run.progress.completed_units??0} units complete${run.error?' · '+run.error:''}`);
 renderRunDetails(el('run-details'),run);
 let budget,budgetError;
 try{budget=await request(`/v1/runs/${expected}/budget`);}catch(error){if(!valid())return;if(error.code!=='BUDGET_MISSING')throw error;budgetError='This historical run has no recorded reservation. Usage is unavailable.';}
 if(!valid())return;renderRunDetails(el('run-details'),run,budget,budgetError);
 if(terminal.has(run.status)){
  if(run.result_id){const value=await request(`/v1/results/${run.result_id}`);if(!valid())return;await renderResult(value);}
  if(valid()){refreshComparison();await resources();}return;
 }
 pollTimer=setTimeout(()=>guard(poll),700);
}
async function actOnRun(action){
 if(!runId||runAction)return;
 if(action==='resume'&&currentRun?.status!=='incomplete')return;
 if(['resume','reconcile'].includes(action)&&!recoveryEnabled)return;
 const expected=runId,number=currentRun?.attempts?.at(-1)?.number??0;
 // Stable for this explicit retry, including a lost response or page reload.
 const args=action==='resume'?{idempotency_key:`ui-resume:${expected}:${number}`}:{ };
 stopPolling();runAction=true;updateRunControls();if(action==='resume')clearResult();
 try{await request(`/v1/runs/${expected}/${action}`,args);if(runId===expected)await poll();}
 finally{runAction=false;updateRunControls();}
}

el('profile').onchange=()=>guard(async()=>{const p=profile(),a=adapter();
 importer.setProfile(p.id);
 el('scope').textContent=`State: ${p.state}. Actions: ${p.action}. Observations: ${p.observation}. Outcomes: ${p.outcome}.`;
 el('adapter-info').textContent=`${a.adapter_id} v${a.version} · ${a.execution_mode}. ${a.limitations.join(' ')}`;
 el('operation').replaceChildren();for(const op of p.operations){const o=document.createElement('option');o.value=op;o.textContent=op.replaceAll('_',' ');el('operation').append(o);}operationChanged();
 const builtin=['movingai.octile.v1','robomimic.can-paired.v1','sensor.trajectory.xyz.v1'].includes(p.id);el('shortcuts').hidden=!builtin;
 el('solve').disabled=!p.operations.includes('solve_navigation');el('verify').disabled=!p.operations.includes('verify_episodes');el('export').disabled=!p.operations.includes('export_dataset');el('evaluate').disabled=!p.operations.includes('evaluate_trajectory');await resources();});
el('operation').onchange=()=>operationChanged();
el('prepare-adapter').onclick=()=>guard(()=>prepare(el('operation').value,readParameters()));
for(const id of ['split','asset','threshold','max-seconds','cpu-seconds','memory-mib','artifact-mib']){el(id).oninput=invalidate;el(id).onchange=invalidate;}
function shortcut(operation,parameters){el('operation').value=operation;operationChanged(parameters);return prepare(operation,parameters);}
el('solve').onclick=()=>guard(()=>shortcut('solve_navigation',{}));el('verify').onclick=()=>guard(()=>shortcut('verify_episodes',{filters:filters()}));el('export').onclick=()=>guard(()=>shortcut('export_dataset',{filters:filters()}));
el('evaluate').onclick=()=>guard(async()=>{const revision=selection,selected=profile().id,file=el('asset').files[0];if(!file||file.size>2*1024*1024)throw new Error('Choose a trajectory JSON file up to 2 MiB');const asset=await request('/v1/assets',JSON.parse(await file.text()));if(revision!==selection||selected!==profile().id)return;await prepare('evaluate_trajectory',{asset_id:asset.asset_id,threshold_m:Number(el('threshold').value)});});
el('execute').onclick=()=>guard(async()=>{if(!plan||busy||runAction)return;busy=true;el('execute').disabled=true;el('open-result').disabled=true;try{const run=await request('/v1/runs',{plan_id:plan.plan_id,plan_digest:plan.plan_digest,idempotency_key:plan.plan_id});clearResult();selectRun(run.run_id);el('run-state').textContent=run.status;await poll();}catch(error){busy=false;updateRunControls();throw error;}});
el('cancel').onclick=()=>guard(()=>actOnRun('cancel'));el('more').onclick=()=>guard(()=>resources(false));
el('refresh-run').onclick=()=>guard(poll);el('reconcile-run').onclick=()=>guard(()=>actOnRun('reconcile'));el('resume-run').onclick=()=>guard(()=>actOnRun('resume'));
const comparisonSelection=()=>({left_id:el('compare-left').value,right_id:el('compare-right').value,mode:el('compare-mode').value});
for(const id of ['compare-left','compare-right','compare-mode'])el(id).onchange=()=>el('comparison').replaceChildren();
el('compare').onclick=()=>guard(async()=>{el('comparison').replaceChildren();const selected=comparisonSelection(),compared=await request('/v1/comparisons/query',selected);if(JSON.stringify(selected)!==JSON.stringify(comparisonSelection()))return;renderComparison(el('comparison'),compared);message('Comparison completed by the shared service.');});
el('open-result').onclick=()=>guard(async()=>{if(busy||runAction)return;clearResult();selectRun(el('saved-result').value);await poll();});
el('export-manifest').onclick=()=>guard(async()=>{if(!currentResult)return;const id=currentResult.result_id,response=await fetch(`/v1/results/${id}`,{headers:{Authorization:`Bearer ${token}`}});if(!response.ok){const fault=parseResponseText(await response.text()).error;const error=new Error(fault?.message||'Result export denied');error.code=fault?.code;throw error;}const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=`worldfuzz-result-${id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('Checked result exported as the original API response, preserving exact numeric values.');});
el('sign-in').onclick=async()=>{el('login-error').textContent='';const password=el('login-password').value;el('login-password').value='';try{const operations=['get_capabilities','find_resources','compile_goal','prepare_plan','execute_plan','get_run','get_result','get_result_report','get_budget','cancel_run','compare_results'];if(el('login-import').checked)operations.push('register_sensor_recording','register_trajectory','register_asset');if(el('login-recovery').checked)operations.push('reconcile_run','resume_run');const login=await request('/v1/organization/login',{organization:el('login-organization').value,workspace:el('login-workspace').value,principal:el('login-principal').value,password,operations},false);token=login.token;recoveryEnabled=el('login-recovery').checked;await start();}catch(error){el('login-error').textContent=`${error.code?error.code+': ':''}${error.message}`;}};

async function start(){
 const capabilities=await request('/v1/capabilities');profiles=capabilities.profiles;adapters=capabilities.adapters;el('profile').replaceChildren();for(const p of profiles){const o=document.createElement('option');o.value=p.id;o.textContent=`${p.domain} · ${p.id}`;el('profile').append(o);}
 el('login').hidden=true;el('workspace').hidden=false;try{runHistory=JSON.parse(sessionStorage.getItem('worldfuzz-run-history-v1')||'[]').filter(x=>x&&typeof x.id==='string'&&typeof x.label==='string');}catch{runHistory=[];}refreshComparison();await el('profile').onchange();const savedRun=sessionStorage.getItem('worldfuzz-run');if(savedRun){selectRun(savedRun);await poll();}
}
await guard(async()=>{try{token=(await request('/bootstrap',undefined,false)).token;recoveryEnabled=true;await start();}catch(error){if(error.code!=='ACCESS_DENIED'&&error.code!=='HTTP_403')throw error;el('login').hidden=false;el('workspace').hidden=true;message('Workspace sign in required.');}});
