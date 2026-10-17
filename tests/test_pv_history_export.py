"""PV generation round-trip: live Modbus source -> runtime snapshot -> SQLite -> Influx.

The generation value is read-only and never introduced into controller snapshots.
A missing, offline or stale reading is NULL; 0 W is a valid LIVE sample.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from pv2hash import app as app_module
from pv2hash.datalogger import DataLogger
from pv2hash.exporters.influxdb2 import InfluxDB2Destination
from pv2hash.exporters.source import ExportSource
from pv2hash.sources.pv_group import PvRound


class PVHistoryTest(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "existing-history.sqlite"
        self.logger = DataLogger(
            db_path=self.db,
            config_provider=lambda: {"datalogger": {"retention_days": 7}},
            snapshot_provider=lambda: {},
        )
        self.cfg = {"retention_days": 7}

    def test_additive_sqlite_upgrade_and_live_zero(self):
        # Simulates a real 0.9.5 SQLite DB with no PV history columns.
        self.logger._ensure_schema()
        with sqlite3.connect(self.db) as con:
            con.execute("ALTER TABLE history_samples DROP COLUMN pv_power_w")
            con.execute("ALTER TABLE history_samples DROP COLUMN pv_quality")
            con.execute("UPDATE schema_meta SET value='5' WHERE key='datalogger_schema_version'")
            con.execute("INSERT INTO history_samples (ts, instance_id) VALUES ('2026-10-16T12:00:00+00:00', 'old')")
        self.logger._ensure_schema()
        with sqlite3.connect(self.db) as con:
            old = con.execute("SELECT pv_power_w,pv_quality FROM history_samples WHERE instance_id='old'").fetchone()
            self.assertEqual(old, (None, None))
            self.assertEqual(con.execute("SELECT value FROM schema_meta WHERE key='datalogger_schema_version'").fetchone()[0], "6")
        now = datetime.now(UTC).replace(microsecond=0)
        self.logger._write_snapshot({
            "timestamp": now,
            "instance": {"id": "node-1"},
            "source": {"grid_power_w": -200, "quality": "live"},
            "pv": {"power_w": 0, "quality": "live"},
        }, self.cfg)
        with sqlite3.connect(self.db) as con:
            power, quality = con.execute(
                "SELECT pv_power_w,pv_quality FROM history_samples WHERE instance_id='node-1'"
            ).fetchone()
        self.assertEqual((power, quality), (0.0, "live"))

    def test_offline_stale_disabled_are_null_not_fake_zero(self):
        now = datetime.now(UTC)
        for index, quality in enumerate(("offline", "stale", "disabled", "no_data")):
            self.logger._write_snapshot({
                "timestamp": now + timedelta(microseconds=index + 1),
                "instance": {"id": "node-2"}, "pv": {"power_w": 900.0, "quality": quality},
            }, self.cfg)
        items = ExportSource(self.db, lambda: {}).samples(limit=10)["items"]
        self.assertEqual(len(items), 4)
        self.assertTrue(all(item["pv_power_w"] is None for item in items))
        self.assertEqual([item["pv_quality"] for item in items],
                         ["offline", "stale", "disabled", "no_data"])
        self.assertTrue(all("pv_power_w" in item for item in items))

    def test_real_exporter_system_line_contains_measurement_quality_and_versions(self):
        now = datetime.now(UTC)
        self.logger._write_snapshot({
            "timestamp": now, "instance": {"id": "n1"},
            "source": {"grid_power_w": 25, "quality": "live"},
            "pv": {"power_w": 412.5, "quality": "live"},
        }, self.cfg)
        row = ExportSource(self.db, lambda: {}).samples()["items"][0]
        with patch("pv2hash.exporters.influxdb2.read_build_identity", return_value={
            "release_id": "r2026.10.19.1", "core_version": "0.9.5",
            "gui_version": "2.0.1",
        }), patch("pv2hash.exporters.influxdb2.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value.status_code = 204
            InfluxDB2Destination({
                "settings": {"url": "http://localhost:8086", "org": "org",
                             "bucket": "bucket", "token": "testing"}
            }).send("samples", [row])
            data = client.return_value.__enter__.return_value.post.call_args.kwargs["content"].decode()
        system = next(row for row in data.splitlines() if row.startswith("pv2hash_system,"))
        self.assertIn("pv_power_w=412.5", system)
        self.assertIn('pv_quality="live"', system)
        self.assertIn('pv2hash_version="r2026.10.19.1"', system)
        self.assertIn('pv2hash_core_version="0.9.5"', system)
        self.assertIn('pv2hash_gui_version="2.0.1"', system)
        self.assertNotIn("pv_quality=", system.split(" ", 1)[0])

    def test_degraded_partial_sum_persisted_with_quality(self):
        now = datetime.now(UTC)
        self.logger._write_snapshot({
            "timestamp": now,
            "instance": {"id": "n-degraded"},
            "pv": {"power_w": 230.0, "quality": "degraded"},
        }, self.cfg)
        rows = ExportSource(self.db, lambda: {}).samples()["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pv_power_w"], 230.0)
        self.assertEqual(rows[0]["pv_quality"], "degraded")
        with patch("pv2hash.exporters.influxdb2.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value.status_code = 204
            InfluxDB2Destination({
                "settings": {"url": "http://localhost:8086", "org": "org",
                             "bucket": "bucket", "token": "testing"}
            }).send("samples", rows)
            lines = client.return_value.__enter__.return_value.post.call_args.kwargs["content"].decode()
        system = next(line for line in lines.splitlines() if line.startswith("pv2hash_system,"))
        self.assertIn("pv_power_w=230.0", system)
        self.assertIn('pv_quality="degraded"', system)

    def test_runtime_snapshot_uses_only_published_round_quality(self):
        timestamp = datetime.now(UTC)
        fake = SimpleNamespace(
            snapshot=PvRound(power_w=0.0, quality="live",
                             updated_at=timestamp, live_count=1, enabled_count=1),
            poll_interval_ms=5000,
        )
        with patch.object(app_module.services, "pv_source", fake), patch.object(
            app_module.services, "get_pv_source_debug_info", return_value={"current_quality": "live"}
        ), patch.object(app_module, "_build_controller_status", return_value={}), patch.object(
            app_module, "_build_snapshot_host_info", return_value={}
        ), patch.object(app_module, "_build_socket_snapshot_items", return_value=[]):
            live = app_module._build_runtime_snapshot_payload()
            self.assertEqual(live["pv"]["power_w"], 0.0)
            self.assertEqual(live["pv"]["quality"], "live")
            fake.snapshot = PvRound(
                power_w=0.0, quality="live",
                updated_at=timestamp - timedelta(minutes=2),
                live_count=1, enabled_count=1,
            )
            # While the next round is still collecting, consumers MUST
            # retain the previous published value and quality together.
            waiting = app_module._build_runtime_snapshot_payload()
            self.assertEqual(waiting["pv"]["power_w"], 0.0)
            self.assertEqual(waiting["pv"]["quality"], "live")
            fake.snapshot = PvRound(
                power_w=None, quality="stale",
                updated_at=datetime.now(UTC), live_count=0, enabled_count=1,
            )
            stale = app_module._build_runtime_snapshot_payload()
            self.assertIsNone(stale["pv"]["power_w"])
            self.assertEqual(stale["pv"]["quality"], "stale")
            fake.snapshot = PvRound(
                power_w=None, quality="offline", updated_at=datetime.now(UTC),
                live_count=0, enabled_count=1,
            )
            offline = app_module._build_runtime_snapshot_payload()
            self.assertIsNone(offline["pv"]["power_w"])
            self.assertEqual(offline["pv"]["quality"], "offline")

    def test_downsample_pv_preserves_mean_and_quality(self):
        now = datetime.now(UTC).replace(microsecond=0)
        start = now - timedelta(minutes=2)
        rows = [
            {"ts": (start + timedelta(seconds=5*i)).isoformat(),
             "pv_power_w": float(i*10), "pv_quality": "live"}
            for i in range(4)
        ]
        down = self.logger._downsample_rows(rows, max_points=2, start=start, end=now)
        self.assertTrue(down)
        self.assertEqual(down[0]["pv_power_w"], 15.0)
        self.assertEqual(down[0]["pv_quality"], "live")
