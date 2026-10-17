import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { fieldsToValues, formForFields, validateFields } from '../src/miners/model.ts';
import { unpack } from '../src/miners/api.ts';

test('miner metadata fields serialize dotted profile names and on/off checkboxes', () => {
  const fields = [
    { name:'name', label:'Name', type:'text', value:'Test' },
    { name:'monitor_enabled', label:'Verbindung', type:'checkbox', value:true },
    { name:'control_enabled', label:'In Regelung', type:'checkbox', value:false },
    { name:'profiles.p1.power_w',label:'Profil p1',type:'number',value:900 },
    { name:'settings.port',label:'Port',type:'number',value:4028 },
    { name:'profiles.p2.power_w',label:'Profil p2',type:'number',value:1800,read_only:true },
  ];
  const values=fieldsToValues(fields);
  const form=formForFields(fields,values);
  assert.equal(form.get('monitor_enabled'),'on');
  assert.equal(form.has('control_enabled'),false);
  assert.equal(form.get('profiles.p1.power_w'),'900');
  assert.equal(form.get('settings.port'),'4028');
  assert.equal(form.has('profiles.p2.power_w'),false);
});

test('stored miner passwords stay redacted and preserve unchanged-on-blank semantics', () => {
  const fields=[{name:'settings.password',label:'Passwort',type:'password',required:true,value:''}];
  const values=fieldsToValues(fields);
  assert.equal(values['settings.password'],'');
  assert.equal(validateFields(fields,values,true),null);
  assert.match(validateFields(fields,values,false)??'',/Passwort/);
  assert.equal(formForFields(fields,values).get('settings.password'),'');
});

test('new miner driver fields validate required host and numeric bounds', () => {
  const fields=[
    {name:'name',label:'Name',type:'text',required:true,value:'Miner'},
    {name:'host',label:'Host',type:'text',required:true,value:'192.168.10.2'},
    {name:'settings.port',label:'API-Port',type:'number',required:true,min:1,max:65535,value:4028},
  ];
  const base=fieldsToValues(fields);
  assert.equal(validateFields(fields,base,false),null);
  assert.match(validateFields(fields,{...base,host:''},false)??'',/Host/);
  assert.match(validateFields(fields,{...base,'settings.port':'90000'},false)??'',/Ungültiger Zahlenwert/);
  assert.match(validateFields(fields,{...base,'settings.port':'abc'},false)??'',/Ungültiger Zahlenwert/);
});

test('non-JSON server error reports HTTP status instead of a misleading JSON syntax error', async () => {
  const error = new Response('Internal Server Error', {status: 500, headers:{'content-type':'text/plain'}});
  await assert.rejects(unpack(Promise.resolve(error)), /HTTP 500.*JSON-Antwort/);
  const success = new Response(JSON.stringify({status:'ok',miners:[]}),{
    status:200,headers:{'content-type':'application/json'},
  });
  assert.deepEqual(await unpack(Promise.resolve(success)),{status:'ok',miners:[]});
});
