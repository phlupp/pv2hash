import {test} from 'node:test';
import {strict as assert} from 'node:assert';
import {socketValuesFrom,validateSocketAssignment,setSocketFormValues,formatSocketPower,formatSocketEnergy} from '../src/miners/socketAssignment.ts';

test('miner socket form keeps the configured association and countdowns',()=>{
  const state=socketValuesFrom({socket:{socket_id:'s1',mode:'switching',enabled:true,startup_delay_seconds:60,power_off_after_off_seconds:3600}});
  assert.equal(state.socket_id,'s1');
  assert.equal(state.mode,'switching');
  assert.equal(validateSocketAssignment(state),null);
  const form=new FormData();setSocketFormValues(form,state);
  assert.equal(form.get('socket.socket_id'),'s1');
  assert.equal(form.get('socket.power_off_after_off_seconds'),'3600');
});
test('removing a socket requires explicit form key; bad timings blocked before save',()=>{
  const old=new FormData();
  assert.equal(old.has('socket.socket_id'),false); // other miner edits preserve previous socket
  const values=socketValuesFrom();values.socket_id='';setSocketFormValues(old,values);
  assert.equal(old.get('socket.socket_id'),'');
  const bad={...values,socket_id:'s1',power_off_after_off_seconds:'599'};
  assert.match(validateSocketAssignment(bad)||'',/600/);
  assert.equal(validateSocketAssignment({...bad,power_off_after_off_seconds:'600'}),null);
});
test('socket telemetry formats real numbers and never invents missing values',()=>{
  assert.equal(formatSocketPower(16.3),'16,3 W');
  assert.equal(formatSocketEnergy(0.7843),'0,784 kWh');
  assert.equal(formatSocketEnergy(undefined),'—');
});
