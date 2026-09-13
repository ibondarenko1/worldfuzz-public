// Present the common service's accounting. No retry eligibility or outcome inference.
const value=x=>x===undefined||x===null?'Not recorded':String(x);
const human=x=>String(x).replaceAll('_',' ');
function node(tag,text){const el=document.createElement(tag);if(text!==undefined)el.textContent=value(text);return el;}
function facts(parent,rows){const list=node('dl');list.className='record-facts';for(const [name,text] of rows)list.append(node('dt',name),node('dd',text));parent.append(list);}
function table(parent,headers,rows){const scroll=node('div'),grid=node('table'),head=node('thead'),body=node('tbody'),tr=node('tr');scroll.className='table-scroll';for(const text of headers)tr.append(node('th',text));head.append(tr);for(const row of rows){const item=node('tr');for(const text of row)item.append(node('td',text));body.append(item);}grid.append(head,body);scroll.append(grid);parent.append(scroll);}

export function renderRunDetails(parent,run,budget,budgetError){
 parent.replaceChildren();
 facts(parent,[['Run',run.run_id],['Execution state',human(run.status)],['Execution error',run.error??'None reported']]);
 if(run.status==='incomplete')parent.append(node('p','This execution is incomplete. Its saved data does not establish a completed task. Resume is explicit and uses the original reservation.'));
 if(run.status==='budget_exhausted')parent.append(node('p','Execution stopped at its resource limit. This is not a confirmed robotics regression.'));
 const attempts=run.attempts??[];
 if(attempts.length){
  parent.append(node('h3','Saved attempts'));
  table(parent,['Attempt','State','Execution error','Saved artifacts'],attempts.map(a=>[a.number,human(a.status),a.error??'None reported',(a.artifacts??[]).map(x=>`${x.name} (${x.bytes} bytes)`).join(', ')||'None recorded']));
 }
 if(!budget){parent.append(node('p',budgetError??'Loading recorded resource usage.'));return;}
 const reservation=budget.reservation,limits=reservation.limits;
 parent.append(node('h3','Original resource reservation'));
 facts(parent,[['Reservation state',human(reservation.state)],['CPU limit (seconds)',limits.cpu_seconds],['Wall limit (seconds)',limits.wall_seconds],['Memory ceiling (bytes)',limits.memory_bytes],['Artifact ceiling (bytes)',limits.artifact_bytes],['Model call limit',limits.model_calls],['Device call limit',limits.device_calls]]);
 parent.append(node('p','Observed usage and conservative holds are separate. A hold after a lost worker is not measured consumption. The service decides whether the remaining reservation permits a retry.'));
 table(parent,['Attempt','Observed CPU (s)','Observed wall (s)','Unmeasured CPU hold (s)','Unmeasured wall hold (s)','Charge basis'],(budget.attempt_accounting??[]).map(a=>[attempts.find(x=>x.attempt_id===a.attempt_id)?.number??a.attempt_id,a.observed_cpu_seconds,a.observed_wall_seconds,a.unmeasured_cpu_hold_seconds,a.unmeasured_wall_hold_seconds,human(a.charge_basis)]));
 const detail=node('details');detail.append(node('summary','Recorded usage by phase and remaining unknowns'));
 table(detail,['Phase','Resource','Observed value','Unit'],(reservation.actual_usage??[]).map(x=>[human(x.phase),human(x.resource),x.value,x.unit]));
 const unknown=node('ul');for(const x of reservation.unknown_usage??[])unknown.append(node('li',`${human(x.resource)} · ${human(x.phase)}: ${x.reason}`));detail.append(unknown);parent.append(detail);
}
