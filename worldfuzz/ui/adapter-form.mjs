// Render the registry's bounded schema subset. Values remain data, never HTML/code.
export function validateValue(value,schema,path='parameters') {
 const fail=reason=>{throw new Error(`${path}: ${reason}`);};
 const type=schema.type;
 if(type==='object') {
  if(value===null||Array.isArray(value)||typeof value!=='object')fail('expected object');
  for(const key of schema.required??[])if(!Object.hasOwn(value,key))fail(`missing ${key}`);
  for(const [key,item] of Object.entries(value)) {
   if(!Object.hasOwn(schema.properties??{},key))fail(`unknown field ${key}`);
   validateValue(item,schema.properties[key],`${path}.${key}`);
  }
 } else if(type==='array') {
  if(!Array.isArray(value))fail('expected array');
  if(value.length<(schema.minItems??0)||value.length>(schema.maxItems??Infinity))fail('array length outside limits');
  value.forEach((item,i)=>validateValue(item,schema.items,`${path}[${i}]`));
 } else if(type==='number'||type==='integer') {
  if(typeof value!=='number'||!Number.isFinite(value)||(type==='integer'&&!Number.isInteger(value)))fail(`expected ${type}`);
  if(value<(schema.minimum??-Infinity)||value>(schema.maximum??Infinity))fail('number outside limits');
 } else if(type==='string') {
  if(typeof value!=='string')fail('expected string');
  const length=[...value].length;
  if(length<(schema.minLength??0)||length>(schema.maxLength??Infinity))fail('text length outside limits');
 } else if(type==='boolean') {if(typeof value!=='boolean')fail('expected boolean');}
 else fail('unsupported field type');
 if(schema.enum&&!schema.enum.some(item=>JSON.stringify(item)===JSON.stringify(value)))fail('choose an allowed value');
 return value;
}

function scalarControl(field,onchange,initial) {
 const input=document.createElement(field.enum||field.type==='boolean'?'select':'input');
 if(field.enum||field.type==='boolean') {
  const empty=document.createElement('option');empty.value='';empty.textContent='Select a value';input.append(empty);
  for(const value of field.enum??[true,false]) {const o=document.createElement('option');o.value=JSON.stringify(value);o.textContent=String(value);input.append(o);}
 } else {
  input.type=['number','integer'].includes(field.type)?'number':'text';
  if(input.type==='number') {input.step=field.type==='integer'?'1':'any';if(field.minimum!==undefined)input.min=field.minimum;if(field.maximum!==undefined)input.max=field.maximum;}
 }
 if(initial!==undefined)input.value=field.enum||field.type==='boolean'?JSON.stringify(initial):String(initial);
 input.oninput=onchange;input.onchange=onchange;
 return {node:input,read(path,required) {
  const raw=input.value;
  if(raw==='') {if(required)throw new Error(`${path}: value required`);return undefined;}
  if(field.type==='string'&&!field.enum)return raw;
  if(['number','integer'].includes(field.type)&&!field.enum)return Number(raw);
  return JSON.parse(raw);
 }};
}

function control(field,onchange,path,required=false,initial) {
 if(!['object','array'].includes(field.type))return scalarControl(field,onchange,initial);
 if(field.type==='object') {
  const group=document.createElement('fieldset');group.className='object-control';
  const children=[];
  for(const [name,child] of Object.entries(field.properties??{})) {
   const childRequired=(field.required??[]).includes(name),wrapper=document.createElement('label');
   const title=document.createElement('span');title.textContent=`${name}${childRequired?' (required)':' (optional)'}`;
   const item=control(child,onchange,`${path}.${name}`,childRequired,initial?.[name]);wrapper.append(title,item.node);group.append(wrapper);
   children.push({name,item,required:childRequired});
  }
  return {node:group,read(readPath,requiredObject) {
   const result=Object.create(null);
   for(const child of children) {const value=child.item.read(`${readPath}.${child.name}`,child.required);if(value!==undefined)result[child.name]=value;}
   if(!requiredObject&&!Object.keys(result).length)return undefined;
   return result;
  }};
 }
 const group=document.createElement('div');group.className='array-control';
 const items=document.createElement('div');items.className='array-items';
 const add=document.createElement('button');add.type='button';add.textContent='Add item';group.append(items,add);
 const rows=[];
 function addItem(initialValue) {
  if(rows.length>=(field.maxItems??Infinity))return;
  const row=document.createElement('div');row.className='array-item';
  const item=control(field.items,onchange,`${path}[${rows.length}]`,true,initialValue);
  const remove=document.createElement('button');remove.type='button';remove.textContent='Remove';
  const entry={row,item};remove.onclick=()=>{const index=rows.indexOf(entry);if(index>=0){rows.splice(index,1);row.remove();onchange();}add.disabled=rows.length>=(field.maxItems??Infinity);};
  row.append(item.node,remove);items.append(row);rows.push(entry);add.disabled=rows.length>=(field.maxItems??Infinity);onchange();
 }
 add.onclick=()=>addItem();
 for(let i=0;i<Math.max(initial?.length??0,field.minItems??0);i++)addItem(initial?.[i]);
 return {node:group,read(readPath,requiredArray) {
  if(!rows.length&&!requiredArray)return undefined;
  return rows.map((entry,index)=>entry.item.read(`${readPath}[${index}]`,true));
 }};
}

const fieldNames={episode_id:'Saved episode',asset_id:'Registered recording',case_indices:'Published task indices',max_cases:'Maximum tasks',algorithm:'Planner',start_index:'First recorded frame',frame_count:'Recorded frames to inspect',stride:'Frame stride',max_messages:'Maximum messages',threshold_m:'Position error threshold (metres)'};

export function renderForm(container,schema,onchange=()=>{},initial={}) {
 validateValue(initial,{...schema,required:[]});
 container.replaceChildren();
 const fields=[];
 for(const [name,field] of Object.entries(schema.properties??{})) {
  const label=document.createElement('label'),required=(schema.required??[]).includes(name);
  const title=document.createElement('span');title.textContent=`${fieldNames[name]??name.replaceAll('_',' ')}${required?' (required)':' (optional)'}`;
  const item=control(field,onchange,`parameters.${name}`,required,initial[name]);item.node.setAttribute('data-field',name);label.append(title,item.node);container.append(label);
  fields.push({name,item,required});
 }
 return ()=>{
  const result=Object.create(null);
  for(const {name,item,required} of fields) {const value=item.read(`parameters.${name}`,required);if(value!==undefined)result[name]=value;}
  return validateValue(result,schema);
 };
}
