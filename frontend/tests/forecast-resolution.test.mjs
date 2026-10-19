import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { forecastSelectValue } from '../src/forecast/settingValues.ts';

test('forecast resolution selector saves 15/60 minutes as numeric settings', () => {
  assert.equal(forecastSelectValue('resolution_minutes', '15'), 15);
  assert.equal(forecastSelectValue('resolution_minutes', '60'), 60);
  assert.equal(forecastSelectValue('resolution_minutes', null), 15);
});

test('other provider dropdown values stay strings', () => {
  assert.equal(forecastSelectValue('mode','free'), 'free');
  assert.equal(forecastSelectValue('mode','customer'), 'customer');
  assert.equal(forecastSelectValue('mode', null), '');
});
