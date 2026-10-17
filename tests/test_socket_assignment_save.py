"""A normal Miner GUI save must never silently remove a socket assignment."""
from __future__ import annotations
import asyncio
from types import SimpleNamespace
from unittest import TestCase
from fastapi import FastAPI
from pv2hash import socket_miner_assignment


class MinerSocketSaveTest(TestCase):
    def make_app(self):
        socket = {"id": "s-demo", "name": "Test Tasmota", "driver": "tasmota_http", "control_enabled": False}
        miner = {"id": "m-demo", "name": "Testminer", "socket": {
            "enabled": True, "socket_id": "s-demo", "mode": "measure_only",
            "startup_delay_seconds": 60, "power_off_after_off_seconds": 3600,
        }}
        config = {"miners": [miner], "sockets": [socket]}
        saves = []
        reloads = []
        async def original(form):
            # The legacy config writer retains unrelated fields in updated config.
            config["miners"][0]["name"] = str(form.get("name", "Testminer"))
            saves.append("legacy")
            reloads.append("legacy")
            return {"status": "ok", "miner_id": "m-demo"}
        app = SimpleNamespace(
            app=FastAPI(), state=SimpleNamespace(config=config),
            _build_miners_view=lambda: [], _build_sockets_view=lambda: [],
            _build_runtime_snapshot_payload=lambda: {"sockets": [], "miners": []},
            _build_socket_snapshot_items=lambda: [],
            _update_miner_config_result=original,
            save_config=lambda _: saves.append("socket"),
            reload_runtime=lambda: reloads.append("socket"),
            logger=SimpleNamespace(info=lambda *args: None),
        )
        socket_miner_assignment.install(app)
        socket_miner_assignment.sync_socket_assignments(config)
        return app, config, saves, reloads

    def test_saving_miner_without_socket_fields_keeps_link(self):
        app, config, saves, reloads = self.make_app()
        result = asyncio.run(app._update_miner_config_result({"miner_id": "m-demo", "name": "Renamed"}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(config["miners"][0]["socket"]["socket_id"], "s-demo")
        self.assertEqual(config["sockets"][0]["assignment"]["target_id"], "m-demo")
        self.assertEqual(saves, ["legacy"])
        self.assertEqual(reloads, ["legacy"])

    def test_explicitly_clearing_socket_only_when_sent(self):
        app, config, saves, reloads = self.make_app()
        result = asyncio.run(app._update_miner_config_result({"miner_id": "m-demo", "socket.socket_id": ""}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(config["miners"][0]["socket"]["socket_id"], "")
        self.assertFalse(config["miners"][0]["socket"]["enabled"])
        self.assertNotIn("assignment", config["sockets"][0])
        self.assertEqual(saves, ["legacy", "socket"])
        self.assertEqual(reloads, ["legacy", "socket"])

    def test_invalid_socket_ignored_when_not_submitted_but_rejected_when_submitted(self):
        app, config, saves, _ = self.make_app()
        old = config["miners"][0]["socket"].copy()
        bad = asyncio.run(app._update_miner_config_result({"miner_id": "m-demo", "socket.socket_id": "missing"}))
        self.assertEqual(bad["status"], "error")
        self.assertEqual(config["miners"][0]["socket"], old)
        self.assertEqual(saves, [])
