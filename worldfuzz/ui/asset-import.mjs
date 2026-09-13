// File selection and bounded transport only. Parsing, rights and storage stay in the service.
const FORMATS={
 'sensor.mcap.aerial.v1':{kind:'mcap',route:'/v1/sensor-recordings',operation:'inspect_sensor_recording'},
 'sensor.trajectory.xyz.v1':{kind:'tum',route:'/v1/trajectory-assets',operation:'evaluate_trajectory'}
};
const MIB=1048576;
function node(tag,text){const value=document.createElement(tag);if(text!==undefined)value.textContent=text;return value;}
function toBase64(buffer){const bytes=new Uint8Array(buffer);let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(binary);}

export function mountImport(container,{request,onImported,onChange=()=>{},onError=()=>{}}){
 const body=node('details'),title=node('summary','Add your recording'),intro=node('p'),fields=node('div'),specific=node('div'),status=node('p'),submit=node('button','Add recording');
 fields.className='grid';specific.className='grid';status.setAttribute('role','status');submit.type='button';
 body.append(title,intro,fields,specific,submit,status);container.replaceChildren(body);
 const controls={},wrappers={};let profileId=null,revision=0,pending=false;
 const changed=()=>{revision++;status.textContent='';onChange();};
 function field(parent,key,label,{type='text',values,accept}={}){
  const wrapper=node('label'),text=node('span',label),input=node(values?'select':'input');
  if(values){for(const [value,label] of values){const option=node('option',label);option.value=value;input.append(option);}}
  else {input.type=type;if(type!=='file')input.maxLength=256;if(accept)input.accept=accept;}
  input.setAttribute('data-import-field',key);input.oninput=changed;input.onchange=changed;wrapper.append(text,input);parent.append(wrapper);controls[key]=input;wrappers[key]=wrapper;
 }
 field(fields,'name','Recording name');field(fields,'source_url','Source URL, including a version when available');
 field(fields,'data_license','Data license or source terms');field(fields,'code_license','Source code license, or unknown');field(fields,'frame','Coordinate frame, or unknown');
 field(specific,'source_revision','Source revision');
 field(specific,'platform','Declared robot type',{values:[['','Choose the recorded robot type'],['aerial','Aerial'],['ground','Ground'],['manipulator','Manipulator'],['unknown','Unknown']]});
 field(specific,'units','Recorded units, separated by commas');
 field(specific,'ros_version','ROS serialization',{values:[['','Choose the recording format'],['ros1','ROS 1'],['ros2','ROS 2']]});
 field(specific,'mcap','MCAP file, up to 1 MiB',{type:'file',accept:'.mcap'});
 field(specific,'prediction','Prediction, TUM text file, up to 500 KB',{type:'file',accept:'.txt,.tum,.traj,text/plain'});
 field(specific,'reference','Reference, TUM text file, up to 500 KB',{type:'file',accept:'.txt,.tum,.traj,text/plain'});
 function text(key){const value=controls[key].value.trim();if(!value||[...value].length>256)throw new Error(`${wrappers[key].children[0].textContent}: enter 1 to 256 characters`);return value;}
 function file(key,maximum){const value=controls[key].files[0];if(!value||value.size===0||value.size>maximum)throw new Error(`${wrappers[key].children[0].textContent}: choose a nonempty file within the stated limit`);return value;}
 function setProfile(id){
  profileId=id;revision++;status.textContent='';const format=FORMATS[id];container.hidden=!format;if(!format)return;
  const isMcap=format.kind==='mcap';
  intro.textContent=isMcap?'Add a small ROS recording. Supply its source terms, units and coordinate frame. Registration checks structure; it does not establish flight success.':'Add prediction and reference files with columns timestamp tx ty tz qx qy qz qw. Positions must already be in metres in the same frame, with matching timestamps. No automatic alignment is applied.';
  for(const key of ['source_revision','platform','units','ros_version','mcap'])wrappers[key].hidden=!isMcap;
  for(const key of ['prediction','reference'])wrappers[key].hidden=isMcap;
 }
 async function upload(){
  if(pending)return;const selected=profileId,format=FORMATS[selected];if(!format)return;
  pending=true;submit.disabled=true;const current=revision;status.textContent='Reading selected files…';
  try{
   const payload=Object.fromEntries(['name','source_url','data_license','code_license','frame'].map(key=>[key,text(key)]));
   if(format.kind==='mcap'){
    const recording=file('mcap',MIB);
    Object.assign(payload,{source_revision:text('source_revision'),platform:text('platform'),ros_version:text('ros_version'),units:text('units').split(',').map(x=>x.trim()).filter(Boolean)});
    if(!payload.units.length||payload.units.length>16)throw new Error('Enter between 1 and 16 recorded units');
    payload.mcap_base64=toBase64(await recording.arrayBuffer());
   }else{
    const prediction=file('prediction',500000),reference=file('reference',500000),decoder=new TextDecoder('utf-8',{fatal:true});
    Object.assign(payload,{units:'m',format:'tum.v1',prediction_text:decoder.decode(await prediction.arrayBuffer()),reference_text:decoder.decode(await reference.arrayBuffer())});
   }
   if(current!==revision||selected!==profileId){if(selected===profileId)status.textContent='Inputs changed. Review the files and add the recording again.';return;}
   if(new TextEncoder().encode(JSON.stringify(payload)).length>2*MIB)throw new Error('Recording and metadata exceed the service request limit');
   status.textContent='Adding recording to this workspace…';const stored=await request(format.route,payload);
   if(current!==revision||selected!==profileId)return; // The saved source remains; do not replace a newer form selection.
   await onImported({profile:selected,operation:format.operation,assetId:stored.asset_id});
   if(current===revision&&selected===profileId)status.textContent='Recording added. Check compatibility and limits before running.';
  }catch(error){if(current===revision){status.textContent=`${error.code?error.code+': ':''}${error.message}`;onError(error);}}
  finally{pending=false;submit.disabled=false;}
 }
 submit.onclick=upload;setProfile(null);return {setProfile,upload};
}
