import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { forecastAfterSave, syncedForecastDraft } from '../src/forecast/saveSync.ts';

test('a new confirmed forecast snapshot replaces the old draft, without reload', () => {
  const old = {enabled: false, inverters: [{id:'wr1', generators: []}]};
  const saved = {enabled: true, inverters: [{id:'wr1', generators: [{id:'g1'}]}]};
  assert.equal(syncedForecastDraft(saved, old, false), saved);
  assert.equal(syncedForecastDraft(undefined, old, false), old);
  assert.equal(syncedForecastDraft(saved, null, false), saved);
});

test('periodic forecast config reload must never overwrite unsaved form data', () => {
  const edited = {refresh_minutes: 30};
  const fromServer = {refresh_minutes: 60};
  assert.equal(syncedForecastDraft(fromServer, edited, true), edited);
});

test('after saving enabled settings, the forecast is fetched once', async () => {
  let fetches = 0;
  const result = await forecastAfterSave(true, async () => {
    fetches += 1;
    return {future_points: 37, inverters: 2};
  });
  assert.equal(fetches, 1);
  assert.deepEqual(result, {attempted: true, result: {future_points: 37, inverters: 2}});
});

test('after disabling forecast, save must not trigger weather requests', async () => {
  let fetches = 0;
  const result = await forecastAfterSave(false, async () => {
    fetches += 1;
    return {};
  });
  assert.equal(fetches, 0);
  assert.deepEqual(result, {attempted: false});
});

test('failed forecast refresh does not turn a successful config save into a failure', async () => {
  const error = new Error('weather unavailable');
  const result = await forecastAfterSave(true, async () => { throw error; });
  assert.equal(result.attempted, true);
  assert.equal(result.error, error);
});
