"""axeOS Stratum pool discovery: legacy and multi-pool firmware schemas."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import sqlite3

from pv2hash.datalogger import DataLogger
from pv2hash.miners.axeos import AxeOsMiner


def miner():
    return AxeOsMiner(miner_id="axe-test", name="Axe Test", host="axe.invalid")


class AxeOsPoolParsingTest(TestCase):
    def test_legacy_one_pool_unknown_active(self):
        p = miner()._parse_pools({
            "stratumURL": "stratum+tcp://stratum.phlupp.net",
            "stratumPort": 23334, "stratumUser": "bc1qABC.worker01",
        })
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0].slot, p[0].host, p[0].port),
                         (0, "stratum.phlupp.net", 23334))
        self.assertEqual(p[0].username, "bc1qABC.worker01")
        self.assertIsNone(p[0].is_active)

    def test_legacy_fallback_active_and_primary_active(self):
        data = {
            "stratumURL": "primary.invalid", "stratumPort": 3333,
            "stratumUser": "bc1qmyaccount.primary",
            "fallbackStratumURL": "backup.invalid", "fallbackStratumPort": 4444,
            "fallbackStratumUser": "bc1qmyaccount.fallback",
            "isUsingFallbackStratum": 1,
        }
        p = miner()._parse_pools(data)
        self.assertEqual([(x.slot, x.is_active) for x in p], [(0, False), (1, True)])
        data["isUsingFallbackStratum"] = 0
        p = miner()._parse_pools(data)
        self.assertEqual([(x.slot, x.is_active) for x in p], [(0, True), (1, False)])
        data.pop("isUsingFallbackStratum")
        p = miner()._parse_pools(data)
        self.assertEqual([x.is_active for x in p], [None, None])

    def test_modern_array_with_selected_pool_indices(self):
        data = {
            "pools": [
                {"stratumURL": "pool0.invalid", "stratumPort": 3000, "stratumUser": "acct.w0"},
                {"stratumURL": "pool1.invalid", "stratumPort": 3001, "stratumUser": "acct.w1"},
                {"stratumURL": "stratum+tcp://pool2.invalid:3333", "stratumUser": "acct.w2",
                 "stratumPassword": "NEVER_STORED"},
            ],
            "primaryPoolIndex": 2,
            "secondaryPoolIndex": 1,
            "isUsingFallbackStratum": True,
        }
        p = miner()._parse_pools(data)
        self.assertEqual([x.slot for x in p], [0, 1, 2])
        self.assertEqual([x.is_active for x in p], [False, True, False])
        self.assertEqual(p[2].port, 3333)
        self.assertNotIn("NEVER_STORED", repr(p))
        data["isUsingFallbackStratum"] = False
        self.assertEqual([x.is_active for x in miner()._parse_pools(data)],
                         [False, False, True])

    def test_no_flag_does_not_assume_pool_connected(self):
        data = {"pools": [{"stratumURL": "main.invalid",
                           "stratumPort": 3333, "stratumUser": "address.worker"}],
                "useFallbackStratum": 0}  # preference is not connection state
        self.assertEqual([x.is_active for x in miner()._parse_pools(data)], [None])
        data["isUsingFallbackStratum"] = "unexpected"
        self.assertEqual([x.is_active for x in miner()._parse_pools(data)], [None])

    def test_empty_vs_unavailable(self):
        self.assertEqual(miner()._parse_pools({"pools": []}), [])
        self.assertIsNone(miner()._parse_pools({}))
        self.assertIsNone(miner()._parse_pools({"pools": None}))
        self.assertIsNone(miner()._parse_pools({"stratumURL": "main.invalid"}))
        self.assertIsNone(miner()._parse_pools({"pools": [{
            "stratumURL": "main.invalid", "stratumPort": 3333,
        }]}))
        self.assertIsNone(miner()._parse_pools({"pools": [{
            "stratumUser": "address.worker",
        }]}))

    def test_empty_slot_and_ipv6_normalization(self):
        data = {"pools": [
            {"stratumURL": "stratum+tcp://[2001:db8::123]:3333",
             "stratumUser": "acct.worker"},
            {"stratumURL": "", "stratumUser": "", "stratumPort": None},
            {"stratumURL": "stratum+ssl://alice:secret@backup.invalid:4444",
             "stratumUser": "acct.fallback"},
        ]}
        pools = miner()._parse_pools(data)
        self.assertEqual([(p.slot,p.host,p.port) for p in pools],
                         [(0,"2001:db8::123",3333),(2,"backup.invalid",4444)])
        self.assertNotIn("secret", repr(pools))

    def test_invalid_ports_and_malformed_pool_data_do_not_erase(self):
        self.assertIsNone(miner()._parse_pools({
            "stratumURL": "main.invalid", "stratumPort": 0, "stratumUser": "me"
        }))
        self.assertIsNone(miner()._parse_pools({"pools": ["not a dict"]}))

    def test_http_status_uses_api_readback_without_control_actions(self):
        a = miner()
        data = {
            "power": 12, "hashRate": 90,
            "stratumURL": "main.invalid", "stratumPort": 3333,
            "stratumUser": "acct.worker", "isUsingFallbackStratum": 0,
            "stratumPassword": "NEVER_STORED",
        }
        with patch.object(a, "_refresh_sync", return_value=data):
            info = asyncio.run(a.get_status())
        self.assertTrue(info.reachable)
        self.assertEqual(info.pools[0].username, "acct.worker")
        self.assertTrue(info.pools[0].is_active)
        self.assertNotIn("NEVER_STORED", repr(info.pools))

    def test_unreachable_reports_unknown_while_sqlite_preserves_last_known(self):
        a = miner()
        good = {"stratumURL": "main.invalid", "stratumPort": 3333,
                "stratumUser": "acct.worker", "isUsingFallbackStratum": False}
        with patch.object(a, "_refresh_sync", return_value=good):
            asyncio.run(a.get_status())
        self.assertEqual(len(a.info.pools), 1)
        with TemporaryDirectory() as temp:
            logger = DataLogger(config_provider=lambda: {}, snapshot_provider=lambda: {},
                                db_path=Path(temp) / "history.sqlite")
            def persist():
                pools = None if a.info.pools is None else [
                    dict(slot=p.slot,host=p.host,port=p.port,
                         username=p.username,is_active=p.is_active)
                    for p in a.info.pools]
                logger._write_snapshot({
                    "timestamp": datetime.now(UTC),
                    "instance": {"id": "node-1"},
                    "miners": [{"id": "miner-1", "name": "Axe",
                                "driver": "axeos", "pools": pools}],
                }, {"retention_days": 7})
            persist()
            with patch.object(a, "_refresh_sync", side_effect=RuntimeError("offline")):
                info = asyncio.run(a.get_status())
            self.assertFalse(info.reachable)
            self.assertIsNone(info.pools)
            persist()
            with sqlite3.connect(logger._db_path) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM miner_pools").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT count(*) FROM miner_pool_events").fetchone()[0], 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
