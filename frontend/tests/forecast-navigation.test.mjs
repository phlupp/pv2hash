import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  FORECAST_LIVE_REFRESH_MS, daySelectionAfterMove, dayShift, forecastDayRefreshInterval,
} from '../src/forecast/dayNavigation.ts';

test('day navigation is calendar-based across month, year and DST changes', () => {
  assert.equal(dayShift('2026-10-31', 1), '2026-11-01');
  assert.equal(dayShift('2026-12-31', 1), '2027-01-01');
  assert.equal(dayShift('2026-03-29', -1), '2026-03-28');
});

test('moving back or forward to today returns to the live day endpoint', () => {
  assert.equal(daySelectionAfterMove('2026-10-16', '2026-10-17', 1), null);
  assert.equal(daySelectionAfterMove('2026-10-18', '2026-10-17', -1), null);
  assert.equal(daySelectionAfterMove('2026-10-17', '2026-10-17', -1), '2026-10-16');
  assert.equal(daySelectionAfterMove('2026-10-17', '2026-10-17', 1), '2026-10-18');
});

test('day chart polls automatically for today and tomorrow, not immutable history', () => {
  const data = { today: '2026-10-17' };
  assert.equal(FORECAST_LIVE_REFRESH_MS, 30_000);
  assert.equal(forecastDayRefreshInterval(null, data), 30_000);
  assert.equal(forecastDayRefreshInterval('2026-10-17', data), 30_000);
  assert.equal(forecastDayRefreshInterval('2026-10-18', data), 30_000);
  assert.equal(forecastDayRefreshInterval('2026-10-16', data), false);
  assert.equal(forecastDayRefreshInterval('2026-10-16'), 30_000); // bootstrap before first fetch
  assert.equal(forecastDayRefreshInterval(null, { today: '2026-10-18' }), 30_000); // midnight rollover
});
