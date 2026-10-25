"""Regression: both Hashpis must get identical, as-issued lead-time coverage."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pv2hash.forecast.schedule import due_in_forecast_slot, effective_refresh_minutes
from pv2hash.forecast.quality import evaluate_recent, quality_report
from pv2hash.forecast.store import ForecastStore


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def due(time: str, last: str | None = None, interval: int = 15) -> bool:
    return due_in_forecast_slot(
        now=dt(time), last_success=last, refresh_minutes=interval
    )


class FixedUtcScheduleTests(unittest.TestCase):
    def test_every_quarter_hour_issues_five_minutes_before_cutoff(self):
        for minute in (10, 25, 40, 55):
            with self.subTest(minute=minute):
                self.assertTrue(due(f"2026-10-24T12:{minute:02d}:00Z"))
                self.assertTrue(due(f"2026-10-24T12:{minute:02d}:59Z"))
                self.assertTrue(due(f"2026-10-24T12:{minute+3:02d}:59Z"))
                self.assertFalse(due(f"2026-10-24T12:{minute+4:02d}:00Z"))
        self.assertFalse(due("2026-10-24T12:06:00Z"))
        self.assertFalse(due("2026-10-24T12:24:59Z"))

    def test_restart_manual_refresh_and_retry(self):
        self.assertTrue(due("2026-10-24T12:10:15Z", "2026-10-24T11:55:31Z"))
        # Persisted success means no duplicates, including after restart.
        self.assertFalse(due("2026-10-24T12:11:50Z", "2026-10-24T12:10:20Z"))
        # Manual refresh in the same slot counts as the successful fetch.
        self.assertFalse(due("2026-10-24T12:12:00Z", "2026-10-24T12:10:00Z"))
        # Two slots may be less than 15*60 seconds apart owing to poll jitter.
        self.assertTrue(due("2026-10-24T12:25:00Z", "2026-10-24T12:10:55Z"))
        # An unsuccessful API call does not advance persisted last_success.
        self.assertTrue(due("2026-10-24T12:11:55Z", "2026-10-24T11:55:31Z"))
        # Late startup must wait for the next slot, never fake issuance time.
        self.assertFalse(due("2026-10-24T12:22:00Z", None))
        self.assertTrue(due("2026-10-24T12:25:15Z", None))

    def test_30_minute_cadence_stays_aligned_but_cannot_fill_all_quarters(self):
        self.assertEqual(effective_refresh_minutes(30), 30)
        for minute in (10, 40):
            self.assertTrue(due(f"2026-10-24T12:{minute:02d}:00Z", interval=30))
        for minute in (25, 55):
            self.assertFalse(due(f"2026-10-24T12:{minute:02d}:00Z", interval=30))
        self.assertTrue(due("2026-10-24T12:10:00Z", interval=60))
        self.assertFalse(due("2026-10-24T12:40:00Z", interval=60))

    def test_nonquarter_settings_do_not_increase_request_frequency(self):
        self.assertEqual(effective_refresh_minutes(17), 30)
        self.assertEqual(effective_refresh_minutes(31), 45)
        self.assertEqual(effective_refresh_minutes(360), 360)
        for invalid in (14, 361, 20.3, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                effective_refresh_minutes(invalid)

    def test_utc_schedule_survives_berlin_dst_fall_back(self):
        berlin = ZoneInfo("Europe/Berlin")
        # Repeated local 02:10: both UTC instants get a distinct, valid slot.
        before = dt("2026-10-25T00:10:00Z")
        after = dt("2026-10-25T01:10:00Z")
        self.assertEqual(before.astimezone(berlin).hour, 2)
        self.assertEqual(after.astimezone(berlin).hour, 2)
        self.assertTrue(due_in_forecast_slot(
            now=before.astimezone(berlin), last_success=None, refresh_minutes=15))
        self.assertTrue(due_in_forecast_slot(
            now=after.astimezone(berlin), last_success="2026-10-25T00:55:25Z",
            refresh_minutes=15))
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            due_in_forecast_slot(now=datetime(2026, 10, 25, 2, 10),
                                 last_success=None, refresh_minutes=15)


class RealForecastQualityPairingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ForecastStore(Path(self.tmp.name) / "forecast.sqlite")
        self.history = Path(self.tmp.name) / "history.sqlite"

    def test_standard_issue_slots_yield_all_15_30_60_180_comparisons(self):
        target = "2026-10-24T12:45:00Z"
        times = ["2026-10-24T09:40:00Z", "2026-10-24T11:40:00Z",
                 "2026-10-24T12:10:00Z", "2026-10-24T12:25:00Z"]
        for issued in times:
            self.assertTrue(due(issued))
            self.store.save_future(
                now=dt(issued), provider="open_meteo", timezone="Europe/Berlin",
                resolution_minutes=15, surfaces={"site/south": {target: 2000}})
        with sqlite3.connect(self.history) as con:
            con.execute("CREATE TABLE history_samples("
                        "ts TEXT PRIMARY KEY, pv_power_w REAL, pv_quality TEXT)")
            con.execute("INSERT INTO history_samples VALUES(?,?,?)",
                        ("2026-10-24T12:45:00+00:00", 2200, "live"))
        result = evaluate_recent(
            self.store, history_db=self.history,
            now=dt("2026-10-24T12:47:00Z"), lookback_hours=5)
        self.assertEqual(result["evaluated"], 4)
        report = quality_report(
            self.store, days=1, now=dt("2026-10-24T12:47:00Z"))
        self.assertEqual([m["samples"] for m in report["metrics"]], [1, 1, 1, 1])
        with sqlite3.connect(self.store.path) as con:
            pairs = con.execute(
                "SELECT horizon_minutes, issued_at FROM forecast_quality_samples"
                " ORDER BY horizon_minutes").fetchall()
        self.assertEqual(pairs, [(15, times[3]), (30, times[2]),
                                 (60, times[1]), (180, times[0])])


if __name__ == "__main__":
    unittest.main()
