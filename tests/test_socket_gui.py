from __future__ import annotations

import unittest

from pv2hash.socket_gui import driver_catalog, socket_gui_item


class SocketGuiContractTest(unittest.TestCase):
    def test_password_never_leaves_gui_payload(self):
        marker = "do-not-leak-supersecret-value"
        view = {
            "id": "s-test", "name": "Test", "driver": "tasmota_http",
            "host": "192.168.10.100", "priority": 3,
            "settings": {
                "password": marker, "username": "operator", "relay": 1,
                "port": 80, "some_private_field": marker,
            },
        }
        item = socket_gui_item(view)
        self.assertTrue(item["password_configured"])
        self.assertEqual(item["settings"]["username"], "operator")
        self.assertNotIn("password", item["settings"])
        self.assertNotIn("some_private_field", item["settings"])
        self.assertNotIn(marker, repr(item))

    def test_driver_fields_expose_both_configs_without_stored_secrets(self):
        catalog = driver_catalog()
        self.assertEqual({item["id"] for item in catalog}, {"simulator", "tasmota_http"})
        tasmota = next(item for item in catalog if item["id"] == "tasmota_http")
        self.assertEqual(
            {f["name"] for f in tasmota["fields"]},
            {"port", "relay", "timeout_s", "use_energy", "username", "password"},
        )
        self.assertEqual(next(f for f in tasmota["fields"] if f["name"] == "password")["default"], "")

    def test_assignment_metadata_is_limited(self):
        view = {
            "id": "s-test", "name": "Socket", "driver": "simulator",
            "settings": {"reachable": False, "on_power_w": 45},
            "assignment": {"role": "miner", "target_id": "m1", "private_note": "never-expose"},
        }
        item = socket_gui_item(view)
        self.assertEqual(item["settings"], {"reachable": False, "on_power_w": 45})
        self.assertEqual(item["assignment"], {"role": "miner", "target_id": "m1"})


if __name__ == "__main__":
    unittest.main()
