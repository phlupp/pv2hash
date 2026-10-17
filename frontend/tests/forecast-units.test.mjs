import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { wattsToKw, formatKw, formatKwTick } from '../src/forecast/units.ts';

test('chart converts stored watts into kW without changing the underlying measurement', () => {
  for (const [w, kw] of [[0, 0], [500, 0.5], [1250, 1.25], [13700, 13.7]]) {
    assert.equal(wattsToKw(w), kw);
  }
});

test('axis ticks always use kW (even for fractional kW) and tooltip carries unit', () => {
  assert.equal(formatKwTick(0.25), '0,25');
  assert.equal(formatKwTick(1.5), '1,5');
  assert.equal(formatKw(0.25), '0,25 kW');
  assert.equal(formatKw(3.124), '3,12 kW');
});
