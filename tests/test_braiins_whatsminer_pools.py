"""Read-only pool discovery for Braiins OS+ gRPC and WhatsMiner API3."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from pv2hash.miners.braiins import BraiinsMiner
from pv2hash.miners.whatsminer_api3 import WhatsminerApi3Miner
from pv2hash.datalogger import DataLogger
from pv2hash.miners.pool_identity import pool_endpoint, explicit_bool


def braiins():
    return BraiinsMiner(miner_id="bos1", name="Braiins Test", host="bos.invalid")


def whatsminer():
    return WhatsminerApi3Miner(miner_id="w1", name="WhatsMiner Test", host="whats.invalid")


class EndpointTest(TestCase):
    def test_stratum_uri_and_ipv6_userinfo_stripped(self):
        self.assertEqual(pool_endpoint("stratum+tcp://user:SECRET@[2001:db8::1]:3333"),
                         ("2001:db8::1", 3333))
        self.assertEqual(pool_endpoint("pool.invalid:4444"), ("pool.invalid", 4444))
        self.assertEqual(pool_endpoint("pool.invalid"), ("pool.invalid", None))
        self.assertIsNone(pool_endpoint("pool.invalid:99999"))
        self.assertIsNone(pool_endpoint(""))
        self.assertEqual([explicit_bool(x) for x in (True,False,1,0,"true","false","unknown",None)],
                         [True,False,True,False,True,False,None,None])


class BraiinsPoolTest(TestCase):
    def test_groups_flatten_and_preserve_usernames(self):
        payload = {"pool_groups": [
            {"name": "main", "pools": [
                {"uid": "a", "url": "stratum+tcp://main.invalid:3333",
                 "user": "bc1qaddr.main", "active": True, "enabled": True},
                {"uid": "b", "url": "backup.invalid:4444",
                 "user": "bc1qaddr.backup", "enabled": True},
            ]},
            {"name": "other", "pools": [
                {"url": "stratum+ssl://bob:PASS@other.invalid:5555",
                 "user": "bc1qaddr.other", "active": False, "password": "NEVER_EXPORTED"},
            ]},
        ]}
        result = braiins()._parse_pool_groups(payload)
        self.assertEqual([p.slot for p in result], [0,1,2])
        self.assertEqual([p.host for p in result],
                         ["main.invalid","backup.invalid","other.invalid"])
        self.assertEqual([p.port for p in result], [3333,4444,5555])
        self.assertEqual([p.username for p in result],
                         ["bc1qaddr.main","bc1qaddr.backup","bc1qaddr.other"])
        self.assertEqual([p.is_active for p in result], [True,False,False])
        self.assertNotIn("PASS", repr(result))
        self.assertNotIn("NEVER_EXPORTED", repr(result))

    def test_successful_empty_and_unavailable_not_the_same(self):
        a=braiins()
        self.assertEqual(a._parse_pool_groups({}), [])
        self.assertEqual(a._parse_pool_groups({"pool_groups":[]}), [])
        self.assertIsNone(a._parse_pool_groups(None))
        self.assertIsNone(a._parse_pool_groups({"pool_groups":None}))
        self.assertIsNone(a._parse_pool_groups({"pool_groups":[{"pools":[{"url":"main.invalid"}]}]}))

    def test_bundle_sets_pools_but_unreachable_resets_unknown(self):
        a=braiins()
        with patch.object(a,"_fetch_bundle_sync",return_value={
            "reachable":True, "pool_groups":{"pool_groups":[{
                "name":"main", "pools":[{"url":"main.invalid:3333","user":"acct.worker","active":True}]
            }]}}):
            info=asyncio.run(a.get_status())
        self.assertTrue(info.reachable)
        self.assertEqual(info.pools[0].username,"acct.worker")
        with patch.object(a,"_fetch_bundle_sync",side_effect=RuntimeError("offline")):
            info=asyncio.run(a.get_status())
        self.assertIsNone(info.pools)


class WhatsMinerPoolTest(TestCase):
    def test_official_api3_pools_shape(self):
        data={"code":0,"msg":{"pools":[
            {"id":1,"url":"stratum+tcp://primary.invalid:3333",
             "account":"bc1qabc.worker1","status":"alive","stratum-active":True},
            {"id":2,"url":"stratum+tcp://backup.invalid:4444",
             "account":"bc1qabc.worker2","status":"alive","stratum-active":False},
        ]}}
        p=whatsminer()._parse_pools(data)
        self.assertEqual([(x.slot,x.host,x.port,x.username,x.is_active) for x in p],
                         [(0,"primary.invalid",3333,"bc1qabc.worker1",True),
                          (1,"backup.invalid",4444,"bc1qabc.worker2",False)])
        data["msg"]["pools"][0].pop("stratum-active")
        self.assertIsNone(whatsminer()._parse_pools(data)[0].is_active)

    def test_failed_or_incomplete_readback_unknown(self):
        m=whatsminer()
        self.assertIsNone(m._parse_pools(None))
        self.assertIsNone(m._parse_pools({"code":-2,"msg":"not supported"}))
        self.assertIsNone(m._parse_pools({"code":0,"msg":{}}))
        self.assertEqual(m._parse_pools({"code":0,"msg":{"pools":[]}}), [])
        self.assertIsNone(m._parse_pools({"code":0,"msg":{"pools":[{"id":1,"url":"host.invalid:3333"}]}}))
        self.assertIsNone(m._parse_pools({"code":0,"msg":{"pools":[{"id":1,"url":"host.invalid:3333","account":"x"},
                                                                  {"id":1,"url":"host2.invalid:3333","account":"y"}]}}))

    def test_status_readback_only_nonfatal(self):
        m=whatsminer()
        device={"code":0,"msg":{"miner":{"working":"true"},"system":{}}}
        status={"code":0,"msg":{"summary":{"power-realtime":1200,"hash-realtime":40,"up-freq-finish":1}}}
        pools={"code":0,"msg":{"pools":[{"id":1,"url":"main.invalid:3333",
                                          "account":"btcaddress.worker","stratum-active":True,
                                          "passwd":"NEVER_SAVE"}]}}
        with patch.object(m,"_get_device_info",return_value=device), \
             patch.object(m,"_get_summary_status",return_value=status), \
             patch.object(m,"_get_fan_setting",return_value={}), \
             patch.object(m,"_get_pools_status",return_value=pools):
            info=asyncio.run(m.get_status())
        self.assertTrue(info.reachable)
        self.assertEqual(info.pools[0].username,"btcaddress.worker")
        self.assertNotIn("NEVER_SAVE",repr(info.pools))
        with patch.object(m,"_get_device_info",return_value=device), \
             patch.object(m,"_get_summary_status",return_value=status), \
             patch.object(m,"_get_fan_setting",return_value={}), \
             patch.object(m,"_get_pools_status",side_effect=OSError("optional timeout")):
            info=asyncio.run(m.get_status())
        self.assertTrue(info.reachable)
        self.assertIsNone(info.pools)

    def test_pools_persist_to_local_sqlite(self):
        source=whatsminer()._parse_pools({"code":0,"msg":{"pools":[
            {"id":1,"url":"main.invalid:3333","account":"mybtc.worker","stratum-active":True}
        ]}})
        with TemporaryDirectory() as temp:
            logger=DataLogger(config_provider=lambda:{},snapshot_provider=lambda:{},
                              db_path=Path(temp)/"dev.sqlite")
            stamp=datetime.now(UTC)
            values=[{"slot":p.slot,"host":p.host,"port":p.port,
                     "username":p.username,"is_active":p.is_active} for p in source]
            for i,pools in enumerate((values,None)):
                logger._write_snapshot({
                    "timestamp":stamp+timedelta(seconds=10*i),
                    "instance":{"id":"node1"},
                    "miners":[{"id":"wminer1","driver":"whatsminer_api3","pools":pools}]
                },{"retention_days":7})
            with sqlite3.connect(logger._db_path) as con:
                self.assertEqual(con.execute("SELECT count(*) FROM miner_pools").fetchone()[0],1)
                self.assertEqual(con.execute("SELECT count(*) FROM miner_pool_events").fetchone()[0],1)


if __name__=="__main__":
    import unittest
    unittest.main()
