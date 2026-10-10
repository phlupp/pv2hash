"""Local MinerPool -> runtime -> DataLogger SQLite persistence tests."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from pv2hash.datalogger import DataLogger
from pv2hash.factory import build_miners
from pv2hash.miners.simulator import SimulatorMiner


def snapshot(ts: datetime, miner_pools, miner_id="miner-uuid-1"):
    return {
        "timestamp": ts,
        "instance": {"id": "dev-node-uuid"},
        "miners": [{
            "id": miner_id,
            "key": "sim1",
            "name": "Simulated Miner",
            "driver": "simulator",
            "runtime_state": "running",
            "pools": miner_pools,
        }],
    }


def pool(slot, *, active=False, username=None):
    return {
        "slot": slot,
        "host": ["primary.invalid", "backup.invalid"][slot],
        "port": 23334,
        "username": username or f"bc1qtestaddr.worker-{slot}",
        "is_active": active,
    }


class PoolDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "history.sqlite"
        self.logger = DataLogger(
            config_provider=lambda: {},
            snapshot_provider=lambda: {},
            db_path=self.db_path,
        )
        self.cfg = {"retention_days": 7}
        self.start = datetime.now(UTC).replace(microsecond=0)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, offset_seconds, pools):
        self.logger._write_snapshot(
            snapshot(self.start + timedelta(seconds=offset_seconds), pools),
            self.cfg,
        )

    def read(self, sql):
        with sqlite3.connect(self.db_path) as db:
            return db.execute(sql).fetchall()

    def test_migration_creates_current_and_history_tables(self):
        self.logger._ensure_schema()
        tables = {r[0] for r in self.read(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertIn("history_samples", tables)
        self.assertIn("miner_pools", tables)
        self.assertIn("miner_pool_events", tables)
        self.assertEqual(
            self.read("SELECT value FROM schema_meta "
                      "WHERE key='datalogger_schema_version'")[0][0],
            "5",
        )

    def test_first_observation_and_no_repeat_events(self):
        pools = [pool(0, active=True), pool(1)]
        self.write(0, pools)
        self.write(10, pools)
        self.assertEqual(self.read("SELECT count(*) FROM miner_pools")[0][0], 2)
        self.assertEqual(self.read("SELECT count(*) FROM miner_pool_events")[0][0], 2)
        self.assertEqual(
            [r[0] for r in self.read(
                "SELECT event_type FROM miner_pool_events ORDER BY id"
            )], ["added", "added"]
        )
        self.assertEqual(
            [r[0] for r in self.read(
                "SELECT is_active FROM miner_pools ORDER BY pool_slot"
            )], [1, 0]
        )

    def test_failover_only_changes_active_flag(self):
        self.write(0, [pool(0, active=True), pool(1)])
        self.write(10, [pool(0), pool(1, active=True)])
        self.assertEqual(
            self.read("SELECT pool_slot,is_active FROM miner_pools ORDER BY pool_slot"),
            [(0, 0), (1, 1)],
        )
        self.assertEqual(
            self.read("SELECT event_type, pool_slot FROM miner_pool_events ORDER BY id"),
            [("added", 0), ("added", 1), ("changed", 0), ("changed", 1)],
        )

    def test_unavailable_does_not_clear_but_confirmed_empty_does(self):
        self.write(0, [pool(0, active=True)])
        self.write(10, None)
        self.assertEqual(self.read("SELECT count(*) FROM miner_pools")[0][0], 1)
        self.write(20, [])
        self.assertEqual(self.read("SELECT count(*) FROM miner_pools")[0][0], 0)
        self.assertEqual(
            self.read("SELECT event_type FROM miner_pool_events ORDER BY id"),
            [("added",), ("removed",)],
        )

    def test_username_change_is_history_event(self):
        self.write(0, [pool(0, active=True)])
        self.write(10, [pool(0, active=True, username="bc1qchanged.myworker")])
        self.assertEqual(
            self.read("SELECT username FROM miner_pools"), [("bc1qchanged.myworker",)]
        )
        self.assertEqual(
            self.read("SELECT event_type FROM miner_pool_events ORDER BY id"),
            [("added",), ("changed",)],
        )

    def test_invalid_duplicate_slots_rollback_atomically(self):
        self.write(0, [pool(0, active=True)])
        with self.assertRaises(ValueError):
            self.write(10, [pool(0), pool(0)])
        self.assertEqual(self.read("SELECT count(*) FROM miner_pools")[0][0], 1)
        self.assertEqual(self.read("SELECT count(*) FROM history_samples")[0][0], 1)


class SimulatorPoolTest(unittest.TestCase):
    def test_simulator_exposes_both_slots_and_switches_without_network(self):
        sim = SimulatorMiner(
            miner_id="sim1", name="Dev Sim", host="simulator.invalid",
            pool_primary_host="pool-primary.invalid",
            pool_primary_port=23334,
            pool_primary_username="bc1qexample.main",
            pool_backup_host="pool-backup.invalid",
            pool_backup_port=3333,
            pool_backup_username="bc1qexample.failover",
        )
        first = asyncio.run(sim.get_status())
        self.assertEqual([p.slot for p in first.pools], [0, 1])
        self.assertEqual([p.is_active for p in first.pools], [True, False])
        self.assertEqual([p.username for p in first.pools],
                         ["bc1qexample.main", "bc1qexample.failover"])
        sim.set_active_pool_slot(1)
        self.assertEqual([p.is_active for p in asyncio.run(sim.get_status()).pools],
                         [False, True])
        with self.assertRaises(ValueError):
            sim.set_active_pool_slot(99)

    def test_factory_uses_configured_pool_slots(self):
        adapters = build_miners({"miners": [{
            "id": "sim1", "name": "Test", "driver": "simulator",
            "host": "dev.invalid", "settings": {
                "pool_primary_host": "main.invalid",
                "pool_primary_port": 3333,
                "pool_primary_username": "account.worker",
                "pool_backup_host": "backup.invalid",
                "pool_backup_port": 4444,
                "pool_backup_username": "account.backup",
                "active_pool_slot": 1,
            },
        }]})
        self.assertEqual(len(adapters), 1)
        status = asyncio.run(adapters[0].get_status())
        self.assertEqual([(p.host, p.port, p.is_active) for p in status.pools],
                         [("main.invalid", 3333, False),
                          ("backup.invalid", 4444, True)])


if __name__ == "__main__":
    unittest.main()
