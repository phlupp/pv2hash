from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from pv2hash.models.energy import EnergySnapshot


class EnergySource(ABC):
    driver_id = "unknown"
    driver_label = "Unbekannte Source"

    @abstractmethod
    async def read(self) -> EnergySnapshot:
        raise NotImplementedError

    def get_config_fields(self, *, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    def get_detail_groups(
        self,
        *,
        snapshot: EnergySnapshot | None = None,
        debug_info: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def get_actions(self, *, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    def get_warnings(self, *, config: dict[str, Any] | None = None) -> list[str]:
        return []

    async def run_action(self, action: str, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"status": "error", "message": f"Unbekannte Source-Aktion: {action}"}

    def close(self) -> None:
        return None

    def get_header_fields(
        self,
        *,
        snapshot: EnergySnapshot | None = None,
        debug_info: dict[str, Any] | None = None,
        status: dict[str, Any] | None = None,
        detail_groups: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        status = status or {}
        fields.append({"label": "Status", "value": status.get("text") or "—"})
        age_seconds = status.get("age_seconds")
        fields.append({"label": "Alter", "value": age_seconds, "unit": "s", "precision": 1})

        return fields

    def get_gui_model(
        self,
        *,
        source_id: str,
        role: str,
        title: str,
        enabled: bool = True,
        config: dict[str, Any] | None = None,
        snapshot: EnergySnapshot | None = None,
        debug_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = config or {}
        debug_info = debug_info or getattr(self, "debug_info", {}) or {}
        source_type = str(config.get("type") or self.driver_id or "unknown")
        source_name = str(config.get("name") or self.driver_label or source_type)
        quality = str(getattr(snapshot, "quality", None) or debug_info.get("current_quality") or ("disabled" if not enabled else "unknown"))

        age_seconds = None
        updated_at = getattr(snapshot, "updated_at", None)
        if updated_at is not None:
            try:
                age_seconds = max(0.0, (datetime.now(UTC) - updated_at).total_seconds())
            except Exception:
                age_seconds = None

        status = {
            "state": quality,
            "text": self._format_quality_text(quality),
            "age_seconds": age_seconds,
            "updated_at": updated_at.isoformat() if updated_at is not None else None,
        }
        detail_groups = self.get_detail_groups(snapshot=snapshot, debug_info=debug_info)

        return {
            "id": source_id,
            "role": role,
            "title": title,
            "enabled": bool(enabled),
            "driver": source_type,
            "driver_label": source_name,
            "status": status,
            "header_fields": self.get_header_fields(
                snapshot=snapshot,
                debug_info=debug_info,
                status=status,
                detail_groups=detail_groups,
            ),
            "config_fields": self.get_config_fields(config=config),
            "detail_groups": detail_groups,
            "capabilities": self.get_capabilities(),
            "actions": self.get_actions(config=config),
            "warnings": self.get_warnings(config=config),
        }

    def get_capabilities(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def _format_quality_text(quality: str) -> str:
        return {
            "live": "Live",
            "simulated": "Simulation",
            "stale": "Veraltet",
            "offline": "Offline",
            "no_data": "Keine Daten",
            "disabled": "Deaktiviert",
            "unknown": "Unbekannt",
        }.get(str(quality or "unknown"), str(quality or "unknown"))
