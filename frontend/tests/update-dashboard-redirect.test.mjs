import {test} from 'node:test';
import {strict as assert} from 'node:assert';
import {
  DASHBOARD_AFTER_UPDATE,DASHBOARD_RELOAD_DELAY_MS,
  observedUpdateStart,successfulUpdateMatches,installedUpdateMatches,
} from '../src/update/dashboardRedirect.ts';

const starting={status:'starting',running:true,started_at:'2026-10-17T16:30:00Z',target_tag:'r2026.10.17.4',target_version_full:'0.10.3'};
const success={...starting,status:'success',running:false};
const build={status:'ok',release_id:'r2026.10.17.4',core_version:'0.10.3'};

test('a started and installed update triggers a full navigation to the dashboard',()=>{
  const armed=observedUpdateStart(starting);
  assert.deepEqual(armed,{started_at:starting.started_at,target_tag:starting.target_tag,target_version_full:starting.target_version_full});
  assert.equal(successfulUpdateMatches(armed,success),true);
  assert.equal(installedUpdateMatches(armed,success,build),true);
  assert.equal(DASHBOARD_AFTER_UPDATE,'/ui-next/');
  assert.equal(DASHBOARD_RELOAD_DELAY_MS,3000);
});

test('reopening the old success log never starts an automatic reload',()=>{
  assert.equal(observedUpdateStart(success),null);
  assert.equal(installedUpdateMatches(null,success,build),false);
  assert.equal(observedUpdateStart({status:'idle'}),null);
});

test('a stale or unrelated success cannot navigate away',()=>{
  const armed=observedUpdateStart(starting);
  assert.equal(successfulUpdateMatches(armed,{...success,started_at:'past'}),false);
  assert.equal(successfulUpdateMatches(armed,{...success,target_tag:'r2026.10.17.3'}),false);
  assert.equal(installedUpdateMatches(armed,{...success,status:'error'},build),false);
  assert.equal(installedUpdateMatches(armed,{...starting,status:'running'},build),false);
});

test('the installed Core and exact release must be confirmed before redirect',()=>{
  const armed=observedUpdateStart(starting);
  assert.equal(installedUpdateMatches(armed,success,undefined),false);
  assert.equal(installedUpdateMatches(armed,success,{...build,release_id:'r2026.10.17.3'}),false);
  assert.equal(installedUpdateMatches(armed,success,{...build,core_version:'0.10.2'}),false);
  assert.equal(installedUpdateMatches(armed,success,{...build,status:'error'}),false);
});

test('legacy tags are checked against Core version',()=>{
  const start={status:'running',started_at:'t1',target_tag:'v0.10.0',target_version_full:'0.10.0'};
  const armed=observedUpdateStart(start);
  assert.equal(installedUpdateMatches(armed,{...start,status:'success'}, {status:'ok',core_version:'0.10.0'}),true);
  assert.equal(installedUpdateMatches(armed,{...start,status:'success'}, {status:'ok',core_version:'0.9.5'}),false);
});
