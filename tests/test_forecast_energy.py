"""Energy in kWh uses real measured intervals, never interpolates data gaps."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pv2hash.forecast.energy import energy_summary
from pv2hash.forecast.service import actual_day_with_energy
from pv2hash.forecast.store import ForecastStore, TOTAL
from pv2hash.forecast import utc_iso


def ts(date: str) -> datetime:
    return datetime.fromisoformat(date.replace('Z', '+00:00'))


class EnergyMathTests(unittest.TestCase):
    def test_full_day_constant_forecast_and_daylight_savings(self):
        for start, end, hours in [
            ('2025-03-29T23:00:00Z', '2025-03-30T22:00:00Z', 23),
            ('2025-10-25T22:00:00Z', '2025-10-26T23:00:00Z', 25),
            ('2026-10-17T22:00:00Z', '2026-10-18T22:00:00Z', 24),
        ]:
            with self.subTest(hours=hours):
                first, last = ts(start), ts(end)
                points = [(first + timedelta(hours=i), 1000.0)
                          for i in range(hours+1)]
                result = energy_summary(points, start=first, end=last, max_gap_seconds=5400)
                self.assertEqual(result['kwh'], hours)
                self.assertEqual(result['coverage_hours'], hours)
                self.assertTrue(result['complete'])

    def test_missing_hour_cannot_be_filled_and_negative_noise_is_zero_energy(self):
        first = ts('2026-10-18T00:00:00Z')
        result = energy_summary([
            (first, 1000), (first + timedelta(hours=1), 1000),
            (first + timedelta(hours=3), 1000),
            (first + timedelta(hours=4), 1000),
        ], start=first, end=first+timedelta(hours=5), max_gap_seconds=5400)
        self.assertEqual(result['kwh'], 2)
        self.assertEqual(result['coverage_hours'], 2)
        self.assertFalse(result['complete'])
        zero = energy_summary([
            (first, -9), (first+timedelta(hours=1), -2),
        ], start=first, end=first+timedelta(hours=1), max_gap_seconds=5400)
        self.assertEqual(zero['kwh'], 0)
        self.assertTrue(zero['complete'])
        missing = energy_summary([(first, 1234)], start=first,
                                 end=first+timedelta(hours=1), max_gap_seconds=5400)
        self.assertIsNone(missing['kwh'])
        self.assertFalse(missing['complete'])

    def test_forecast_store_exposes_day_end_boundary_without_plotting_next_day(self):
        with TemporaryDirectory() as directory:
            store=ForecastStore(Path(directory)/'forecast.sqlite')
            store.ensure_schema()
            day_start = ts('2026-10-17T22:00:00Z')
            with store._connect() as con:
                con.execute("INSERT INTO forecast_meta VALUES('timezone','Europe/Berlin')")
                con.executemany(
                    'INSERT INTO forecast_points VALUES (?,?,?,?,?)',
                    [(utc_iso(day_start+timedelta(hours=i)),TOTAL,2000,
                      utc_iso(day_start-timedelta(hours=1)),'test')
                     for i in range(25)])
            day = store.day('2026-10-18')
            self.assertEqual(len(day['forecast']),24)
            self.assertEqual(day['energy']['forecast']['kwh'],48)
            self.assertTrue(day['energy']['forecast']['complete'])

    def test_actual_energy_only_live_consecutive_samples_no_sma_or_degraded_gaps(self):
        with TemporaryDirectory() as directory:
            db=Path(directory)/'history.sqlite'
            start=ts('2026-10-18T11:00:00Z')
            with sqlite3.connect(db) as con:
                con.execute('CREATE TABLE history_samples(ts TEXT, pv_power_w REAL, pv_quality TEXT)')
                rows=[
                    (0,1000,'live'), (10,1000,'live'), (20,1000,'live'),
                    (30,-2147483648,'live'), (40,1000,'live'),
                    (50,1000,'degraded'), (60,2000,'live'),
                    (70,2000,'live'), (140,2000,'live'), (150,2000,'live'),
                ]
                con.executemany('INSERT INTO history_samples VALUES (?,?,?)',
                    [(utc_iso(start+timedelta(seconds=i)),p,q) for i,p,q in rows])
            actual, energy = actual_day_with_energy(utc_iso(start),
                    utc_iso(start+timedelta(hours=1)), db)
            # 20s at 1 kW + 10s at 2 kW + 10s at 2 kW = 0.016667 kWh.
            self.assertAlmostEqual(energy['kwh'], 40/3600*1.5, places=6)
            self.assertAlmostEqual(energy['coverage_hours'],40/3600,places=5)
            self.assertFalse(energy['complete'])
            self.assertEqual(len(actual),1)
            # chart averages only valid live samples, not SMA NaN or degraded
            self.assertAlmostEqual(actual[0]['power_w'], 1500.0)
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute('SELECT count(*) FROM history_samples').fetchone()[0],len(rows))

    def test_forecast_day_api_includes_both_energy_totals(self):
        import asyncio
        import json
        from unittest.mock import patch
        from pv2hash import app as application
        with TemporaryDirectory() as directory:
            store=ForecastStore(Path(directory)/'forecast.sqlite')
            store.ensure_schema()
            day_start=ts('2026-10-17T22:00:00Z')
            with store._connect() as con:
                con.execute("INSERT INTO forecast_meta VALUES('timezone','Europe/Berlin')")
                con.executemany('INSERT INTO forecast_points VALUES (?,?,?,?,?)', [
                    (utc_iso(day_start+timedelta(hours=i)),TOTAL,1000,
                     utc_iso(day_start-timedelta(hours=1)),'test') for i in range(25)
                ])
            with patch.object(application,'forecast_store',store), patch.object(
                application, 'forecast_actual_day_with_energy',return_value=([],
                 {'kwh':None,'coverage_hours':0,'day_hours':24,'complete':False})):
                response=asyncio.run(application.api_forecast_day('2026-10-18'))
            payload=json.loads(response.body)
            self.assertEqual(payload['status'],'ok')
            self.assertEqual(payload['energy']['forecast']['kwh'],24)
            self.assertIsNone(payload['energy']['actual']['kwh'])
            self.assertEqual(len(payload['forecast']),24)

    def test_missing_history_is_missing_energy_not_zero(self):
        with TemporaryDirectory() as directory:
            a,e=actual_day_with_energy('2026-10-18T00:00:00Z','2026-10-19T00:00:00Z',
                                       Path(directory)/'absent.sqlite')
            self.assertEqual(a, [])
            self.assertIsNone(e['kwh'])
            self.assertFalse(e['complete'])


if __name__ == '__main__':
    unittest.main()
