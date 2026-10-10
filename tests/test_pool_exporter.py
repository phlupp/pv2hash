"""Pool inventory and change-event exports from real local SQLite rows.

No external InfluxDB is needed for these regression tests.
"""
from __future__ import annotations

import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pv2hash.datalogger import DataLogger
from pv2hash.exporters.source import ExportSource
from pv2hash.exporters.manager import ExportManager
from pv2hash.exporters.influxdb2 import InfluxDB2Destination, pool_active_state


def pool(slot, username, active, host=None):
    return {
        "slot": slot, "host": host or f"pool{slot}.invalid", "port": 23334,
        "username": username, "is_active": active,
    }


class PoolExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        base = Path(self.tmp.name)
        self.data_path = base / "history.sqlite"
        self.state_path = base / "exporters.sqlite"
        self.start = datetime.now(UTC).replace(microsecond=0)
        self.logger = DataLogger(
            config_provider=lambda: {}, snapshot_provider=lambda: {},
            db_path=self.data_path,
        )
        self.source = ExportSource(self.data_path, lambda: {})
        self.config = {
            "id": "test-exporter",
            "type": "influxdb2",
            "enabled": True,
            "settings": {
                "url": "http://localhost:8086", "org": "test",
                "bucket": "pv2hash-dev", "token": "testtoken",
            },
            "instance_id": "node-test",
            "instance_name": "Test",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, seconds, pools):
        self.logger._write_snapshot({
            "timestamp": self.start + timedelta(seconds=seconds),
            "instance": {"id": "node-test"},
            "miners": [{
                "id": "physical-miner-uuid", "key": "configured-miner-id",
                "name": "Miner A", "driver": "axeos",
                "runtime_state": "running", "pools": pools,
            }],
        }, {"retention_days": 7})

    def capture(self, stream, items):
        with patch('pv2hash.exporters.influxdb2.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value.status_code = 204
            InfluxDB2Destination(self.config).send(stream, items)
            return client.return_value.__enter__.return_value.post.call_args.kwargs['content'].decode()

    def test_tribool_string_conversion(self):
        self.assertEqual(pool_active_state(None), "unknown")
        self.assertEqual(pool_active_state(0), "inactive")
        self.assertEqual(pool_active_state(1), "active")
        with self.assertRaises(ValueError):
            pool_active_state("inferred")

    def test_inventory_sends_full_username_as_field_not_tag(self):
        self.write(0, [
            pool(0, "bc1qFullAddress.Worker01", None, "stratum.phlupp.net"),
            pool(1, "bc1qFullAddress.Worker01", 0, "backup.invalid"),
        ])
        inventory = self.source.pool_state()
        self.assertEqual(inventory["kind"], "pool_state")
        self.assertEqual(inventory["count"], 2)
        data = self.capture("pool_state", inventory["items"])
        self.assertEqual(len(data.splitlines()), 2)
        self.assertIn('active_state="unknown"', data)
        self.assertIn('active_state="inactive"', data)
        self.assertIn('username="bc1qFullAddress.Worker01"', data)
        self.assertIn('host="stratum.phlupp.net"', data)
        self.assertIn("port=23334i", data)
        self.assertIn("pool_slot=0", data)
        for line in data.splitlines():
            tags = line.split(" ", 1)[0]
            self.assertNotIn("bc1qFullAddress", tags)
            self.assertNotIn("stratum.phlupp.net", tags)

    def test_event_cursor_and_failover_and_remove(self):
        self.write(0, [pool(0, "account.worker", 1), pool(1, "account.worker", 0)])
        self.write(10, [pool(0, "account.worker", 0), pool(1, "account.worker", 1)])
        self.write(20, [pool(0, "account.worker", None)])
        first = self.source.pool_events(after_id=0, limit=2)
        self.assertEqual(first["count"], 2)
        self.assertEqual(first["next_cursor"], 2)
        later = self.source.pool_events(after_id=first["next_cursor"])
        self.assertEqual(later["count"], 4)
        self.assertEqual(later["next_cursor"], 6)
        data = self.capture("pool_events", later["items"])
        self.assertEqual(len(data.splitlines()), 4)
        self.assertIn('event_type="removed"', data)
        self.assertIn('active_state="unknown"', data)
        self.assertIn('event_id=6i', data)
        self.assertEqual(self.source.pool_events(after_id=6)["items"], [])

    def test_manager_state_retries_and_no_duplicate_delivery(self):
        self.write(0, [pool(0, "account.worker", None)])
        cfg = {"exporters": [self.config]}
        manager = ExportManager(self.source, lambda: cfg,
                                state_path=self.state_path)
        deliveries = []
        class Recorder:
            def send(self, kind, items):
                deliveries.append((kind, list(items)))
        manager.register("influxdb2", lambda _: Recorder())
        manager.run_once()
        kinds = [k for k, _ in deliveries]
        self.assertEqual(set(kinds), {"samples", "pool_state", "pool_events"})
        self.assertEqual(manager._state("test-exporter", "pool_events")["cursor"], "1")
        self.assertIsNotNone(manager._state("test-exporter", "pool_state")["last_success_at"])
        deliveries.clear()
        manager.run_once()
        self.assertEqual(deliveries, [])

        # A new observation gets a fresh event ID; only the new event is sent.
        self.write(10, [pool(0, "account.worker", 1)])
        manager.run_once()
        self.assertEqual([kind for kind, _ in deliveries], ["samples", "pool_events"])
        self.assertEqual(manager._state("test-exporter", "pool_events")["cursor"], "2")

    def test_pool_event_failure_does_not_advance_cursor(self):
        self.write(0, [pool(0, "account.worker", 1)])
        cfg = {"exporters": [self.config]}
        manager = ExportManager(self.source, lambda: cfg, state_path=self.state_path)
        class FailsPoolEvents:
            def send(self, kind, items):
                if kind == "pool_events":
                    raise RuntimeError("temporary Influx problem")
        manager.register("influxdb2", lambda _: FailsPoolEvents())
        manager.run_once()
        state = manager._state("test-exporter", "pool_events")
        self.assertIsNone(state["cursor"])
        self.assertEqual(state["consecutive_failures"], 1)
        self.assertTrue(state["retry_after"])
        self.assertEqual(manager._state("test-exporter", "pool_state")["consecutive_failures"], 0)

    def test_empty_pool_inventory_is_checkpointed(self):
        self.logger._ensure_schema()
        cfg = {"exporters": [self.config]}
        manager = ExportManager(self.source, lambda: cfg, state_path=self.state_path)
        class Recorder:
            def send(self, kind, items):
                raise AssertionError("empty sources must not send")
        manager.register("influxdb2", lambda _: Recorder())
        manager.run_once()
        self.assertIsNotNone(manager._state("test-exporter", "pool_state")["last_success_at"])
        self.assertIsNone(manager._state("test-exporter", "pool_events")["cursor"])

    def test_inventory_rehydrates_even_if_old_events_pruned(self):
        self.write(0, [pool(0, "stored.worker", None)])
        with sqlite3.connect(self.data_path) as db:
            db.execute("DELETE FROM miner_pool_events")
        self.assertEqual(self.source.pool_events()["count"], 0)
        inventory = self.source.pool_state()
        self.assertEqual(inventory["count"], 1)
        self.assertIn('username="stored.worker"',
                      self.capture("pool_state", inventory["items"]))


if __name__ == "__main__":
    unittest.main()
