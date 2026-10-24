"""Atomic, round-based PV generation aggregation.

Each independently configured device contributes at most one value *per round*.
No intermediate results are published. The last completed snapshot is the
single read-only source for dashboard, DataLogger, Influx and diagnostics.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pv2hash.logging_ext.setup import get_logger
from pv2hash.sources.pv_reading import PvReading

logger = get_logger("pv2hash.source.pv_group")


def pv_device_configs(pv: dict) -> list[dict]:
    """Backward compatible read of an old single-device PV configuration."""
    if pv.get("type") == "pv_modbus" and not pv.get("devices"):
        return [{
            "id": str(pv.get("uuid") or "legacy-pv"),
            "name": str(pv.get("name") or "Wechselrichter 1"),
            "enabled": bool(pv.get("enabled", False)),
            "driver": "pv_modbus", "settings": pv.get("settings", {}),
        }]
    if isinstance(pv.get("devices"), list):
        return [x for x in pv["devices"] if isinstance(x, dict)]
    return []


@dataclass(frozen=True)
class PvRound:
    number: int = 0
    power_w: float | None = None
    quality: str = "no_data"
    updated_at: datetime | None = None
    devices: tuple[dict, ...] = ()
    live_count: int = 0
    standby_count: int = 0
    enabled_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number, "power_w": self.power_w,
            "quality": self.quality,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "live_count": self.live_count, "standby_count": self.standby_count,
            "enabled_count": self.enabled_count,
            "devices": [dict(x) for x in self.devices],
        }


class PvGenerationGroup:
    driver_id = "pv_group"
    driver_label = "PV-Erzeugung (gesamt)"

    def __init__(self, devices: list[tuple[dict, Any]], poll_interval_ms: int = 5000):
        # These adapters are read-only driver implementations. They never publish
        # their own values to the shared API; only the finished round does.
        self.devices = list(devices)
        self.driver_label = (
            devices[0][1].driver_label if len(devices) == 1
            else "PV-Erzeugung (gesamt)"
        )
        self.poll_interval_ms = int(poll_interval_ms)
        self._round = PvRound(
            devices=tuple({
                "id": cfg["id"], "name": cfg["name"],
                "driver": cfg["driver"], "driver_label": adapter.driver_label,
                "quality": "no_data", "power_w": None,
                "updated_at": None, "last_error": None, "register_values": [],
            } for cfg, adapter in devices),
            enabled_count=len(devices),
        )
        self._closed = False
        self._poll_lock = asyncio.Lock()

    @property
    def snapshot(self) -> PvRound:
        return self._round

    @property
    def power_w(self) -> float | None:
        return self._round.power_w

    @property
    def quality(self) -> str:
        return self._round.quality

    @property
    def updated_at(self) -> datetime | None:
        return self._round.updated_at

    @property
    def debug_info(self) -> dict:
        state = self._round
        return {
            "current_quality": state.quality,
            "pv_power_w": state.power_w,
            "last_live_packet_at": state.updated_at.isoformat() if state.updated_at else None,
            "round_number": state.number,
            "live_count": state.live_count,
            "standby_count": state.standby_count,
            "enabled_count": state.enabled_count,
            "devices": [dict(x) for x in state.devices],
        }

    @staticmethod
    async def _read_one(cfg: dict, adapter: Any) -> dict:
        timeout = float(getattr(adapter, "round_timeout_seconds", adapter.request_timeout_seconds))
        result = {
            "id": cfg["id"], "name": cfg["name"],
            "driver": cfg["driver"], "driver_label": adapter.driver_label,
            "quality": "offline", "power_w": None,
            "updated_at": None, "last_error": None, "register_values": [],
        }
        try:
            # The socket read happens in an independent thread and does not
            # mutate the published state. Timed-out threads cannot publish a
            # late result into the *next* round.
            reading = await asyncio.wait_for(
                asyncio.to_thread(adapter._poll_reading), timeout=timeout)
            if not isinstance(reading, PvReading):
                raise TypeError("PV driver returned no standardized PvReading")
            result.update(power_w=reading.power_w, quality=reading.quality,
                          register_values=reading.register_values,
                          updated_at=datetime.now(UTC).isoformat(),
                          note=reading.note)
        except TimeoutError:
            result["last_error"] = f"Timeout nach {timeout:g} s"
        except Exception as exc:
            result["last_error"] = str(exc)
        return result

    async def read(self) -> float | None:
        async with self._poll_lock:
            if self._closed:
                return None
            # All reads start together. No device writes into _round itself.
            members = await asyncio.gather(
                *(self._read_one(cfg, adapter) for cfg, adapter in self.devices),
            )
            if self._closed:
                return None
            live = [m for m in members if m["quality"] == "live"]
            standby = [m for m in members if m["quality"] == "standby"]
            usable = live + standby
            count = len(self.devices)
            # Standby contributes a documented 0 W but is kept explicitly
            # distinct from a measured live value in the member diagnostics.
            quality = ("live" if len(usable) == count else
                       "degraded" if usable else "offline")
            total = sum(m["power_w"] for m in usable) if usable else None
            completed = PvRound(
                number=self._round.number + 1,
                power_w=total, quality=quality,
                updated_at=datetime.now(UTC),
                devices=tuple(members),
                live_count=len(live), standby_count=len(standby), enabled_count=count,
            )
            # This is the ONLY publication point, after gather/timeout.
            self._round = completed
            return total

    def close(self) -> None:
        self._closed = True
        for _, adapter in self.devices:
            adapter.close()
