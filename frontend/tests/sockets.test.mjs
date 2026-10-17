import {strict as assert} from 'node:assert';
import {test} from 'node:test';
import {
  initialSocketValues,serializeSocketValues,validateSocket,
} from '../src/sockets/model.ts';
import {socketApiJson} from '../src/sockets/api.ts';

const simulator={
  id:'simulator',label:'Simulator Socket',default_name:'Simulator Socket',default_host:'simulator.local',
  fields:[
    {name:'reachable',label:'Erreichbar',type:'checkbox',default:true,width:'quarter'},
    {name:'on_power_w',label:'Ein',type:'number',default:50,width:'quarter'},
    {name:'standby_power_w',label:'Aus',type:'number',default:0,width:'quarter'},
  ],
};
const tasmota={
  id:'tasmota_http',label:'Tasmota HTTP',default_name:'Tasmota Socket',default_host:'',
  fields:[
    {name:'port',label:'Port',type:'number',default:80,min:1,max:65535},
    {name:'relay',label:'Relais',type:'number',default:1,min:1},
    {name:'use_energy',label:'Leistung lesen',type:'checkbox',default:true},
    {name:'username',label:'Benutzer',type:'text',default:''},
    {name:'password',label:'Passwort',type:'password',default:''},
  ],
};

test('simulator creation uses defaults and produces the legacy HTML checkbox format',()=>{
  const values=initialSocketValues(simulator);
  assert.equal(validateSocket(simulator,values),null);
  assert.equal(values.reachable,true);
  const form=serializeSocketValues(simulator,values,false);
  assert.equal(form.get('driver'),'simulator');
  assert.equal(form.get('reachable'),'on');
  assert.equal(form.get('on_power_w'),'50');
  assert.equal(form.has('priority'),false);
});
test('existing socket with disabled flags omits checkboxes and retains password by sentinel',()=>{
  const config={
    id:'s-1234',driver:'tasmota_http',name:'Steckdose',host:'192.168.10.54',priority:12,
    monitor_enabled:false,control_enabled:false,password_configured:true,
    settings:{port:80,relay:1,use_energy:true,username:'operator'},
  };
  const values=initialSocketValues(tasmota,config);
  const form=serializeSocketValues(tasmota,{...values,use_energy:false},true);
  assert.equal(form.get('password'),'__KEEP__');
  assert.equal(form.has('use_energy'),false);
  assert.equal(form.has('monitor_enabled'),false);
  assert.equal(form.get('priority'),'12');
  assert.equal(form.get('username'),'operator');
});
test('invalid host and out-of-range ports are blocked for Tasmota but simulator may use a dummy host',()=>{
  const v=initialSocketValues(tasmota);
  assert.match(validateSocket(tasmota,v)||'',/Host/);
  assert.equal(validateSocket(tasmota,{...v,host:'192.168.1.2'}),null);
  assert.match(validateSocket(tasmota,{...v,host:'192.168.1.2',port:'70000'})||'',/Port/);
  assert.match(validateSocket(tasmota,{...v,host:'192.168.1.2',relay:'0'})||'',/Relais/);
  assert.equal(validateSocket(simulator,{...initialSocketValues(simulator),host:''}),null);
});
test('socket API errors reject non-JSON and parseable API failures',async()=>{
  await assert.rejects(socketApiJson(Promise.resolve(new Response('Internal Server Error',{status:500,headers:{'content-type':'text/plain'}}))),/HTTP 500/);
  await assert.rejects(socketApiJson(Promise.resolve(new Response(JSON.stringify({status:'error',message:'Socket nicht gefunden.'}),{status:404,headers:{'content-type':'application/json'}}))),/Socket nicht gefunden/);
});
