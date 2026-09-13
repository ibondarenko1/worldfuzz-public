// Host supplies an authenticated request function. No token in URLs or storage.
export function mountResult(container,{request,resultId}) {
 const section=document.createElement('section'),title=document.createElement('h2'),facts=document.createElement('dl'),status=document.createElement('p'),refreshButton=document.createElement('button');
 title.textContent='WorldFuzz result';refreshButton.textContent='Refresh result';status.setAttribute('role','status');section.append(title,facts,status,refreshButton);container.replaceChildren(section);
 let disposed=false,revision=0;
 async function refresh(){
  const current=++revision;facts.replaceChildren();status.textContent='Checking access and result integrity…';
  try{
   const response=await request(`/v1/results/${encodeURIComponent(resultId)}/report`);
   if(disposed||current!==revision)return;
   if(response.status!=='ok')throw new Error(response.error?.code||'Result unavailable');
   const s=response.data.summary;
   for(const [label,value] of [['Run',s.execution_status],['Task outcome',s.task_outcome],['Requested check',s.required_check],['CI status',s.ci_status],['Scope',s.verification_scope],['Result SHA-256',s.result_sha256]]){const name=document.createElement('dt'),item=document.createElement('dd');name.textContent=label;item.textContent=value;facts.append(name,item);}
   status.textContent=`Saved result. ${s.meaning}`;
  }catch(error){if(!disposed&&current===revision){facts.replaceChildren();status.textContent=`Result unavailable: ${error.message}`;}}
 }
 refreshButton.onclick=refresh;
 return {refresh,destroy(){disposed=true;revision++;container.replaceChildren();}};
}
