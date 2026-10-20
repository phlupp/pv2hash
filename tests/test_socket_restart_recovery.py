"""Recover the socket/miner off state after a PV2Hash process restart."""
from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pv2hash import socket_miner_workflow as workflow
from pv2hash.models.miner import MinerInfo


class FakeSocket:
    def __init__(self, *, is_on=False, reachable=True):
        self.info = SimpleNamespace(id="s-test", reachable=reachable, is_on=is_on)
        self.read_count = 0
        self.switches = []

    def get_status(self):
        self.read_count += 1
        return self.info

    def switch_on(self):
        self.switches.append("on")
        self.info.is_on = True
        return {"ok": True}

    def switch_off(self):
        self.switches.append("off")
        self.info.is_on = False
        return {"ok": True}


class SocketRestartRecoveryTest(unittest.TestCase):
    def setUp(self):
        workflow._RUNTIME.clear()

    def tearDown(self):
        workflow._RUNTIME.clear()

    def make_env(self, *, is_on=False, reachable=True):
        socket = FakeSocket(is_on=is_on, reachable=reachable)
        config = {
            "miners": [{
                "id": "m-test", "name": "Antminer",
                "socket": {
                    "socket_id": "s-test", "mode": "switching",
                    "startup_delay_seconds": 60,
                    "power_off_after_off_seconds": 3600,
                },
            }],
            "sockets": [{
                "id": "s-test", "driver": "tasmota_http",
                "control_enabled": False,
                "assignment": {"role": "miner", "target_id": "m-test"},
            }],
        }
        app = SimpleNamespace(
            state=SimpleNamespace(config=config),
            services=SimpleNamespace(sockets=[socket]),
        )
        info = MinerInfo(id="m-test", name="Antminer", host="miner.invalid", driver="whatsminer_api3")
        miner = SimpleNamespace(info=info)
        calls = []

        async def miner_set_profile(profile):
            calls.append(profile)
            info.profile = profile
            return info

        async def apply(profile):
            return await workflow._handle_profile_with_socket(
                app_mod=app, miner=miner, original_set_profile=miner_set_profile,
                profile=profile,
            )
        return socket, miner, calls, apply

    def test_cold_restart_with_socket_already_off_skips_unpowered_miner(self):
        socket, miner, calls, apply = self.make_env(is_on=False)
        for _ in range(4):
            self.assertIsNone(asyncio.run(apply("off")))
        state = workflow._workflow_state("m-test")
        self.assertEqual(calls, [], "Must not send 'off' to a physically unplugged miner")
        self.assertEqual(socket.switches, [])
        self.assertEqual(state["state"], "socket_off")
        self.assertNotIn("power_off_due_monotonic", state)
        self.assertFalse(miner.info.reachable)
        self.assertEqual(miner.info.current_hashrate_ghs, 0.0)
        self.assertEqual(miner.info.runtime_state, "socket_off")

    def test_preexisting_off_timer_reconciles_external_poweroff(self):
        _, _, calls, apply = self.make_env(is_on=False)
        state = workflow._workflow_state("m-test")
        state["state"] = "off_timer"
        state["off_since_monotonic"] = time.monotonic() - 400
        state["power_off_due_monotonic"] = time.monotonic() + 3200
        asyncio.run(apply("off"))
        self.assertEqual(calls, [])
        self.assertEqual(state["state"], "socket_off")
        self.assertNotIn("power_off_due_monotonic", state)

    def test_socket_still_on_keeps_one_hour_stop_sequence(self):
        socket, _, calls, apply = self.make_env(is_on=True)
        asyncio.run(apply("off"))
        state = workflow._workflow_state("m-test")
        self.assertEqual(calls, ["off"])
        self.assertEqual(state["state"], "off_timer")
        self.assertEqual(socket.switches, [])
        self.assertGreater(state["power_off_due_monotonic"], time.monotonic() + 3500)
        # Simulate expiration without waiting one real hour.
        state["power_off_due_monotonic"] = time.monotonic() - 1
        asyncio.run(apply("off"))
        self.assertEqual(socket.switches, ["off"])
        self.assertEqual(state["state"], "socket_off")
        asyncio.run(apply("off"))
        self.assertEqual(calls, ["off", "off"], "No more miner calls once off")

    def test_restart_during_off_delay_cancels_timer_and_resets_next_delay(self):
        socket, miner, calls, apply = self.make_env(is_on=True)

        # Normal stop: miner is paused, but its socket is still powered.
        asyncio.run(apply("off"))
        state = workflow._workflow_state("m-test")
        self.assertEqual(state["state"], "off_timer")
        self.assertEqual(socket.switches, [])

        # Mimic 59 min 55 sec into the one-hour delay.
        state["off_since_monotonic"] = time.monotonic() - 3595
        state["power_off_due_monotonic"] = time.monotonic() + 5
        old_deadline = state["power_off_due_monotonic"]

        # PV surplus resumes before the power cut: cancel the old timer.
        asyncio.run(apply("p1"))
        self.assertEqual(miner.info.profile, "p1")
        self.assertEqual(state["state"], "running")
        self.assertNotIn("off_since_monotonic", state)
        self.assertNotIn("power_off_due_monotonic", state)
        self.assertNotIn("power_off_due_at", state)
        self.assertNotIn("off_since", state)
        self.assertEqual(socket.switches, [])
        self.assertEqual(calls, ["off", "p1"])

        # Even after the previous deadline, no pending power cut can fire.
        # A subsequent stop must get an entirely new one-hour grace period.
        asyncio.run(apply("off"))
        second_deadline = state["power_off_due_monotonic"]
        self.assertEqual(state["state"], "off_timer")
        self.assertGreater(second_deadline, time.monotonic() + 3500)
        self.assertGreater(second_deadline - old_deadline, 3500)
        asyncio.run(apply("off"))
        self.assertEqual(socket.switches, [])
        self.assertEqual(calls, ["off", "p1", "off", "off"])

    def test_turned_on_again_still_honors_startup_delay(self):
        socket, _, calls, apply = self.make_env(is_on=False)
        asyncio.run(apply("off"))
        self.assertEqual(calls, [])
        asyncio.run(apply("p1"))
        state = workflow._workflow_state("m-test")
        self.assertEqual(socket.switches, ["on"])
        self.assertEqual(state["state"], "startup_delay")
        self.assertEqual(calls, [])
        state["startup_due_monotonic"] = time.monotonic() - 1
        asyncio.run(apply("p1"))
        self.assertEqual(calls, ["p1"])
        self.assertEqual(state["state"], "running")

    def test_unreachable_socket_does_not_fake_confirmed_off(self):
        _, _, calls, apply = self.make_env(is_on=None, reachable=False)
        asyncio.run(apply("off"))
        self.assertEqual(calls, ["off"])
        self.assertEqual(workflow._workflow_state("m-test")["state"], "off_timer")

    def test_dashboard_socket_off_is_not_running(self):
        from pv2hash import app
        info = MinerInfo(
            id="m-test", name="Antminer", host="miner.invalid",
            driver="whatsminer_api3",
            runtime_state="socket_off", reachable=False, profile="off",
        )
        state = SimpleNamespace(
            config={"miners": [{"id": "m-test", "name": "Antminer", "host": "miner.invalid"}]},
            miners=[info],
        )
        with patch.object(app, "state", state):
            rows = app._build_dashboard_miner_rows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["is_running"])
        self.assertFalse(rows[0]["reachable"])


if __name__ == "__main__":
    unittest.main()
