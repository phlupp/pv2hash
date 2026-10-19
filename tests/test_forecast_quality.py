"""Append-only forecast revisions and settled, observed-quality evaluation."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pv2hash.forecast import utc_iso
from pv2hash.forecast.store import ForecastStore, TOTAL
from pv2hash.forecast.quality import evaluate_recent, quality_report
from pv2hash.forecast.planner_inputs import inspect_inputs, DEFAULT_BATTERY_TARGET


def when(t: str) -> datetime:
    return datetime.fromisoformat(t.replace('Z','+00:00'))


class RevisionArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=ForecastStore(Path(self.tmp.name)/'forecast.sqlite')
        self.history=Path(self.tmp.name)/'history.sqlite'

    def _write_actual(self, samples):
        with sqlite3.connect(self.history) as con:
            con.execute('CREATE TABLE history_samples(ts TEXT PRIMARY KEY, pv_power_w REAL, pv_quality TEXT)')
            con.executemany('INSERT INTO history_samples VALUES (?,?,?)',samples)

    def test_archive_preserves_every_prior_issued_prediction_with_atomic_projection(self):
        target='2025-11-05T14:00:00Z'
        before='2025-11-05T13:30:00Z'
        for issued, power in [('2025-11-05T12:00:00Z',1000),('2025-11-05T13:00:00Z',1400),
                              ('2025-11-05T13:45:00Z',1600)]:
            self.store.save_future(now=when(issued),provider='open_meteo',timezone='Europe/Berlin',
                resolution_minutes=15,
                surfaces={'wr1/south':{target:power},'wr2/west':{target:500}})
        with sqlite3.connect(self.store.path) as con:
            revisions=con.execute('''SELECT v.issued_at,p.power_w FROM forecast_revision_points p
                  JOIN forecast_revisions v ON v.revision_id=p.revision_id
                  WHERE p.valid_at=? AND p.device_id=? ORDER BY v.revision_id''',
                  (target,TOTAL)).fetchall()
            current=con.execute('SELECT power_w FROM forecast_points WHERE valid_at=? AND device_id=?',
                                (target,TOTAL)).fetchone()
            self.assertEqual(revisions,[('2025-11-05T12:00:00Z',1500),
                                        ('2025-11-05T13:00:00Z',1900),
                                        ('2025-11-05T13:45:00Z',2100)])
            self.assertEqual(current,(2100,))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM forecast_revisions').fetchone()[0],3)
        # Failed provider round must never insert a revision.
        with self.assertRaisesRegex(ValueError,'gemeinsamen'):
            self.store.save_future(now=when(before),provider='open_meteo',timezone='Europe/Berlin',
                                   surfaces={'wr1/south':{target:500},'wr2/east':{before:500}},
                                   resolution_minutes=15)
        with sqlite3.connect(self.store.path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM forecast_revisions').fetchone()[0],3)

    def test_quality_metrics_as_issued_for_each_horizon_and_idempotent(self):
        target='2025-11-05T14:00:00Z'
        samples=[('2025-11-05T11:00:00Z',1800),('2025-11-05T13:00:00Z',1600),
                 ('2025-11-05T13:30:00Z',1500),('2025-11-05T13:45:00Z',1300)]
        for issued, power in samples:
            self.store.save_future(now=when(issued),provider='open_meteo',timezone='Europe/Berlin',
                 resolution_minutes=15,surfaces={'wr1/south':{target:power}})
        self._write_actual([('2025-11-05T14:00:03+00:00',1000,'live')])
        done=when('2025-11-05T14:05:00Z')
        result=evaluate_recent(self.store,history_db=self.history,now=done,lookback_hours=5)
        self.assertEqual((result['evaluated'],result['candidate_samples']),(4,4))
        report=quality_report(self.store,days=1,now=done)
        self.assertEqual(report['revision_count'],4)
        self.assertEqual([row['samples'] for row in report['metrics']],[1]*4)
        self.assertEqual([row['mae_w'] for row in report['metrics']],[300,500,600,800])
        self.assertEqual(evaluate_recent(self.store,history_db=self.history,
                                         now=done,lookback_hours=5)['evaluated'],0)
        with sqlite3.connect(self.store.path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM forecast_quality_samples').fetchone()[0],4)
        # DataLogger may rotate; durable quality metrics remain readable.
        self.history.unlink()
        self.assertEqual(quality_report(self.store,days=1,now=done)['metrics'][0]['mae_w'],300)

    def test_stale_issued_predicition_never_masquerades_as_15m(self):
        t='2025-11-05T14:00:00Z'
        self.store.save_future(now=when('2025-11-05T13:00:00Z'),provider='x',timezone='UTC',
                               surfaces={'wr1/south':{t:1600}},resolution_minutes=15)
        self._write_actual([(t.replace('Z','+00:00'),1200,'live')])
        result=evaluate_recent(self.store,history_db=self.history,
                               now=when('2025-11-05T14:04:00Z'),lookback_hours=5)
        self.assertEqual(result['evaluated'],1)  # exactly 60m horizon
        self.assertEqual([m['samples'] for m in quality_report(self.store,days=1,
                         now=when('2025-11-05T14:04:00Z'))['metrics']],[0,0,1,0])

    def test_missing_or_degraded_measured_value_is_never_evaluated(self):
        t='2025-11-05T14:00:00Z'
        self.store.save_future(now=when('2025-11-05T13:45:00Z'),provider='x',timezone='UTC',
                               surfaces={'wr1/south':{t:1600}},resolution_minutes=15)
        # Although a LIVE sample exists nearby, closest sample is degraded.
        self._write_actual([('2025-11-05T14:00:00+00:00',500,'degraded'),
                            ('2025-11-05T14:00:10+00:00',1800,'live')])
        result=evaluate_recent(self.store,history_db=self.history,
                               now=when('2025-11-05T14:04:00Z'),lookback_hours=5)
        self.assertEqual(result['evaluated'],0)
        with sqlite3.connect(self.history) as con:
            con.execute("UPDATE history_samples SET pv_power_w=-2147483648,pv_quality='live' WHERE ts=?",
                        ('2025-11-05T14:00:00+00:00',))
        result=evaluate_recent(self.store,history_db=self.history,
                               now=when('2025-11-05T14:04:00Z'),lookback_hours=5)
        self.assertEqual(result['evaluated'],0)

    def test_quality_report_empty_never_claims_accuracy(self):
        report=quality_report(self.store,days=7)
        self.assertEqual(report['revision_count'],0)
        self.assertEqual([r['mae_w'] for r in report['metrics']],[None]*4)
        with self.assertRaises(ValueError):quality_report(self.store,days=91)
        self.assertEqual(evaluate_recent(self.store,history_db=self.history)['status'],'no_history')


class PlannerInputContractTests(unittest.TestCase):
    def _snapshot(self):
        return {'source':{'quality':'live','grid_power_w':-200},
                'pv':{'quality':'live','power_w':3500},
                'battery':{'enabled':True,'quality':'live','soc_pct':40,
                           'charge_power_w':1200,'discharge_power_w':0},
                'totals':{'miner_power_w':900},
                'miners':[{'key':'miner01','reachable':True,'monitor_enabled':True}]}

    def test_shadow_planner_is_not_enabled_even_if_current_meter_values_valid(self):
        result=inspect_inputs(self._snapshot(), {'battery_nominal_capacity_kwh':10})
        self.assertEqual(result['target_default'],{'soc_pct':100,'time_mode':'sunset','offset_minutes':-120})
        self.assertEqual(result['inputs']['household_load_now_w'],1200)
        self.assertEqual(result['inputs']['battery_nominal_capacity_kwh'],10)
        self.assertIsNone(result['inputs']['battery_usable_capacity_kwh'])
        self.assertFalse(result['planner_enabled'])
        self.assertFalse(result['ready_for_control'])
        self.assertIn('battery_max_charge_w',result['missing'])

    def test_degraded_pv_and_offline_miner_invalidate_houseload(self):
        snapshot=self._snapshot()
        snapshot['pv']['quality']='degraded'
        snapshot['miners'][0]['reachable']=False
        result=inspect_inputs(snapshot,{'battery_nominal_capacity_kwh':10})
        self.assertIsNone(result['inputs']['pv_power_w'])
        self.assertIsNone(result['inputs']['miner_power_w'])
        self.assertIsNone(result['inputs']['household_load_now_w'])
        self.assertEqual(result['unobserved_miners'],['miner01'])

    def test_missing_battery_capacity_is_explicit_not_fake(self):
        snapshot=self._snapshot()
        snapshot['battery']['quality']='stale'
        result=inspect_inputs(snapshot,{})
        self.assertIsNone(result['inputs']['battery_soc_pct'])
        self.assertIsNone(result['inputs']['battery_nominal_capacity_kwh'])
        self.assertIn('battery_soc_pct',result['missing'])


if __name__=='__main__':unittest.main()
