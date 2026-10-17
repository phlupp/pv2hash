import {strict as assert} from 'node:assert';
import {test} from 'node:test';
import {fieldValues,validateConfig} from '../src/configuration/types.ts';
import {configJson} from '../src/configuration/api.ts';

test('generic settings schema maps text, selects, numeric strings and checkboxes',()=>{
  const fields=[
    {name:'instance_name',label:'Instanzname',type:'text',value:'PV2Hash',required:true,layout:{width:'half'}},
    {name:'refresh_seconds',label:'Aktualisieren',type:'number',value:5,min:1,max:60},
    {name:'datalogger_enabled',label:'DataLogger',type:'checkbox',value:true},
    {name:'policy_mode',label:'Regelmodus',type:'select',value:'coarse',options:[{value:'coarse',label:'Coarse'}]},
    {name:'instance_id',label:'UUID',type:'text',value:'uuid',disabled:true},
  ];
  const values=fieldValues(fields);
  assert.deepEqual(values,{
    instance_name:'PV2Hash',refresh_seconds:'5',datalogger_enabled:true,policy_mode:'coarse',instance_id:'uuid',
  });
  assert.equal(validateConfig(fields,values),null);
  assert.match(validateConfig(fields,{...values,instance_name:''})||'',/Instanzname/);
  assert.match(validateConfig(fields,{...values,refresh_seconds:'0'})||'',/Aktualisieren/);
  assert.match(validateConfig(fields,{...values,refresh_seconds:'not-a-number'})||'',/Aktualisieren/);
});

test('password stays blank in existing exporter edit; new exporter requires token',()=>{
  const fields=[
    {name:'url',label:'InfluxDB URL',type:'url',value:'https://influx.local',required:true},
    {name:'token',label:'API-Token',type:'password',value:'',required:true},
  ];
  const values=fieldValues(fields);
  assert.equal(values.token,'');
  assert.equal(validateConfig(fields,values,true),null);
  assert.match(validateConfig(fields,values,false)||'',/API-Token/);
});

test('settings API reports HTTP failures without exposing JSON parse exceptions',async()=>{
  const broken=new Response('Internal Server Error',{status:500,headers:{'content-type':'text/plain'}});
  await assert.rejects(configJson(Promise.resolve(broken)),/HTTP 500.*JSON/);
  const ok=new Response(JSON.stringify({status:'ok',model:{sections:[]}}),{
    status:200,headers:{'content-type':'application/json'},
  });
  assert.deepEqual(await configJson(Promise.resolve(ok)),{status:'ok',model:{sections:[]}});
});
