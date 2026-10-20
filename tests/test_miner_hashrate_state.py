"""Regression for cached device hashrate after mining is paused."""
import asyncio
import unittest
from unittest.mock import patch

from pv2hash.miners.braiins import BraiinsMiner
from pv2hash.exporters.influxdb2 import InfluxDB2Destination
from pv2hash.miners.telemetry import effective_hashrate_ghs


def miner():
    return BraiinsMiner(miner_id="test", name="Test Antminer", host="miner.invalid")


def bundle(status, hash_values=None):
    return {
        "reachable": True,
        "details": {"status": "MINER_STATUS_NORMAL"},
        "status_first": {"state": status},
        "stats": {"miner_stats": {
            "real_hashrate": hash_values or {},
            "nominal_hashrate": {"gigahash_per_second": 30000},
        }},
        "tuner_state": {},
    }


class TelemetryTest(unittest.TestCase):
    def test_running(self):
        self.assertEqual(effective_hashrate_ghs(12500, "running"), 12500)
        self.assertEqual(effective_hashrate_ghs(0, "running"), 0.0)

    def test_paused_off_stopped(self):
        for state in ("paused", "stopped", "idle", "off", "socket_off"):
            self.assertEqual(effective_hashrate_ghs(12500, state), 0.0)

    def test_unknown_and_unreachable(self):
        for state in ("unknown", "starting", "rebooting", "fault"):
            self.assertIsNone(effective_hashrate_ghs(12500, state))
        self.assertIsNone(effective_hashrate_ghs(12500, "running", reachable=False))
        self.assertIsNone(effective_hashrate_ghs(None, "running"))
        self.assertIsNone(effective_hashrate_ghs(float("nan"), "running"))
        self.assertIsNone(effective_hashrate_ghs(-100, "running"))


class BraiinsTest(unittest.TestCase):
    def test_paused_and_stopped(self):
        for state in ("PAUSED", "STOPPED"):
            a = miner()
            a._apply_bundle(bundle("RUNNING", {
                "last_5s": {"gigahash_per_second": 12000}}))
            self.assertEqual(a.info.current_hashrate_ghs, 12000)
            a._apply_bundle(bundle(state, {
                "last_5s": {"gigahash_per_second": 12000},
                "since_restart": {"gigahash_per_second": 11500}}))
            self.assertEqual(a.info.current_hashrate_ghs, 0.0)
            self.assertEqual(a.info.power_w, 0.0)

    def test_no_nominal_historical_fallback(self):
        a = miner()
        a._apply_bundle(bundle("RUNNING", {
            "last_5m": {"gigahash_per_second": 14000},
            "since_restart": {"gigahash_per_second": 12000}}))
        self.assertEqual(a.info.runtime_state, "running")
        self.assertIsNone(a.info.current_hashrate_ghs)

    def test_5s_first_then_1m(self):
        a = miner()
        a._apply_bundle(bundle("RUNNING", {
            "last_5s": {"gigahash_per_second": 16000},
            "last_1m": {"gigahash_per_second": 11000}}))
        self.assertEqual(a.info.current_hashrate_ghs, 16000)
        a._apply_bundle(bundle("RUNNING", {
            "last_1m": {"gigahash_per_second": 10500}}))
        self.assertEqual(a.info.current_hashrate_ghs, 10500)

    def test_actions_and_off_profile(self):
        a = miner()
        a._apply_bundle(bundle("RUNNING", {
            "last_5s": {"gigahash_per_second": 12000}}))
        with patch.object(a, "_run_action_sync"):
            self.assertTrue(a.apply_action("pause_mining")["ok"])
        self.assertEqual(a.info.current_hashrate_ghs, 0.0)
        with patch.object(a, "_apply_profile_sync"):
            asyncio.run(a.set_profile("off"))
        self.assertEqual(a.info.current_hashrate_ghs, 0.0)
        with patch.object(a, "_run_action_sync"):
            self.assertTrue(a.apply_action("resume_mining")["ok"])
        self.assertIsNone(a.info.current_hashrate_ghs)

    def test_unreachable_unknown(self):
        a = miner()
        a._apply_bundle(bundle("PAUSED", {}))
        a._mark_unreachable("lost connection")
        self.assertIsNone(a.info.current_hashrate_ghs)


class ExporterTest(unittest.TestCase):
    def test_paused_raw_sample_is_not_exported_as_active_hashrate(self):
        config = {
            "instance_id": "test", "instance_name": "test",
            "settings": {"url": "http://localhost:8086", "org": "test",
                         "bucket": "test", "token": "not-used"},
        }
        sample = {
            "ts": "2026-10-20T00:00:00Z", "instance_id": "test",
            "miners": [
                {"miner_id": "paused", "runtime_state": "paused",
                 "reachable": True, "hashrate_ghs": 12000},
                {"miner_id": "running", "runtime_state": "running",
                 "reachable": True, "hashrate_ghs": 16000},
            ],
        }
        with patch("pv2hash.exporters.influxdb2.httpx.Client") as http:
            http.return_value.__enter__.return_value.post.return_value.status_code = 204
            InfluxDB2Destination(config).send("samples", [sample])
            content = http.return_value.__enter__.return_value.post.call_args.kwargs["content"].decode()
        paused = next(row for row in content.splitlines() if "miner_id=paused" in row)
        running = next(row for row in content.splitlines() if "miner_id=running" in row)
        self.assertIn("hashrate_ghs=0.0", paused)
        self.assertIn("hashrate_ghs=16000.0", running)
        self.assertIn("running_hashrate_ghs=16000.0", content)


if __name__ == "__main__":
    unittest.main()
