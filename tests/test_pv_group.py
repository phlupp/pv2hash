"""PV device group: atomic completed-round output, timeout, migration and editor."""
from __future__ import annotations

import asyncio
import time
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch

from pv2hash.config.defaults import DEFAULT_CONFIG
from pv2hash.config.store import deep_merge, normalize_config
from pv2hash.factory import build_pv_source
from pv2hash.sources.pv_group import PvGenerationGroup


def two_device_config():
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["pv"] = {
        "name": "PV-Erzeugung", "type": "pv_group", "enabled": True,
        "poll_interval_ms": 1000,
        "devices": [
            {"id": "pv-a111111111", "name": "Dach", "enabled": True, "driver": "pv_modbus",
             "settings": {"host": "localhost", "request_timeout_seconds": 0.2,
                          "power": {"address": 100, "value_type": "uint16"}}},
            {"id": "pv-b222222222", "name": "Garage", "enabled": True, "driver": "pv_modbus",
             "settings": {"host": "localhost", "request_timeout_seconds": 0.2,
                          "power": {"address": 200, "value_type": "uint16"}}},
        ],
    }
    return normalize_config(cfg)


class GroupRoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_publication_and_degraded_timeout_and_late_thread(self):
        group = build_pv_source(two_device_config())
        assert group is not None
        dach = group.devices[0][1]
        garage = group.devices[1][1]
        with patch.object(dach, "_poll_sync", return_value=200), patch.object(garage, "_poll_sync", return_value=800):
            result = await group.read()
        self.assertEqual(result, 1000.0)
        before = group.snapshot
        self.assertEqual(before.number, 1)
        self.assertEqual(before.quality, "live")

        # Dach finishes immediately; Garage takes longer than its own timeout.
        def slow_read():
            time.sleep(0.36)
            return 9999
        with patch.object(dach, "_poll_sync", return_value=300), patch.object(garage, "_poll_sync", side_effect=slow_read):
            task = asyncio.create_task(group.read())
            await asyncio.sleep(0.05)
            self.assertIs(group.snapshot, before, "intermediate device results MUST remain invisible")
            self.assertEqual(group.power_w, 1000)
            self.assertEqual(group.quality, "live")
            await task
        after = group.snapshot
        self.assertEqual(after.number, 2)
        self.assertEqual(after.power_w, 300)
        self.assertEqual(after.quality, "degraded")
        self.assertEqual((after.live_count, after.enabled_count), (1, 2))
        self.assertEqual([x["quality"] for x in after.devices], ["live", "offline"])
        self.assertIn("Timeout", after.devices[1]["last_error"])
        self.assertEqual(group.power_w, 300)
        await asyncio.sleep(0.22)
        self.assertIs(group.snapshot, after, "a late thread MUST NOT publish obsolete data")

    async def test_sma_invalid_member_is_excluded_and_sum_is_marked_degraded(self):
        group = build_pv_source(two_device_config())
        valid,invalid = [adapter for _,adapter in group.devices]
        sentinel_error=ValueError('PV-Modbus: ungültiger SMA-NaN-Messwert in Register 30775')
        with patch.object(valid,"_poll_sync",return_value=620.0), patch.object(
            invalid,"_poll_sync",side_effect=sentinel_error
        ):
            self.assertEqual(await group.read(),620.0)
        snap=group.snapshot
        self.assertEqual(snap.quality,"degraded")
        self.assertEqual((snap.live_count,snap.enabled_count),(1,2))
        self.assertEqual([d["power_w"] for d in snap.devices],[620.0,None])
        self.assertIn("SMA-NaN",snap.devices[1]["last_error"])
        self.assertEqual(group.debug_info["pv_power_w"],620.0)

        # A valid zero from both inverters must restore full LIVE status.
        with patch.object(valid,"_poll_sync",return_value=0.0),patch.object(
            invalid,"_poll_sync",return_value=0.0
        ):
            self.assertEqual(await group.read(),0.0)
        self.assertEqual(group.quality,"live")
        self.assertEqual(group.snapshot.live_count,2)

        # When no source supplies a valid value, never fabricate 0 W.
        with patch.object(valid,"_poll_sync",side_effect=sentinel_error),patch.object(
            invalid,"_poll_sync",side_effect=sentinel_error
        ):
            self.assertIsNone(await group.read())
        self.assertEqual(group.quality,"offline")
        self.assertIsNone(group.power_w)

    async def test_sma_power_standby_stays_full_quality_with_large_inverter(self):
        from pv2hash.sources.pv_modbus import SMAStandbyPower
        group = build_pv_source(two_device_config())
        big,small = [adapter for _,adapter in group.devices]
        with patch.object(big, "_poll_sync", return_value=4300.0), patch.object(
            small, "_poll_sync", side_effect=SMAStandbyPower("SMA-Standby 30775")
        ):
            self.assertEqual(await group.read(), 4300.0)
        snap=group.snapshot
        self.assertEqual(snap.quality, "live")
        self.assertEqual((snap.live_count,snap.standby_count,snap.enabled_count),(1,1,2))
        self.assertEqual([x["quality"] for x in snap.devices],["live","standby"])
        self.assertEqual([x["power_w"] for x in snap.devices],[4300.0,0.0])
        self.assertIsNone(snap.devices[1]["last_error"])
        self.assertIn("Standby",snap.devices[1]["note"])

        with patch.object(big,"_poll_sync",side_effect=SMAStandbyPower("Standby")), patch.object(
            small,"_poll_sync",side_effect=SMAStandbyPower("Standby")
        ):
            self.assertEqual(await group.read(),0.0)
        self.assertEqual(group.quality,"live")
        self.assertEqual(group.snapshot.standby_count,2)

        with patch.object(big,"_poll_sync",return_value=4200.0),patch.object(
            small,"_poll_sync",side_effect=OSError("Connection refused")
        ):
            self.assertEqual(await group.read(),4200.0)
        self.assertEqual(group.quality,"degraded")
        self.assertEqual(group.snapshot.standby_count,0)
        self.assertEqual(group.snapshot.devices[1]["quality"],"offline")

    async def test_all_offline_is_none_not_zero(self):
        group = build_pv_source(two_device_config())
        with patch.object(group.devices[0][1], "_poll_sync", side_effect=OSError("no route")), patch.object(
            group.devices[1][1], "_poll_sync", side_effect=OSError("refused")
        ):
            self.assertIsNone(await group.read())
        self.assertEqual(group.quality, "offline")
        self.assertIsNone(group.power_w)
        self.assertEqual(group.snapshot.live_count, 0)

    async def test_zero_is_valid_and_disabled_member_excluded(self):
        cfg = two_device_config()
        cfg["pv"]["devices"][1]["enabled"] = False
        group = build_pv_source(cfg)
        self.assertEqual(len(group.devices), 1)
        with patch.object(group.devices[0][1], "_poll_sync", return_value=0):
            self.assertEqual(await group.read(), 0.0)
        self.assertEqual(group.quality, "live")
        self.assertEqual(group.snapshot.enabled_count, 1)
        self.assertEqual(group.snapshot.power_w, 0.0)

    async def test_close_during_read_drops_entire_pending_round(self):
        group = build_pv_source(two_device_config())
        old = group.snapshot
        def slow_read():
            time.sleep(0.12)
            return 500
        with patch.object(group.devices[0][1], "_poll_sync", side_effect=slow_read), patch.object(
            group.devices[1][1], "_poll_sync", side_effect=slow_read
        ):
            task = asyncio.create_task(group.read())
            await asyncio.sleep(0.02)
            group.close()  # runtime reload retired this adapter
            await task
        self.assertIs(group.snapshot, old)


class GroupConfigTests(unittest.TestCase):
    def test_runtime_source_status_endpoint_uses_completed_round(self):
        from pv2hash import app as app_module
        info = app_module._build_sources_live_payload()
        self.assertIn("pv", info)
        self.assertIn("quality", info["pv"])
        self.assertIn("gui_models", info)

    def test_single_095_config_migrates_idempotently_and_keeps_settings(self):
        legacy = {
            "pv": {
                "type": "pv_modbus", "enabled": True,
                "name": "Hausdach", "settings": {
                    "host": "192.168.10.88", "port": 1502, "unit_id": 12,
                    "power": {"address": 303, "value_type": "int32", "factor": 1000},
                },
            }
        }
        new = normalize_config(deep_merge(DEFAULT_CONFIG, legacy))
        self.assertEqual(new["pv"]["type"], "pv_modbus")  # rollback compatibility
        self.assertEqual(len(new["pv"]["devices"]), 1)
        item = new["pv"]["devices"][0]
        self.assertEqual(item["settings"]["host"], "192.168.10.88")
        self.assertEqual(item["settings"]["power"]["address"], 303)
        self.assertEqual(item["settings"]["power"]["factor"], 1000)
        self.assertEqual(item["settings"]["unit_id"], 12)
        self.assertEqual(normalize_config(new), new)
        self.assertEqual(build_pv_source(new).snapshot.enabled_count, 1)

    def test_gui_device_fields_unique_and_form_can_add_remove_without_saving(self):
        from pv2hash.app import _apply_source_config_form, services
        from pv2hash.sources.pv_group import pv_device_configs
        config = two_device_config()
        models = services.get_source_gui_models(config=config)
        ids = [item["id"] for item in models]
        self.assertIn("pv", ids)
        self.assertIn("pv-device:pv-a111111111", ids)
        self.assertIn("pv-device:pv-b222222222", ids)
        from pv2hash.sources.base import EnergySource
        prefixes = [
            field["name"] for model in models if model["id"].startswith("pv-device:")
            for field in model["config_fields"] if field.get("name")
        ]
        self.assertIn("pvdev_pv-a111111111_name", prefixes)
        self.assertIn("pvdev_pv-b222222222_name", prefixes)

        form = {
            "pv_device_ids": "pv-a111111111,pv-b222222222,pv-c333333333",
            "pv_enabled": "on", "pv_poll_interval_ms": "5000",
        }
        for index, name in enumerate(("A", "B", "C")):
            id_ = ("pv-a111111111", "pv-b222222222", "pv-c333333333")[index]
            prefix = f"pvdev_{id_}_"
            form.update({
                prefix+"name": name, prefix+"driver": "pv_modbus",
                prefix+"host": "localhost", prefix+"port": "502",
                prefix+"unit_id": "1",
                prefix+"poll_interval_ms": "5000",
                prefix+"request_timeout_seconds": "1",
                prefix+"power_address": str(100+index),
                prefix+"power_register_type": "holding",
                prefix+"power_value_type": "uint16",
                prefix+"power_endian": "big_endian",
                prefix+"power_factor": "1",
            })
            if index < 2:
                form[prefix+"enabled"] = "on"
        changed = _apply_source_config_form(deepcopy(config), form)
        self.assertEqual(len(pv_device_configs(changed["pv"])), 3)
        self.assertFalse(changed["pv"]["devices"][2]["enabled"])
        self.assertEqual(changed["pv"]["devices"][1]["settings"]["power"]["address"], 101)
        build_pv_source(changed)
        form["pv_device_ids"] = "pv-a111111111,pv-c333333333"
        removed = _apply_source_config_form(deepcopy(changed), form)
        self.assertEqual([d["id"] for d in removed["pv"]["devices"]],
                         ["pv-a111111111", "pv-c333333333"])


if __name__ == "__main__":
    unittest.main()
