"""Dashboard reports the adapter's actual driver name, not its editable label."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from pv2hash import app as app_module


class PvDriverLabelTest(TestCase):
    def test_live_pv_driver_label_is_exposed(self):
        fake = SimpleNamespace(
            power_w=284.0, quality="live", updated_at=None,
            driver_label="Modbus TCP PV-Erzeugung",
        )
        with (
            patch.object(app_module.services, "pv_source", fake),
            patch.object(app_module.services, "get_source_debug_info", return_value={}),
            patch.object(app_module, "_get_host_status", return_value={}),
            patch.object(app_module, "_build_controller_status", return_value={}),
            patch.object(app_module, "_build_dashboard_miner_rows", return_value=[]),
        ):
            pv = app_module._build_dashboard_live_payload()["cards"]["pv"]

        self.assertTrue(pv["enabled"])
        self.assertEqual(pv["power_w"], 284.0)
        self.assertEqual(pv["driver_label"], "Modbus TCP PV-Erzeugung")
        self.assertEqual(pv["quality"], "live")

    def test_missing_pv_driver_has_no_fake_driver_label(self):
        with (
            patch.object(app_module.services, "pv_source", None),
            patch.object(app_module.services, "get_source_debug_info", return_value={}),
            patch.object(app_module, "_get_host_status", return_value={}),
            patch.object(app_module, "_build_controller_status", return_value={}),
            patch.object(app_module, "_build_dashboard_miner_rows", return_value=[]),
        ):
            pv = app_module._build_dashboard_live_payload()["cards"]["pv"]

        self.assertFalse(pv["enabled"])
        self.assertIsNone(pv["driver_label"])
