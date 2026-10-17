import {test} from 'node:test';
import {strict as assert} from 'node:assert';
import {instanceNameFromSettings,browserTitle} from '../src/branding.ts';

test('brand and browser tab follow saved instance settings without duplicating PV2Hash',()=>{
  const saved={status:'ok',model:{sections:[{id:'instance',title:'Instanz',fields:[{name:'instance_name',label:'Instanzname',type:'text',value:' HashPi@Phlupp '}]}]}};
  assert.equal(instanceNameFromSettings(saved),'HashPi@Phlupp');
  assert.equal(browserTitle(instanceNameFromSettings(saved)),'PV2Hash - HashPi@Phlupp');
  assert.equal(browserTitle(instanceNameFromSettings(undefined)),'PV2Hash');
  assert.equal(browserTitle('   '),'PV2Hash');
});
