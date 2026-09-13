// Presentation of server evidence only. No metric, label or success recomputation.
const text=value=>value===null||value===undefined?'Not recorded':typeof value==='boolean'?(value?'Yes':'No'):String(value);
const human=value=>String(value).replaceAll('_',' ');
function node(tag,value){const item=document.createElement(tag);if(value!==undefined)item.textContent=text(value);return item;}
function facts(parent,rows){const list=node('dl');list.className='record-facts';for(const [name,value] of rows){if(value!==undefined)list.append(node('dt',name),node('dd',value));}parent.append(list);}
function details(parent,value){const expanded=node('details');expanded.append(node('summary','Recorded fields (JSON; large integers shown as decimal text)'),node('pre',JSON.stringify(value,null,2)));parent.append(expanded);}
function table(parent,headers,rows){const view=node('div'),grid=node('table'),head=node('thead'),body=node('tbody'),tr=node('tr');view.className='table-scroll';for(const header of headers)tr.append(node('th',header));head.append(tr);for(const row of rows){const r=node('tr');for(const value of row)r.append(node('td',value));body.append(r);}grid.append(head,body);view.append(grid);parent.append(view);}

export function outcomeText(summary){
 const names={not_evaluated:'Task outcome not evaluated',criterion_satisfied:'Requested criterion satisfied',violation_found:'Requirement violation found',integrity_verified:'Record integrity checked; task outcome not evaluated'};
 return names[summary.task_outcome]??human(summary.task_outcome);
}

export function renderRecord(parent,record,index){
 const article=node('article');article.className='record';
 if(record.case_index!==undefined){
  article.append(node('h3',`Published navigation task movingai:${record.case_index}`));
  const c=record.check??{};
  facts(article,[['Case index (zero-based)',record.case_index],['Recorded outcome',record.outcome],['Route valid',c.route_valid],['Matches published length',c.matches_published_optimum],['Measured route length',c.measured_length],['Evidence',record.evidence]]);
 }else if(record.frames){
  article.append(node('h3',`Saved episode · ${record.source_key??record.episode_id}`));
  facts(article,[['Split',record.split],['Frames shown',record.frames.length],['State values per frame',record.state_vector_width],['Action values per frame',record.action_vector_width],['Recorded control frequency (Hz)',record.control_frequency_hz],['Simulator replay performed',record.physics_replayed],['Task outcome independently verified',record.task_outcome_independently_verified]]);
  article.append(node('p','These are recorded values. Source reward and done are not an independent success check.'));
  table(article,['Source frame','Recorded action','Source reward','Source done'],record.frames.map(f=>[f.source_index,f.source_action?.join(', '),f.source_reward,f.source_done]));
 }else if(record.returned_messages){
  article.append(node('h3','Saved sensor recording'));
  facts(article,[['Total messages',record.total_messages],['Messages shown',record.returned_messages.length],['Coordinate frame',record.frame],['Units',record.units?.join(', ')],['ROS format',record.ros_version],['Control actions present',record.control_actions_present],['Flight outcome',record.flight_outcome],['Source revision',record.source_revision]]);
  table(article,['Sequence','Log timestamp (ns)','Publish timestamp (ns)','Payload bytes'],record.returned_messages.map(m=>[m.sequence,m.log_time_ns,m.publish_time_ns,m.data_bytes]));
 }else if(record.rmse_m!==undefined){
  article.append(node('h3','Trajectory evaluation'));
  facts(article,[['Position RMSE (metres)',record.rmse_m],['Threshold (metres)',record.threshold_m],['Samples',record.samples],['Recorded outcome',record.outcome],['Coordinate frame',record.frame],['Reference origin',record.reference_origin],['Protocol',record.protocol],['Control outcome verified',record.control_outcome_verified]]);
 }else if(record.episode_id){
  article.append(node('h3','Saved episode record'));
  facts(article,[['Episode',record.episode_id],['Split',record.split],['Source label',record.source_label],['Source status',record.source_status],['Evidence',record.evidence],['Simulator replay performed',record.physics_replayed]]);
 }else{
  article.append(node('h3',`Result item ${index+1}`));
  facts(article,Object.entries(record).filter(([,v])=>v===null||typeof v!=='object'||(Array.isArray(v)&&v.length<=8&&v.every(x=>typeof x!=='object'))).map(([k,v])=>[human(k),Array.isArray(v)?v.join(', '):v]));
 }
 if(record.unknowns?.length)article.append(node('p',`Still unknown: ${record.unknowns.join('; ')}.`));
 details(article,record);parent.append(article);
}

export function renderComparison(parent,value){
 parent.replaceChildren();
 facts(parent,[['Comparable under the saved protocol',value.comparable],['Same recorded measurements',value.same_records],['Equivalent outcomes',value.equivalent_outcomes],['Compared tasks',value.coverage?`${value.coverage.compared} of ${value.coverage.expected}`:undefined],['Regressions',value.summary?.regressions],['Improvements',value.summary?.improvements],['Scope',value.scope]]);
 if(value.paired_results?.length)table(parent,['Resource ID','Case index (zero-based)','Left outcome','Right outcome','Left length','Right length','Length change'],value.paired_results.map(r=>[`movingai:${r.case_index}`,r.case_index,r.left_outcome,r.right_outcome,r.left_length,r.right_length,r.length_delta_right_minus_left]));
 details(parent,value);
}
