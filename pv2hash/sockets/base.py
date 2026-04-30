from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class SocketInfo:
    id: str
    uuid: str
    name: str
    driver: str
    host: str = ""
    priority: int = 100
    enabled: bool = True
    monitor_enabled: bool = True
    control_enabled: bool = False
    reachable: bool = False
    quality: str = "no_data"
    is_on: bool | None = None
    power_w: float | None = None
    runtime_state: str = "unknown"
    last_seen: datetime | None = None
    last_error: str | None = None
    details: dict[str, Any] | None = None


class SocketAdapter:
    driver_id = "base"
    driver_label = "Socket"

    def __init__(self, info: SocketInfo, settings: dict[str, Any] | None = None) -> None:
        self.info = info
        self.settings = settings or {}

    def get_status(self) -> SocketInfo:
        raise NotImplementedError

    def switch_on(self) -> dict[str, Any]:
        raise NotImplementedError

    def switch_off(self) -> dict[str, Any]:
        raise NotImplementedError

    def reboot(self) -> dict[str, Any]:
        return {"ok": False, "message": "Neustart wird von diesem Socket-Treiber nicht unterstützt."}

    def get_details(self) -> dict[str, Any]:
        return dict(self.info.details or {})

    def close(self) -> None:
        return None
