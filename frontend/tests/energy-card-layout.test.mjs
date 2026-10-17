import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { energyCardLayout } from '../src/dashboard/energyCardLayout.ts';

test('PV card hidden when unconfigured or disabled, battery remains', () => {
  for (const state of [undefined, null, false]) {
    assert.deepEqual(energyCardLayout(state, true), { showPv: false, columns: 2 });
    assert.deepEqual(energyCardLayout(state, false), { showPv: false, columns: 1 });
  }
});

test('enabled PV always visible even while offline; grid layout adapts', () => {
  assert.deepEqual(energyCardLayout(true, false), { showPv: true, columns: 2 });
  assert.deepEqual(energyCardLayout(true, true), { showPv: true, columns: 3 });
});
