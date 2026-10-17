"""Read-only socket GUI metadata. The driver schema is the only field catalog for GUI 2.0.

The current socket adapters do not yet provide DriverField declarations as
miner adapters do. Keep the legacy forms and this schema aligned until the
socket adapter contract can own its own fields.
"""
from __future__ import annotations

from typing import Any


def field(name: str, label: str, kind: str, default: Any = "", **props: Any) -> dict[str, Any]:
    return {"name": name, "label": label, "type": kind, "default": default, **props}


def driver_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": "simulator",
            "label": "Simulator Socket",
            "default_name": "Simulator Socket",
            "default_host": "simulator.local",
            "fields": [
                field("reachable", "Simulator erreichbar", "checkbox", True, width="quarter"),
                field("on_power_w", "Leistung ein (W)", "number", 50, width="quarter"),
                field("standby_power_w", "Leistung aus (W)", "number", 0, width="quarter"),
            ],
        },
        {
            "id": "tasmota_http",
            "label": "Tasmota HTTP",
            "default_name": "Tasmota Socket",
            "default_host": "",
            "fields": [
                field("port", "Port", "number", 80, min=1, max=65535, width="quarter"),
                field("relay", "Relais", "number", 1, min=1, width="quarter"),
                field("timeout_s", "Timeout (s)", "number", 2.0, min=0.1, step=0.1, width="quarter"),
                field("use_energy", "Leistung lesen", "checkbox", True, width="quarter"),
                field("username", "Benutzername (optional)", "text", "", width="half"),
                field("password", "Passwort (optional)", "password", "", width="half",
                      help="Leer lassen, um ein gespeichertes Passwort unverändert zu lassen."),
            ],
        },
    ]


def socket_gui_item(view: dict[str, Any]) -> dict[str, Any]:
    settings = view.get("settings") or {}
    driver = str(view.get("driver") or "simulator")
    permitted = {f["name"] for d in driver_catalog() if d["id"] == driver for f in d["fields"]}
    # Never send passwords or other arbitrary settings to the browser.
    safe_settings = {key: value for key, value in settings.items() if key in permitted and key != "password"}
    return {
        "id": str(view.get("id") or ""),
        "name": str(view.get("name") or ""),
        "driver": driver,
        "host": str(view.get("host") or ""),
        "priority": int(view.get("priority", 100) or 100),
        "monitor_enabled": bool(view.get("monitor_enabled", True)),
        "control_enabled": bool(view.get("control_enabled", False)),
        "settings": safe_settings,
        "password_configured": bool(settings.get("password")),
        "assignment": {
            "role": str((view.get("assignment") or {}).get("role") or ""),
            "target_id": str((view.get("assignment") or {}).get("target_id") or ""),
        },
    }
