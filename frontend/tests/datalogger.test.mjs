import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { clusterEvents } from '../src/datalogger/cluster.ts';
import { previousWindowEnd, nextWindowEnd, RANGE_MS, RANGES } from '../src/datalogger/time.ts';

test('history window moves by one selected range and returns to live at now', () => {
  const now = Date.parse('2026-10-17T10:00:00.000Z');
  for (const range of RANGES) {
    const prior = previousWindowEnd(new Date(now).toISOString(), range);
    assert.equal(Date.parse(prior), now - RANGE_MS[range]);
    assert.equal(nextWindowEnd(prior, range, now), null);
    const twice = previousWindowEnd(prior, range);
    assert.equal(nextWindowEnd(twice, range, now), prior);
  }
});

test('applied events are clustered without discarding individual entries', () => {
  const start = '2026-10-10T00:00:00.000Z';
  const end = '2026-10-17T00:00:00.000Z';
  const events = Array.from({length: 500}, (_, event_id) => ({
    event_id,
    ts: new Date(Date.parse(start) + event_id * 180_000).toISOString(),
    direction: event_id % 2 ? 'up' : 'down',
  }));
  const bins = clusterEvents(events, start, end);
  assert.ok(bins.length > 1 && bins.length <= 26);
  assert.equal(bins.reduce((sum, bin) => sum + bin.events.length, 0), events.length);
  assert.equal(bins.at(-1).latest.event_id, 499);
  assert.ok(bins.every(bin => bin.percentage >= 3 && bin.percentage <= 97));
});

test('bad timestamps and out-of-range events cannot render timeline markers', () => {
  assert.deepEqual(clusterEvents([], 'bad', '2026-10-17T00:00:00Z'), []);
  const result = clusterEvents([
    {event_id: 1, ts: 'bad'},
    {event_id: 2, ts: '2026-10-18T00:00:00Z'},
    {event_id: 3, ts: '2026-10-16T11:00:00Z'},
  ], '2026-10-16T00:00:00Z', '2026-10-17T00:00:00Z');
  assert.deepEqual(result.flatMap(bin => bin.events.map(ev => ev.event_id)), [3]);
});
