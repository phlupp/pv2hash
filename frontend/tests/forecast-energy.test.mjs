import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { energyValue, coverageText } from '../src/forecast/energy.ts';

test('daily energy is formatted as kWh, never as power, and missing is not 0', () => {
  assert.equal(energyValue(18.4312), '18,43 kWh');
  assert.equal(energyValue(0), '0,0 kWh');
  assert.equal(energyValue(null), '—');
});

test('day cards describe future, ongoing and incomplete coverage', () => {
  const noRead = {kwh:null,coverage_hours:0,day_hours:24,complete:false};
  const partial = {kwh:4.2,coverage_hours:7.2,day_hours:24,complete:false};
  const full = {kwh:15.8,coverage_hours:24,day_hours:24,complete:true};
  assert.match(coverageText(noRead,false,false,true), /Noch keine Erzeugung/);
  assert.match(coverageText(noRead,true,false,false), /Keine Prognose/);
  assert.match(coverageText(partial,true,true,false), /Teilprognose/);
  assert.match(coverageText(partial,false,true,false), /Bisher gemessen/);
  assert.match(coverageText(partial,false,false,false), /unvollständig/);
  assert.match(coverageText(full,true,false,false), /ganzen Tag/);
  assert.match(coverageText(full,false,false,false), /Tagesenergie/);
});
