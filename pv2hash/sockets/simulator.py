from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pv2hash.sockets.base import SocketAdapter, SocketInfo


class SimulatorSocket(SocketAdapter):
    driver_id = "simulator"
    driver_label = "Simulator Socket"

    def __init__(self, info: SocketInfo, settings: dict[str, Any] | None = None) -> None:
        super().__init__(info, settings)
        self._is_on = bool(self.settings.get("initial_on", False))
        self._reachable = bool(self.settings.get("reachable", True))
        self._on_power_w = float(self.settings.get("on_power_w", 50.0) or 50.0)
        self._standby_power_w = float(self.settings.get("standby_power_w", 0.0) or 0.0)

    def _update_info(self) -> SocketInfo:
        self.info.reachable = self._reachable and self.info.monitor_enabled and self.info.enabled
        self.info.is_on = self._is_on if self.info.reachable else None
        self.info.power_w = (self._on_power_w if self._is_on else self._standby_power_w) if self.info.reachable else None
        self.info.runtime_state = "on" if self._is_on and self.info.reachable else "off" if self.info.reachable else "unreachable"
        self.info.last_seen = datetime.now(UTC) if self.info.reachable else self.info.last_seen
        self.info.last_error = None if self.info.reachable else "Simulator Socket nicht erreichbar."
        return self.info

    def get_status(self) -> SocketInfo:
        return self._update_info()

    def switch_on(self) -> dict[str, Any]:
        if not self.info.enabled or not self.info.monitor_enabled:
            return {"ok": False, "message": "Socket ist deaktiviert."}
        if not self._reachable:
            self._update_info()
            return {"ok": False, "message": "Socket ist nicht erreichbar."}
        self._is_on = True
        self._update_info()
        return {"ok": True, "message": "Socket eingeschaltet."}

    def switch_off(self) -> dict[str, Any]:
        if not self.info.enabled or not self.info.monitor_enabled:
            return {"ok": False, "message": "Socket ist deaktiviert."}
        if not self._reachable:
            self._update_info()
            return {"ok": False, "message": "Socket ist nicht erreichbar."}
        self._is_on = False
        self._update_info()
        return {"ok": True, "message": "Socket ausgeschaltet."}
