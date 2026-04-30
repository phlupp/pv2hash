from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import socket
import sys
from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any
from datetime import UTC, datetime
from time import monotonic
from pathlib import Path
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import FastAPI, Form, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pv2hash.config.store import CONFIG_PATH, load_config, save_config
from pv2hash.identity import load_instance_identity
from pv2hash.datalogger import DataLogger, normalize_datalogger_config
from pv2hash.logging_ext.setup import (
    get_log_file_path,
    get_logger,
    get_ringbuffer_lines,
    setup_logging,
)
from pv2hash.runtime import AppState
from pv2hash.miners.base import DriverAction, DriverField, DriverFieldChoice, MinerAdapter
from pv2hash.miners.braiins import BraiinsMiner
from pv2hash.miners.axeos import AxeOsMiner
from pv2hash.miners.simulator import SimulatorMiner
from pv2hash.miners.whatsminer_api3 import WhatsminerApi3Miner
from pv2hash.self_update import SelfUpdateManager
from pv2hash.services import RuntimeServices
from pv2hash.update_check import UpdateChecker
from pv2hash.version import APP_VERSION, APP_VERSION_FULL
from pv2hash.netutils import get_local_ipv4_networks
from pv2hash.sockets.tasmota_http import discover_tasmota_http

initial_config = load_config()
setup_logging(initial_config.get("system", {}).get("log_level", "INFO"))
logger = get_logger("pv2hash.app")
instance_identity = load_instance_identity()

app = FastAPI(title="PV2Hash", version=APP_VERSION_FULL)
app.mount("/static", StaticFiles(directory="pv2hash/static"), name="static")
templates = Jinja2Templates(directory="pv2hash/templates")
templates.env.globals["app_version_full"] = APP_VERSION_FULL

state = AppState(config=initial_config)
services = RuntimeServices(state)
services.reload_from_config()
update_checker = UpdateChecker(
    state,
    current_version=APP_VERSION,
)
self_update_manager = SelfUpdateManager(
    current_version=APP_VERSION,
)
data_logger = DataLogger(
    config_provider=lambda: state.config,
    snapshot_provider=lambda: _build_runtime_snapshot_payload(),
)

EDITABLE_PROFILE_NAMES = ("p1", "p2", "p3", "p4")
ALL_PROFILE_NAMES = ("off", "p1", "p2", "p3", "p4")
EDITABLE_MIN_REGULATED_PROFILE_NAMES = ("off", "p1", "p2", "p3", "p4")
MODBUS_REGISTER_TYPES = ("holding", "input", "coil", "discrete_input")
MODBUS_VALUE_TYPES = ("int8", "uint8", "int16", "uint16", "int32", "uint32", "float32")
MODBUS_ENDIAN_TYPES = ("big_endian", "little_endian")


def _build_instance_info() -> dict[str, Any]:
    return {
        "id": instance_identity.id,
        "created_at": instance_identity.created_at,
        "name": state.config.get("system", {}).get("instance_name", "PV2Hash Node"),
        "version": APP_VERSION,
        "version_full": APP_VERSION_FULL,
    }


def _device_uuid(config: dict[str, Any]) -> str:
    return str(config.get("uuid") or "")


def _build_runtime_snapshot_payload() -> dict[str, Any]:
    snapshot = state.snapshot
    runtime_miners = {miner.id: miner for miner in state.miners}
    config_miners = state.config.get("miners", []) if state.config else []

    miners: list[dict[str, Any]] = []
    for miner_cfg in config_miners:
        miner_key = str(miner_cfg.get("id") or "")
        runtime_miner = runtime_miners.get(miner_key)
        miners.append({
            "id": _device_uuid(miner_cfg),
            "key": miner_key,
            "name": str(miner_cfg.get("name") or miner_key),
            "driver": str(miner_cfg.get("driver") or ""),
            "host": str(miner_cfg.get("host") or ""),
            "priority": int(miner_cfg.get("priority", 100) or 100),
            "monitor_enabled": bool(miner_cfg.get("monitor_enabled", True)),
            "control_enabled": bool(miner_cfg.get("control_enabled", True)),
            "reachable": bool(runtime_miner.reachable) if runtime_miner else False,
            "profile": str(runtime_miner.profile or "off") if runtime_miner else "off",
            "power_w": float(runtime_miner.power_w) if runtime_miner else 0.0,
            "hashrate_ghs": getattr(runtime_miner, "current_hashrate_ghs", None) if runtime_miner else None,
            "runtime_state": str(getattr(runtime_miner, "runtime_state", "unknown") or "unknown") if runtime_miner else "unknown",
        })

    source_cfg = state.config.get("source", {}) if state.config else {}
    battery_cfg = state.config.get("battery", {}) if state.config else {}
    controller_status = _build_controller_status()

    return {
        "status": "ok",
        "timestamp": datetime.now(UTC),
        "instance": _build_instance_info(),
        "host": _build_snapshot_host_info(),
        "controller": {
            "policy_mode": state.config.get("control", {}).get("policy_mode"),
            "distribution_mode": state.config.get("control", {}).get("distribution_mode"),
            "summary": controller_status.get("summary_text"),
            "last_decision": state.last_decision,
            "last_decision_at": state.last_decision_at,
            "last_profile_switch_at": state.last_profile_switch_at,
        },
        "source": {
            "id": _device_uuid(source_cfg),
            "key": "source",
            "role": "grid",
            "type": source_cfg.get("type"),
            "name": source_cfg.get("name"),
            "enabled": bool(source_cfg.get("enabled", True)),
            "quality": getattr(snapshot, "quality", None) if snapshot else None,
            "grid_power_w": float(snapshot.grid_power_w) if snapshot else None,
        },
        "battery": {
            "id": _device_uuid(battery_cfg),
            "key": "battery",
            "role": "battery",
            "type": battery_cfg.get("type"),
            "name": battery_cfg.get("name"),
            "enabled": bool(battery_cfg.get("enabled", False)) and battery_cfg.get("type") != "none",
            "quality": getattr(snapshot, "battery_quality", None) if snapshot else None,
            "updated_at": getattr(snapshot, "battery_updated_at", None) if snapshot else None,
            "soc_pct": snapshot.battery_soc_pct if snapshot else None,
            "charge_power_w": snapshot.battery_charge_power_w if snapshot else None,
            "discharge_power_w": snapshot.battery_discharge_power_w if snapshot else None,
            "is_active": bool(snapshot.battery_is_active) if snapshot else False,
            "is_charging": bool(snapshot.battery_is_charging) if snapshot else False,
            "is_discharging": bool(snapshot.battery_is_discharging) if snapshot else False,
        },
        "miners": miners,
        "sockets": _build_socket_snapshot_items(),
        "totals": {
            "miner_power_w": sum(item["power_w"] for item in miners),
            "miner_hashrate_ghs": sum(float(item.get("hashrate_ghs") or 0.0) for item in miners),
            "control_enabled_miner_count": sum(1 for item in miners if item.get("control_enabled")),
            "monitor_enabled_miner_count": sum(1 for item in miners if item.get("monitor_enabled")),
            "reachable_miner_count": sum(1 for item in miners if item.get("reachable")),
            "socket_power_w": sum(float(item.get("power_w") or 0.0) for item in _build_socket_snapshot_items()),
            "reachable_socket_count": sum(1 for item in _build_socket_snapshot_items() if item.get("reachable")),
            "monitor_enabled_socket_count": sum(1 for item in _build_socket_snapshot_items() if item.get("monitor_enabled")),
        },
    }




def _json_safe_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _socket_status_payload(info) -> dict[str, Any]:
    return {
        "id": str(getattr(info, "uuid", "") or ""),
        "key": str(getattr(info, "id", "") or ""),
        "uuid": str(getattr(info, "uuid", "") or ""),
        "name": str(getattr(info, "name", "Socket") or "Socket"),
        "driver": str(getattr(info, "driver", "") or ""),
        "host": str(getattr(info, "host", "") or ""),
        "priority": int(getattr(info, "priority", 100) or 100),
        "enabled": bool(getattr(info, "enabled", True)),
        "monitor_enabled": bool(getattr(info, "monitor_enabled", True)),
        "control_enabled": bool(getattr(info, "control_enabled", False)),
        "reachable": bool(getattr(info, "reachable", False)),
        "quality": str(getattr(info, "quality", "no_data") or "no_data"),
        "is_on": getattr(info, "is_on", None),
        "power_w": getattr(info, "power_w", None),
        "runtime_state": str(getattr(info, "runtime_state", "unknown") or "unknown"),
        "last_seen": _json_safe_datetime(getattr(info, "last_seen", None)),
        "last_error": getattr(info, "last_error", None),
        "details": dict(getattr(info, "details", None) or {}),
    }


def _build_socket_snapshot_items() -> list[dict[str, Any]]:
    runtime_by_id = {str(getattr(socket, "id", "")): socket for socket in state.sockets}
    items: list[dict[str, Any]] = []
    for socket_cfg in state.config.get("sockets", []) or []:
        socket_key = str(socket_cfg.get("id") or "")
        runtime = runtime_by_id.get(socket_key)
        if runtime is not None:
            payload = _socket_status_payload(runtime)
        else:
            payload = {
                "id": _device_uuid(socket_cfg),
                "key": socket_key,
                "uuid": _device_uuid(socket_cfg),
                "name": str(socket_cfg.get("name") or socket_key or "Socket"),
                "driver": str(socket_cfg.get("driver") or ""),
                "host": str(socket_cfg.get("host") or ""),
                "priority": int(socket_cfg.get("priority", 100) or 100),
                "enabled": bool(socket_cfg.get("enabled", True)),
                "monitor_enabled": bool(socket_cfg.get("monitor_enabled", True)),
                "control_enabled": bool(socket_cfg.get("control_enabled", False)),
                "reachable": False,
                "quality": "no_data",
                "is_on": None,
                "power_w": None,
                "runtime_state": "unknown",
                "last_seen": None,
                "last_error": None,
                "details": {},
            }
        items.append(payload)
    return items


def _get_runtime_socket_adapter_by_id(socket_id: str):
    for adapter in services.sockets:
        if getattr(adapter.info, "id", None) == socket_id:
            return adapter
    return None


def _socket_summary(payload: dict[str, Any]) -> dict[str, str]:
    if not payload.get("monitor_enabled"):
        state_text = "Verbindung aus"
        state_class = "neutral"
    elif payload.get("reachable") and payload.get("is_on") is True:
        state_text = "Ein"
        state_class = "ok"
    elif payload.get("reachable") and payload.get("is_on") is False:
        state_text = "Aus"
        state_class = "neutral"
    else:
        state_text = "Nicht erreichbar"
        state_class = "bad"

    power = payload.get("power_w")
    return {
        "state_text": state_text,
        "state_class": state_class,
        "power_text": f"{float(power):.0f} W" if power is not None else "—",
        "connection_text": "Verbindung OK" if payload.get("reachable") else "Keine Verbindung",
        "connection_class": "ok" if payload.get("reachable") else "bad",
    }


def _build_sockets_view() -> list[dict[str, Any]]:
    runtime_items = {item.get("key") or item.get("id"): item for item in _build_socket_snapshot_items()}
    views: list[dict[str, Any]] = []
    for socket_cfg in state.config.get("sockets", []) or []:
        item = deepcopy(socket_cfg)
        item.setdefault("settings", {})
        item["uuid"] = _device_uuid(item)
        runtime = runtime_items.get(str(item.get("id") or ""), {})
        item["runtime"] = runtime
        item["summary"] = _socket_summary(runtime)
        views.append(item)
    return views


def _sockets_context(request: Request) -> dict[str, Any]:
    return {
        "request": request,
        "sockets": _build_sockets_view(),
        "instance_name": state.config["system"].get("instance_name", "PV2Hash Node"),
        "app_version_full": APP_VERSION_FULL,
        "update_check": update_checker.snapshot(),
        "refresh_seconds": _safe_int(state.config.get("app", {}).get("refresh_seconds", 5), 5),
    }

DRIVER_CLASSES: dict[str, type[MinerAdapter]] = {
    "simulator": SimulatorMiner,
    "braiins": BraiinsMiner,
    "axeos": AxeOsMiner,
    "whatsminer_api3": WhatsminerApi3Miner,
}


def _driver_class_for(driver: str | None) -> type[MinerAdapter] | None:
    return DRIVER_CLASSES.get(_normalize_miner_driver(driver))


def _driver_supports_gui_schema(driver: str | None) -> bool:
    driver_cls = _driver_class_for(driver)
    return bool(driver_cls and driver_cls.supports_gui_schema())


def _choice(value: str, label: str) -> DriverFieldChoice:
    return DriverFieldChoice(value=value, label=label)


def _core_identity_schema() -> list[DriverField]:
    return [
        DriverField(
            name="name",
            label="Name",
            type="text",
            required=True,
            preset="Miner",
            default="Miner",
            create_phase="basic",
            placeholder="Miner",
            layout={"width": "full"},
        ),
        DriverField(
            name="monitor_enabled",
            label="Verbindung",
            type="checkbox",
            default=True,
            help="PV2Hash baut den Miner-Adapter auf, liest Status und erlaubt Geräte-Einstellungen.",
            layout={"width": "half"},
        ),
        DriverField(
            name="control_enabled",
            label="In Regelung einbeziehen",
            type="checkbox",
            default=True,
            help="Der PV-Regler darf diesen Miner steuern. Erfordert Verbindung.",
            layout={"width": "half"},
        ),
    ]


def _core_control_schema(driver: str | None = None) -> list[DriverField]:
    driver_cls = _driver_class_for(driver)
    fixed_power_profiles = bool(driver_cls and driver_cls.has_fixed_power_profiles())
    battery_choices = (
        _choice("p1", "p1"),
        _choice("p2", "p2"),
        _choice("p3", "p3"),
        _choice("p4", "p4"),
    )
    min_profile_choices = (
        _choice("off", "off"),
        _choice("p1", "p1"),
        _choice("p2", "p2"),
        _choice("p3", "p3"),
        _choice("p4", "p4"),
    )
    return [
        DriverField(name="priority", label="Priorität", type="number", default=100, placeholder="100", layout={"width": "half"}),
        DriverField(name="min_regulated_profile", label="Min. Regelprofil", type="select", default="off", choices=min_profile_choices, layout={"width": "half"}),
        DriverField(name="profiles.p1.power_w", label="Profil p1", type="number", unit="W", default=900, read_only=fixed_power_profiles, layout={"width": "quarter"}),
        DriverField(name="profiles.p2.power_w", label="Profil p2", type="number", unit="W", default=1800, read_only=fixed_power_profiles, layout={"width": "quarter"}),
        DriverField(name="profiles.p3.power_w", label="Profil p3", type="number", unit="W", default=3000, read_only=fixed_power_profiles, layout={"width": "quarter"}),
        DriverField(name="profiles.p4.power_w", label="Profil p4", type="number", unit="W", default=4200, read_only=fixed_power_profiles, layout={"width": "quarter"}),
        DriverField(name="use_battery_when_charging", label="Beim Laden Batterie nutzen", type="checkbox", default=False, layout={"width": "full"}),
        DriverField(name="battery_charge_soc_min", label="Mindest-SOC Laden", type="number", unit="%", default=95, layout={"width": "half"}),
        DriverField(name="battery_charge_profile", label="Profil bei Laden", type="select", default="p1", choices=battery_choices, layout={"width": "half"}),
        DriverField(name="use_battery_when_discharging", label="Beim Entladen Batterie nutzen", type="checkbox", default=False, layout={"width": "full"}),
        DriverField(name="battery_discharge_soc_min", label="Mindest-SOC Entladen", type="number", unit="%", default=80, layout={"width": "half"}),
        DriverField(name="battery_discharge_profile", label="Profil bei Entladen", type="select", default="p1", choices=battery_choices, layout={"width": "half"}),
    ]

def _get_nested_value(data: dict, path: str, fallback: Any = None) -> Any:
    current: Any = data
    for part in path.split('.'):
        if not isinstance(current, dict) or part not in current:
            return fallback
        current = current[part]
    return current


def _set_nested_value(data: dict, path: str, value: Any) -> None:
    parts = path.split('.')
    current = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _field_value_from_config(field: DriverField, miner_cfg: dict) -> Any:
    value = _get_nested_value(miner_cfg, field.name, None)
    if value is None:
        if field.default is not None:
            return field.default
        return field.preset
    return value


def _coerce_field_value(field: DriverField, raw: Any, fallback: Any = None) -> Any:
    if field.type == "checkbox":
        return bool(raw)
    if raw in (None, ""):
        if field.type == "number" and fallback is None and field.default is None and field.preset is None:
            return None
        if fallback is not None:
            return fallback
        if field.default is not None:
            return field.default
        return field.preset
    if field.type == "number":
        default_value = fallback
        if default_value is None:
            default_value = field.default if field.default is not None else field.preset
        if isinstance(default_value, float):
            return _safe_float(raw, float(default_value))
        if default_value is None:
            return _safe_int(raw, 0)
        return _safe_int(raw, int(float(default_value or 0)))
    return str(raw).strip()


def _normalize_field_layout(layout: dict | None) -> dict:
    layout = layout or {}
    width = str(layout.get("width") or "full").strip().lower()
    if width not in {"full", "half", "third", "quarter", "auto"}:
        width = "full"
    return {**layout, "width": width}


def _render_field(field: DriverField, value: Any) -> dict:
    rendered = {
        **asdict(field),
        "value": value,
        "id": field.name.replace('.', '-'),
    }
    rendered["layout"] = _normalize_field_layout(rendered.get("layout"))
    if field.choices:
        rendered["options"] = [asdict(choice) for choice in field.choices]
    return rendered


def _driver_schema(driver: str | None) -> list[DriverField]:
    driver_cls = _driver_class_for(driver)
    if driver_cls is None:
        return []
    return list(driver_cls.get_config_schema())


def _driver_device_settings_schema(driver: str | None) -> list[DriverField]:
    driver_cls = _driver_class_for(driver)
    if driver_cls is None:
        return []
    return list(driver_cls.get_device_settings_schema())


def _driver_actions_schema(driver: str | None) -> list[DriverAction]:
    driver_cls = _driver_class_for(driver)
    if driver_cls is None:
        return []
    return list(driver_cls.get_actions_schema())


def _driver_basic_fields(driver: str | None) -> list[dict]:
    fields = []
    for field in _driver_schema(driver):
        if field.create_phase == "basic":
            initial = field.preset if field.preset is not None else field.default
            fields.append(_render_field(field, initial))
    return fields


def _driver_full_fields(driver: str | None, miner_cfg: dict) -> list[dict]:
    return [_render_field(field, _field_value_from_config(field, miner_cfg)) for field in _driver_schema(driver)]


def _driver_device_settings_fields(driver: str | None, live_values: dict | None = None) -> list[dict]:
    live_values = live_values or {}
    fields: list[dict] = []
    for field in _driver_device_settings_schema(driver):
        if field.name in live_values:
            rendered = _render_field(field, live_values.get(field.name))
            rendered["value_source"] = "device"
            rendered["value_known"] = True
        else:
            rendered = _render_field(field, None)
            rendered["value_source"] = "unknown"
            rendered["value_known"] = False
        fields.append(rendered)
    return fields


def _render_action(action: DriverAction) -> dict:
    return asdict(action)


def _driver_action_fields(driver: str | None) -> list[dict]:
    return [_render_action(action) for action in _driver_actions_schema(driver)]




def _setting_field(name: str, label: str, field_type: str, value: Any, **kwargs: Any) -> dict:
    field = {"name": name, "label": label, "type": field_type, "value": value, "layout": _normalize_field_layout(kwargs.pop("layout", None))}
    field.update({key: val for key, val in kwargs.items() if val is not None})
    return field


def _settings_select_options(items: tuple[tuple[str, str], ...]) -> list[dict]:
    return [{"value": value, "label": label} for value, label in items]


def _build_settings_model() -> dict:
    system_cfg = state.config.setdefault("system", {})
    app_cfg = state.config.setdefault("app", {})
    control_cfg = state.config.setdefault("control", {})
    datalogger_cfg = normalize_datalogger_config(state.config.setdefault("datalogger", {}))
    source_loss = control_cfg.setdefault("source_loss", {})
    stale_loss = source_loss.setdefault("stale", {})
    offline_loss = source_loss.setdefault("offline", {})
    mode_options = _settings_select_options((("hold_current", "Aktuellen Zustand halten"), ("force_profile", "Fallback-Profil erzwingen"), ("off_all", "Alle Miner ausschalten")))
    profile_options = _settings_select_options((("off", "off"), ("p1", "p1"), ("p2", "p2"), ("p3", "p3"), ("p4", "p4")))
    return {"sections": [
        {"id": "system", "title": "Instanz", "subtitle": "Grunddaten und Aktualisierungsintervall der Oberfläche.", "fields": [
            _setting_field("instance_id", "Instanz-ID", "text", instance_identity.id, disabled=True, help="Stabile lokale UUID dieser PV2Hash-Installation. Wird für Historie und spätere Portal-Anbindung genutzt.", layout={"width": "full"}),
            _setting_field("instance_name", "Instanzname", "text", system_cfg.get("instance_name", "PV2Hash Node"), required=True, layout={"width": "half"}),
            _setting_field("refresh_seconds", "Live-Aktualisierung", "number", app_cfg.get("refresh_seconds", 5), min=1, step=1, unit="s", layout={"width": "half"}),
        ]},
        {"id": "control", "title": "Regelung", "subtitle": "Grundverhalten des PV-Reglers und Schaltabstände.", "fields": [
            _setting_field("policy_mode", "Reglermodus", "select", control_cfg.get("policy_mode", "coarse"), options=_settings_select_options((("coarse", "Coarse"),)), layout={"width": "half"}),
            _setting_field("distribution_mode", "Verteilungsstrategie", "select", control_cfg.get("distribution_mode", "equal"), options=_settings_select_options((("equal", "Equal"), ("cascade", "Cascade"))), layout={"width": "half"}),
            _setting_field("switch_hysteresis_w", "Schalt-Hysterese", "number", control_cfg.get("switch_hysteresis_w", 100), min=0, step=1, unit="W", layout={"width": "third"}),
            _setting_field("min_switch_interval_seconds", "Min. Schaltabstand", "number", control_cfg.get("min_switch_interval_seconds", 60), min=0, step=1, unit="s", layout={"width": "third"}),
            _setting_field("max_import_w", "Max. Netzbezug", "number", control_cfg.get("max_import_w", 200), min=0, step=1, unit="W", layout={"width": "third"}),
            _setting_field("import_hold_seconds", "Netzbezug halten", "number", control_cfg.get("import_hold_seconds", 15), min=0, step=1, unit="s", layout={"width": "third"}),
        ]},
        {"id": "source-loss", "title": "Messwertausfall", "subtitle": "Verhalten, wenn Messwerte veralten oder die Quelle offline ist.", "fields": [
            _setting_field("stale_mode", "Bei veralteten Messwerten", "select", stale_loss.get("mode", "hold_current"), options=mode_options, layout={"width": "third"}),
            _setting_field("stale_fallback_profile", "Fallback-Profil", "select", stale_loss.get("fallback_profile", "p1"), options=profile_options, layout={"width": "third"}),
            _setting_field("stale_hold_seconds", "Halten für", "number", stale_loss.get("hold_seconds", 0), min=0, step=1, unit="s", help="0 bedeutet halten bis Messwerte zurückkommen.", layout={"width": "third"}),
            _setting_field("offline_mode", "Bei Quelle offline", "select", offline_loss.get("mode", "off_all"), options=mode_options, layout={"width": "third"}),
            _setting_field("offline_fallback_profile", "Fallback-Profil", "select", offline_loss.get("fallback_profile", "p1"), options=profile_options, layout={"width": "third"}),
            _setting_field("offline_hold_seconds", "Halten für", "number", offline_loss.get("hold_seconds", 0), min=0, step=1, unit="s", help="0 bedeutet halten bis die Quelle zurückkommt.", layout={"width": "third"}),
        ]},
        {"id": "datalogger", "title": "Data Logger", "subtitle": "Lokale Messwertaufzeichnung als Grundlage für Charts und spätere Portal-Synchronisierung.", "fields": [
            _setting_field("datalogger_enabled", "Data Logging aktivieren", "checkbox", datalogger_cfg.get("enabled", True), help="Speichert lokale Zeitreihen in data/history.sqlite. Kann auf sehr schwachen Systemen deaktiviert werden.", layout={"width": "full"}),
            _setting_field("datalogger_interval_seconds", "Aufzeichnungsintervall", "select", datalogger_cfg.get("interval_seconds", 10), options=_settings_select_options((("10", "10 Sekunden"), ("30", "30 Sekunden"), ("60", "60 Sekunden"))), layout={"width": "half"}),
            _setting_field("datalogger_retention_days", "Aufbewahrung", "number", datalogger_cfg.get("retention_days", 7), min=1, max=30, step=1, unit="Tage", help="Maximal 30 Tage. Standard: 7 Tage.", layout={"width": "half"}),
        ]},
    ]}


def _apply_settings_payload(payload: dict[str, Any]) -> None:
    state.config.setdefault("system", {})
    state.config.setdefault("app", {})
    state.config.setdefault("control", {})
    control = state.config["control"]
    state.config["system"]["instance_name"] = str(payload.get("instance_name") or "PV2Hash Node").strip() or "PV2Hash Node"
    state.config["app"]["refresh_seconds"] = _safe_int(payload.get("refresh_seconds", 5), 5)
    control["policy_mode"] = str(payload.get("policy_mode") or "coarse").strip() or "coarse"
    control["distribution_mode"] = str(payload.get("distribution_mode") or "equal").strip() or "equal"
    control["switch_hysteresis_w"] = _safe_int(payload.get("switch_hysteresis_w", 100), 100)
    control["min_switch_interval_seconds"] = _safe_int(payload.get("min_switch_interval_seconds", 60), 60)
    control["max_import_w"] = max(0, _safe_int(payload.get("max_import_w", 200), 200))
    control["import_hold_seconds"] = _safe_int(payload.get("import_hold_seconds", 15), 15)
    control.setdefault("source_loss", {})
    control["source_loss"]["stale"] = {"mode": str(payload.get("stale_mode") or "hold_current").strip() or "hold_current", "fallback_profile": _normalize_fallback_profile(payload.get("stale_fallback_profile", "p1")), "hold_seconds": _safe_int(payload.get("stale_hold_seconds", 0), 0)}
    control["source_loss"]["offline"] = {"mode": str(payload.get("offline_mode") or "off_all").strip() or "off_all", "fallback_profile": _normalize_fallback_profile(payload.get("offline_fallback_profile", "p1")), "hold_seconds": _safe_int(payload.get("offline_hold_seconds", 0), 0)}
    state.config["datalogger"] = normalize_datalogger_config({
        "enabled": bool(payload.get("datalogger_enabled", False)),
        "interval_seconds": _safe_int(payload.get("datalogger_interval_seconds", 10), 10),
        "retention_days": _safe_int(payload.get("datalogger_retention_days", 7), 7),
    })


def _core_identity_basic_fields() -> list[dict]:
    fields = []
    for field in _core_identity_schema():
        if field.create_phase == "basic":
            initial = field.preset if field.preset is not None else field.default
            fields.append(_render_field(field, initial))
    return fields


def _core_identity_full_fields(miner_cfg: dict) -> list[dict]:
    return [_render_field(field, _field_value_from_config(field, miner_cfg)) for field in _core_identity_schema()]


def _core_control_full_fields(miner_cfg: dict) -> list[dict]:
    driver = miner_cfg.get("driver")
    return [_render_field(field, _field_value_from_config(field, miner_cfg)) for field in _core_control_schema(driver)]


def _build_driver_catalog() -> list[dict]:
    catalog = []
    for driver_key in ("simulator", "braiins", "whatsminer_api3", "axeos"):
        catalog.append({
            "key": driver_key,
            "label": _resolve_miner_driver_label(driver_key),
            "supports_gui_schema": _driver_supports_gui_schema(driver_key),
            "basic_fields": _driver_basic_fields(driver_key),
        })
    return catalog


def _miner_card_summary(miner_cfg: dict, runtime: dict) -> dict:
    reachable = runtime.get("reachable") if runtime else None
    runtime_state = runtime.get("runtime_state") if runtime else None
    return {
        "monitor_enabled": bool(miner_cfg.get("monitor_enabled", True)),
        "control_enabled": bool(miner_cfg.get("control_enabled", True)),
        "connection_ok": True if reachable is True else False if reachable is False else None,
        "runtime_state": runtime_state or "unknown",
        "priority": miner_cfg.get("priority", 100),
        "power_w": runtime.get("power_w") if runtime else None,
        "profile": runtime.get("profile") if runtime else None,
    }


def _safe_int(value, default: int) -> int:
    try:
        if value is None:
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _format_local_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    try:
        return value.astimezone().strftime("%H:%M:%S")
    except Exception:
        return None


def _format_controller_summary(summary: str | None) -> str:
    if not summary:
        return "—"

    text = str(summary).replace("_", " ")
    for mode in ("cascade", "equal"):
        text = text.replace(f" ({mode}, ", " (")
        text = text.replace(f" ({mode})", "")
    return text


def _format_hashrate_text(current_hashrate_ghs: float | None) -> str:
    if current_hashrate_ghs is None:
        return "—"

    try:
        terahash_per_second = float(current_hashrate_ghs) / 1000.0
    except Exception:
        return "—"

    if terahash_per_second >= 100:
        return f"{terahash_per_second:.0f} TH/s"
    if terahash_per_second >= 10:
        return f"{terahash_per_second:.1f} TH/s"
    return f"{terahash_per_second:.2f} TH/s"


def _build_miner_gui_url(host: str | None) -> str | None:
    value = str(host or "").strip()
    if not value:
        return None
    if "://" in value:
        return value
    return f"http://{value}"


def _build_dashboard_miner_rows() -> list[dict]:
    rows: list[dict] = []
    runtime_map = {miner.id: miner for miner in state.miners}
    config_miners = state.config.get("miners", []) if state.config else []

    def _sort_key(miner_cfg: dict) -> tuple[int, int, str]:
        return (
            0 if bool(miner_cfg.get("control_enabled", True)) else 1,
            int(miner_cfg.get("priority", 100)),
            str(miner_cfg.get("name", "")).lower(),
        )

    for miner_cfg in sorted(config_miners, key=_sort_key):
        runtime_miner = runtime_map.get(miner_cfg.get("id"))
        monitor_enabled = bool(miner_cfg.get("monitor_enabled", True))
        control_enabled = bool(miner_cfg.get("control_enabled", True))
        runtime_state_normalized = str(
            getattr(runtime_miner, "runtime_state", "unknown") or "unknown"
        ).lower()
        is_running = runtime_state_normalized not in {"paused", "stopped", "off", "idle", "unknown", "unreachable"}

        host = getattr(runtime_miner, "host", None) or str(miner_cfg.get("host", "") or "")
        name = getattr(runtime_miner, "name", None) or str(miner_cfg.get("name", "") or "")
        priority = int(getattr(runtime_miner, "priority", miner_cfg.get("priority", 100)) or miner_cfg.get("priority", 100))

        if runtime_miner is not None:
            profile = str(runtime_miner.profile or "off")
            power_text = f"{float(runtime_miner.power_w):.0f} W"
            hashrate_text = _format_hashrate_text(getattr(runtime_miner, "current_hashrate_ghs", None))
            reachable = bool(runtime_miner.reachable)
        else:
            profile = "off"
            power_text = "0 W"
            hashrate_text = "—"
            reachable = False

        rows.append(
            {
                "id": str(miner_cfg.get("id", "")),
                "uuid": _device_uuid(miner_cfg),
                "name": name,
                "priority": priority,
                "host": host,
                "host_gui_url": _build_miner_gui_url(host),
                "profile": profile,
                "power_text": power_text,
                "hashrate_text": hashrate_text,
                "monitor_enabled": monitor_enabled,
                "control_enabled": control_enabled,
                "reachable": reachable,
                "is_running": is_running,
                "action_label": "Aus Regelung nehmen" if control_enabled else "In Regelung aufnehmen",
            }
        )
    return rows


def _build_controller_status() -> dict:
    control_config = state.config.get("control", {}) if state.config else {}
    min_switch_interval = max(0.0, float(control_config.get("min_switch_interval_seconds", 0) or 0))
    last_switch_at = state.last_profile_switch_at
    last_switch_monotonic = state.last_profile_switch_monotonic

    progress = 1.0
    ring_state = "ready"
    inner_text = "frei"
    hint_text = "Nächste Umschaltung möglich"

    if min_switch_interval <= 0:
        ring_state = "disabled"
        inner_text = "aus"
        hint_text = "Mindest-Schaltintervall aus"
    elif last_switch_monotonic is None:
        inner_text = "bereit"
        hint_text = "Noch keine Umschaltung"
    else:
        elapsed = max(0.0, monotonic() - last_switch_monotonic)
        progress = max(0.0, min(1.0, elapsed / min_switch_interval))
        remaining = max(0.0, min_switch_interval - elapsed)

        if remaining > 0.05:
            remaining_seconds = max(1, int(remaining + 0.999))
            ring_state = "waiting"
            inner_text = f"{remaining_seconds}s"
            hint_text = f"Nächste Umschaltung in {remaining_seconds}s"
        else:
            ring_state = "ready"
            inner_text = "frei"
            hint_text = "Nächste Umschaltung möglich"

    return {
        "summary_text": _format_controller_summary(state.last_decision),
        "last_switch_at_text": _format_local_time(last_switch_at),
        "switch_ring_state": ring_state,
        "switch_progress": round(progress, 4),
        "switch_inner_text": inner_text,
        "switch_hint_text": hint_text,
    }


def _update_runner_snapshot(update_status: dict | None = None) -> dict:
    return self_update_manager.snapshot(update_status=update_status)


def _update_runner_start_latest(update_status: dict) -> tuple[dict, int]:
    return self_update_manager.start_latest(update_status=update_status)


def _update_progress_value(runner_status: dict | None) -> int | None:
    status = str((runner_status or {}).get("status") or "idle")
    if status == "starting":
        return 10
    if status == "running":
        return 65
    if status == "success":
        return 100
    if status == "error":
        return 100
    return None


def _build_system_update_model() -> dict[str, Any]:
    update_status = update_checker.snapshot()
    runner_status = _update_runner_snapshot(update_status)
    asset_size = update_status.get("release_asset_size_bytes")

    return {
        "update_status": update_status,
        "runner_status": {
            **runner_status,
            "progress_percent": _update_progress_value(runner_status),
        },
        "release_details": {
            "name": update_status.get("release_name"),
            "tag": update_status.get("release_tag"),
            "url": update_status.get("release_url"),
            "published_at": update_status.get("release_published_at"),
            "body": update_status.get("release_body"),
            "asset_name": update_status.get("release_asset_name"),
            "asset_size_bytes": asset_size,
            "asset_size_text": _format_bytes(asset_size),
            "asset_count": update_status.get("release_asset_count"),
        },
    }


def _system_row(label: str, value: Any, *, key: str | None = None) -> dict[str, Any]:
    return {
        "key": key or label.lower().replace(" ", "_"),
        "label": label,
        "value": value if value not in (None, "") else "—",
    }



def _build_system_datalogger_rows() -> list[dict[str, Any]]:
    try:
        status = data_logger.status()
    except Exception as exc:
        return [
            _system_row("Status", "Fehler", key="datalogger_enabled"),
            _system_row("Fehler", str(exc), key="datalogger_error"),
        ]

    enabled = bool(status.get("enabled"))
    interval = status.get("interval_seconds")
    retention = status.get("retention_days")
    sample_count = int(status.get("sample_count") or 0)
    database_size = _format_bytes(status.get("database_size_bytes") or 0)
    latest = status.get("newest_sample_at") or status.get("last_sample_at")

    return [
        _system_row("Aktiv", "Ja" if enabled else "Nein", key="datalogger_enabled"),
        _system_row("Intervall", f"{interval} s" if interval else "—", key="datalogger_interval"),
        _system_row("Aufbewahrung", f"{retention} Tage" if retention else "—", key="datalogger_retention"),
        _system_row("Samples", f"{sample_count:,}".replace(",", "."), key="datalogger_samples"),
        _system_row("DB Size", database_size, key="datalogger_db_size"),
        _system_row("Letztes Sample", _format_relative_time(latest), key="datalogger_latest"),
    ]


def _build_system_model() -> dict[str, Any]:
    host_status = _get_host_status()
    source_type = state.config.get("source", {}).get("type", "unknown")
    battery_type = state.config.get("battery", {}).get("type")
    storage = host_status.get("storage", {}) or {}
    storage_text = storage.get("text") or "—"
    if storage.get("percent_text") and storage.get("percent_text") != "—":
        storage_text = f"{storage_text} · {storage.get('percent_text')}"

    ram_text = "—"
    if host_status.get("ram_text") and host_status.get("ram_text") != "—":
        ram_text = host_status.get("ram_text")
        if host_status.get("ram_percent_text") and host_status.get("ram_percent_text") != "—":
            ram_text = f"{ram_text} ({host_status.get('ram_percent_text')})"

    return {
        "instance_name": state.config["system"].get("instance_name", "PV2Hash Node"),
        "app_version_full": APP_VERSION_FULL,
        "cards": [
            {
                "id": "backup",
                "title": "Sicherung & Wiederherstellung",
                "description": "Komplette PV2Hash-Konfiguration als JSON sichern oder wiederherstellen.",
                "type": "backup",
                "export_url": "/system/config/export",
            },
            {
                "id": "update",
                "title": "Updates",
                "type": "update",
                "model": _build_system_update_model(),
            },
            {
                "id": "instance",
                "title": "Instanz",
                "type": "details",
                "rows": [
                    _system_row("Instanz", state.config["system"].get("instance_name", "PV2Hash Node"), key="instance_name"),
                    _system_row("Instanz-ID", instance_identity.id, key="instance_id"),
                    _system_row("Erstellt", instance_identity.created_at, key="instance_created_at"),
                    _system_row("Messung", _resolve_measurement_profile_label(source_type), key="source_profile"),
                    _system_row("Batterie", _resolve_battery_profile_label(battery_type), key="battery_profile"),
                    _system_row("Miner aktiv", len(state.miners), key="miner_count"),
                    _system_row("Gestartet", state.started_at, key="started_at"),
                    _system_row("Letzter Reload", state.last_reload_at, key="last_reload_at"),
                ],
                "actions": [
                    {"id": "reload_runtime", "label": "Runtime neu laden", "style": "primary"},
                ],
            },
            {
                "id": "host",
                "title": "Host-System",
                "type": "details",
                "rows": [
                    _system_row("Hostname", host_status.get("hostname"), key="hostname"),
                    _system_row("CPU-Auslastung", host_status.get("cpu_percent_text"), key="cpu"),
                    _system_row("RAM", ram_text, key="ram"),
                    _system_row("Load Average", host_status.get("load_text"), key="load"),
                    _system_row("Uptime", host_status.get("uptime_text"), key="uptime"),
                    _system_row("Plattform", host_status.get("platform_text"), key="platform"),
                    _system_row("Python", host_status.get("python_text"), key="python"),
                    _system_row("Speicher", storage_text, key="storage"),
                ],
            },
            {
                "id": "datalogger",
                "title": "Data Logger",
                "type": "details",
                "rows": _build_system_datalogger_rows(),
            },
            {
                "id": "logging",
                "title": "Live-Konsole",
                "type": "logs",
                "description": "Aktuelle Log-Ausgabe aus dem Ringbuffer der Anwendung",
                "log_level": state.config["system"].get("log_level", "INFO"),
                "allowed_log_levels": ["INFO", "DEBUG"],
                "download_url": "/api/logs/download",
            },
        ],
    }


def _optional_int(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def _normalize_log_level(value: str | None, default: str = "INFO") -> str:
    normalized = str(value or default).strip().upper()
    if normalized not in {"INFO", "DEBUG"}:
        return default
    return normalized


def _resolve_sma_device_label(source_debug: dict | None) -> str | None:
    if not source_debug:
        return None

    explicit = source_debug.get("last_packet_device_name")
    if explicit:
        return str(explicit)

    susy_id = source_debug.get("last_packet_susy_id")
    if susy_id in (None, ""):
        return None

    try:
        susy_id_int = int(susy_id)
    except Exception:
        return f"SMA Gerät (SUSy-ID {susy_id})"

    known = {
        270: "SMA Energy Meter",
        349: "SMA Energy Meter 2.0",
        372: "SMA Home Manager 2.0",
        502: "SMA Energy Meter 2.0",
    }
    return known.get(susy_id_int, f"SMA Gerät (SUSy-ID {susy_id_int})")


def _normalize_sma_serial_number(value) -> str:
    text = str(value or "").strip()
    if text == "":
        return ""
    try:
        return str(int(float(text)))
    except Exception:
        return text


def _build_sma_device_choices(source_cfg: dict | None, source_debug: dict | None) -> tuple[list[dict], str]:
    source_settings = (source_cfg or {}).get("settings", {}) or {}
    selected_serial = _normalize_sma_serial_number(source_settings.get("device_serial_number"))
    raw_devices = []
    if source_debug:
        raw_devices = source_debug.get("seen_devices", []) or []

    choices: list[dict] = []
    seen_serials: set[str] = set()

    for item in raw_devices:
        serial = _normalize_sma_serial_number(item.get("serial_number"))
        if not serial or serial in seen_serials:
            continue
        seen_serials.add(serial)

        device_name = str(item.get("device_name") or "SMA Gerät").strip() or "SMA Gerät"
        sender_ip = str(item.get("sender_ip") or "").strip()
        label = f"{device_name} – S/N {serial}"
        if sender_ip:
            label += f" – {sender_ip}"

        choices.append({
            "serial_number": serial,
            "label": label,
            "sender_ip": sender_ip,
            "device_name": device_name,
            "susy_id": item.get("susy_id"),
        })

    choices.sort(key=lambda item: (str(item.get("device_name") or "").lower(), item["serial_number"]))

    if selected_serial and selected_serial not in seen_serials:
        choices.insert(0, {
            "serial_number": selected_serial,
            "label": f"Aktuell gewählt – S/N {selected_serial} (noch nicht erkannt)",
            "sender_ip": "",
            "device_name": "SMA Gerät",
            "susy_id": None,
        })

    return choices, selected_serial


def _resolve_measurement_profile_label(source_type: str | None) -> str:
    normalized = str(source_type or "simulator").strip().lower()
    labels = {
        "simulator": "Simulierter Netzanschlusspunkt",
        "sma_meter_protocol": "SMA Energy Meter",
    }
    return labels.get(normalized, normalized or "Unbekannt")



def _resolve_battery_profile_label(battery_type: str | None) -> str:
    normalized = str(battery_type or "none").strip().lower()
    labels = {
        "none": "Keine Batterie",
        "battery_modbus": "Modbus TCP Batterie",
    }
    return labels.get(normalized, normalized or "Unbekannt")


def _format_bytes(num_bytes: int | float | None) -> str:
    if num_bytes is None:
        return "—"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def _format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _format_relative_time(value: str | datetime | None) -> str:
    if not value:
        return "—"

    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value).strip()
            if not text:
                return "—"
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age_seconds = max(0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())
        return f"vor {_format_duration(age_seconds)}"
    except Exception:
        return str(value)


def _read_cpu_times() -> tuple[int, int] | None:
    try:
        first_line = Path('/proc/stat').read_text(encoding='utf-8').splitlines()[0]
        parts = first_line.split()
        values = [int(item) for item in parts[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        return total, idle
    except Exception:
        return None


def _read_meminfo() -> tuple[int | None, int | None]:
    try:
        values: dict[str, int] = {}
        for line in Path('/proc/meminfo').read_text(encoding='utf-8').splitlines():
            if ':' not in line:
                continue
            key, raw = line.split(':', 1)
            number = raw.strip().split()[0]
            values[key] = int(number) * 1024
        total = values.get('MemTotal')
        available = values.get('MemAvailable', values.get('MemFree'))
        return total, available
    except Exception:
        return None, None


def _read_uptime_seconds() -> float | None:
    try:
        raw = Path('/proc/uptime').read_text(encoding='utf-8').split()[0]
        return float(raw)
    except Exception:
        return None


def _build_storage_summary() -> dict:
    candidates = [
        Path('/'),
        Path.cwd(),
        CONFIG_PATH.parent.resolve(),
    ]
    seen_devices: set[int] = set()
    total_bytes = 0
    used_bytes = 0

    for target in candidates:
        try:
            resolved = target.resolve()
            stat_result = resolved.stat()
            device_id = int(stat_result.st_dev)
            if device_id in seen_devices:
                continue
            usage = shutil.disk_usage(resolved)
        except Exception:
            continue

        seen_devices.add(device_id)
        total_bytes += usage.total
        used_bytes += usage.used

    percent = (used_bytes / total_bytes * 100.0) if total_bytes else None
    return {
        'used_bytes': used_bytes if total_bytes else None,
        'total_bytes': total_bytes if total_bytes else None,
        'used_text': _format_bytes(used_bytes) if total_bytes else '—',
        'total_text': _format_bytes(total_bytes) if total_bytes else '—',
        'text': f"{_format_bytes(used_bytes)} / {_format_bytes(total_bytes)}" if total_bytes else '—',
        'percent': percent,
        'percent_text': f"{percent:.0f}%" if percent is not None else '—',
    }


def _read_os_pretty_name() -> str | None:
    try:
        for line in Path('/etc/os-release').read_text(encoding='utf-8').splitlines():
            if not line.startswith('PRETTY_NAME='):
                continue
            value = line.split('=', 1)[1].strip().strip('\"')
            return value or None
    except Exception:
        return None
    return None


_HOST_CPU_SAMPLE: tuple[int, int] | None = None
_HOST_STATUS_CACHE: tuple[float, dict] | None = None
_HOST_STORAGE_CACHE: tuple[float, dict] | None = None
_HOST_STATIC_INFO = {
    'hostname': socket.gethostname() or '—',
    'fqdn': '',
    'platform': platform.system(),
    'platform_release': platform.release(),
    'platform_text': f"{platform.system()} {platform.release()}",
    'os': _read_os_pretty_name(),
    'python_version': sys.version.split()[0],
    'python_text': sys.version.split()[0],
}
try:
    _HOST_STATIC_INFO['fqdn'] = socket.getfqdn()
except Exception:
    _HOST_STATIC_INFO['fqdn'] = ''
if _HOST_STATIC_INFO['fqdn'] == _HOST_STATIC_INFO['hostname']:
    _HOST_STATIC_INFO['fqdn'] = ''


def _sample_cpu_percent() -> float | None:
    global _HOST_CPU_SAMPLE
    current = _read_cpu_times()
    if current is None:
        return None
    if _HOST_CPU_SAMPLE is None:
        _HOST_CPU_SAMPLE = current
        return None
    prev_total, prev_idle = _HOST_CPU_SAMPLE
    total, idle = current
    _HOST_CPU_SAMPLE = current
    total_delta = total - prev_total
    idle_delta = idle - prev_idle
    if total_delta <= 0:
        return None
    usage = (1.0 - max(0, idle_delta) / total_delta) * 100.0
    return max(0.0, min(100.0, usage))


def _get_storage_summary_cached() -> dict:
    global _HOST_STORAGE_CACHE
    now = monotonic()
    if _HOST_STORAGE_CACHE is not None:
        cached_at, cached = _HOST_STORAGE_CACHE
        if now - cached_at < 60.0:
            return dict(cached)

    storage = _build_storage_summary()
    _HOST_STORAGE_CACHE = (now, dict(storage))
    return storage


def _get_host_status_uncached() -> dict:
    cpu_percent = _sample_cpu_percent()
    mem_total, mem_available = _read_meminfo()
    mem_used = None
    mem_percent = None
    if mem_total is not None and mem_available is not None:
        mem_used = max(0, mem_total - mem_available)
        if mem_total > 0:
            mem_percent = mem_used / mem_total * 100.0
    try:
        load_values = os.getloadavg()
        load_text = ' / '.join(f"{value:.2f}" for value in load_values)
    except Exception:
        load_text = '—'

    uptime_seconds = _read_uptime_seconds()
    return {
        **_HOST_STATIC_INFO,
        'cpu_percent': cpu_percent,
        'cpu_percent_text': f"{cpu_percent:.0f}%" if cpu_percent is not None else '—',
        'ram_used_bytes': mem_used,
        'ram_total_bytes': mem_total,
        'ram_text': (f"{_format_bytes(mem_used)} / {_format_bytes(mem_total)}" if mem_used is not None and mem_total is not None else '—'),
        'ram_percent': mem_percent,
        'ram_percent_text': f"{mem_percent:.0f}%" if mem_percent is not None else '—',
        'load_text': load_text,
        'uptime_seconds': uptime_seconds,
        'uptime_text': _format_duration(uptime_seconds),
        'storage': _get_storage_summary_cached(),
    }


def _get_host_status() -> dict:
    global _HOST_STATUS_CACHE
    now = monotonic()
    if _HOST_STATUS_CACHE is not None:
        cached_at, cached = _HOST_STATUS_CACHE
        if now - cached_at < 2.0:
            return deepcopy(cached)

    status = _get_host_status_uncached()
    _HOST_STATUS_CACHE = (now, deepcopy(status))
    return status


def _build_snapshot_host_info() -> dict[str, Any]:
    host_status = _get_host_status()
    storage = host_status.get('storage') or {}
    return {
        'hostname': host_status.get('hostname'),
        'fqdn': host_status.get('fqdn'),
        'platform': host_status.get('platform'),
        'platform_release': host_status.get('platform_release'),
        'platform_text': host_status.get('platform_text'),
        'os': host_status.get('os'),
        'python_version': host_status.get('python_version'),
        'uptime_seconds': host_status.get('uptime_seconds'),
        'uptime_text': host_status.get('uptime_text'),
        'cpu_percent': host_status.get('cpu_percent'),
        'cpu_percent_text': host_status.get('cpu_percent_text'),
        'load_text': host_status.get('load_text'),
        'memory_total_bytes': host_status.get('ram_total_bytes'),
        'memory_used_bytes': host_status.get('ram_used_bytes'),
        'memory_percent': host_status.get('ram_percent'),
        'memory_text': host_status.get('ram_text'),
        'memory_percent_text': host_status.get('ram_percent_text'),
        'disk_total_bytes': storage.get('total_bytes'),
        'disk_used_bytes': storage.get('used_bytes'),
        'disk_percent': storage.get('percent'),
        'disk_text': storage.get('text'),
        'disk_percent_text': storage.get('percent_text'),
    }


def _redirect_to_miners(
    *,
    miner_id: str | None = None,
    saved: bool = False,
    error: str | None = None,
) -> RedirectResponse:
    params = []
    if saved:
        params.append("saved=1")
    if miner_id:
        params.append(f"open={quote_plus(str(miner_id))}")
    if error:
        params.append(f"error_message={quote_plus(error)}")
    suffix = f"?{'&'.join(params)}" if params else ''
    return RedirectResponse(url=f"/miners{suffix}", status_code=303)


def _normalize_miner_driver(driver: str | None) -> str:
    normalized = str(driver or "simulator").strip().lower()
    if normalized in {"axeos", "espminer", "esp-miner", "bitaxe"}:
        return "axeos"
    if normalized in {"whatsminer3", "whatsminer_api3"}:
        return "whatsminer_api3"
    return normalized or "simulator"


def _resolve_miner_driver_label(driver: str | None) -> str:
    normalized = _normalize_miner_driver(driver)
    labels = {
        "simulator": "Simulator",
        "braiins": "Braiins OS+",
        "whatsminer_api3": "WhatsMiner (API 3.x)",
        "axeos": "axeOS / ESP-Miner",
    }
    return labels.get(normalized, normalized or "Unbekannt")


def _parse_modbus_value_form(form, prefix: str) -> dict:
    register_type = str(form.get(f"{prefix}_register_type", "holding")).strip().lower()
    if register_type not in MODBUS_REGISTER_TYPES:
        register_type = "holding"

    value_type = str(form.get(f"{prefix}_value_type", "uint16")).strip().lower()
    if value_type not in MODBUS_VALUE_TYPES:
        value_type = "uint16"

    endian = str(form.get(f"{prefix}_endian", "big_endian")).strip().lower()
    if endian not in MODBUS_ENDIAN_TYPES:
        endian = "big_endian"

    return {
        "register_type": register_type,
        "address": _optional_int(form.get(f"{prefix}_address")),
        "value_type": value_type,
        "endian": endian,
        "factor": _safe_float(form.get(f"{prefix}_factor", 1.0), 1.0),
    }


def _merge_battery_snapshot(main_snapshot, battery_snapshot):
    if battery_snapshot is None:
        return main_snapshot

    return replace(
        main_snapshot,
        battery_charge_power_w=(
            battery_snapshot.battery_charge_power_w
            if battery_snapshot.battery_charge_power_w is not None
            else main_snapshot.battery_charge_power_w
        ),
        battery_discharge_power_w=(
            battery_snapshot.battery_discharge_power_w
            if battery_snapshot.battery_discharge_power_w is not None
            else main_snapshot.battery_discharge_power_w
        ),
        battery_soc_pct=(
            battery_snapshot.battery_soc_pct
            if battery_snapshot.battery_soc_pct is not None
            else main_snapshot.battery_soc_pct
        ),
        battery_is_charging=(
            battery_snapshot.battery_is_charging
            if battery_snapshot.battery_is_charging is not None
            else main_snapshot.battery_is_charging
        ),
        battery_is_discharging=(
            battery_snapshot.battery_is_discharging
            if battery_snapshot.battery_is_discharging is not None
            else main_snapshot.battery_is_discharging
        ),
        battery_is_active=(
            battery_snapshot.battery_is_active
            if battery_snapshot.battery_is_active is not None
            else main_snapshot.battery_is_active
        ),
        battery_quality=(
            battery_snapshot.battery_quality
            if battery_snapshot.battery_quality is not None
            else main_snapshot.battery_quality
        ),
        battery_updated_at=(
            battery_snapshot.battery_updated_at
            if battery_snapshot.battery_updated_at is not None
            else main_snapshot.battery_updated_at
        ),
    )


def _runtime_matches(generation: int, source, battery_source, controller, miners: list) -> bool:
    return (
        generation == services.reload_generation
        and source is services.source
        and battery_source is services.battery_source
        and controller is services.controller
        and miners == list(services.miners)
    )


async def _shutdown_retired_miners(miners: list) -> None:
    if not miners:
        return

    for miner in miners:
        try:
            logger.info(
                "Applying off profile to retired/disabled miner: id=%s name=%s host=%s",
                getattr(miner.info, "id", "?"),
                getattr(miner.info, "name", "?"),
                getattr(miner.info, "host", "?"),
            )
            await miner.set_profile("off")
            await miner.get_status()
        except Exception:
            logger.exception(
                "Failed to apply off profile to retired/disabled miner: id=%s",
                getattr(getattr(miner, "info", None), "id", "?"),
            )


def _normalize_profiles(driver: str | None, profiles: dict | None) -> dict:
    _, p1_default, p2_default, p3_default, p4_default = _driver_profile_defaults(driver or "simulator")
    source = profiles if isinstance(profiles, dict) else {}

    def _profile_power(name: str, default_value: int) -> int:
        entry = source.get(name, {}) if isinstance(source.get(name, {}), dict) else {}
        raw = entry.get("power_w", default_value)
        return _safe_int(raw, default_value)

    return {
        "p1": {"power_w": _profile_power("p1", p1_default)},
        "p2": {"power_w": _profile_power("p2", p2_default)},
        "p3": {"power_w": _profile_power("p3", p3_default)},
        "p4": {"power_w": _profile_power("p4", p4_default)},
    }


def _driver_profile_defaults(driver: str) -> tuple[int, int, int, int, int]:
    normalized = _normalize_miner_driver(driver)
    if normalized == "braiins":
        return 50051, 1200, 2200, 3200, 4200
    if normalized == "axeos":
        return 80, 200, 200, 200, 200
    if normalized == "whatsminer_api3":
        return 4433, 1000, 1400, 1800, 2200
    return 4028, 900, 1800, 3000, 4200


def _normalize_min_regulated_profile(value: str | None, default: str = "off") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in EDITABLE_MIN_REGULATED_PROFILE_NAMES:
        return default
    return normalized


def _normalize_fallback_profile(value: str | None, default: str = "p1") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in ALL_PROFILE_NAMES:
        return default
    return normalized


def _normalize_battery_override_profile(value: str | None, default: str = "p1") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in EDITABLE_PROFILE_NAMES:
        return default
    return normalized


def _get_runtime_miner_map() -> dict[str, dict]:
    return {miner.id: asdict(miner) for miner in state.miners}


def _get_runtime_details_map() -> dict[str, dict]:
    details: dict[str, dict] = {}
    for adapter in services.miners:
        try:
            payload = adapter.get_details()
        except Exception:
            payload = {}
        details[adapter.info.id] = payload or {}
    return details


def _get_runtime_device_settings_values_map() -> dict[str, dict]:
    values: dict[str, dict] = {}
    for adapter in services.miners:
        try:
            payload = adapter.get_device_settings_values()
        except Exception:
            payload = {}
        values[adapter.info.id] = payload or {}
    return values


def _get_runtime_adapter_by_id(miner_id: str):
    for adapter in services.miners:
        if getattr(adapter.info, "id", None) == miner_id:
            return adapter
    return None


def _format_miner_summary_for_api(summary: dict) -> dict:
    monitor_enabled = bool(summary.get("monitor_enabled"))
    control_enabled = bool(summary.get("control_enabled"))
    connection_ok = summary.get("connection_ok")
    power_w = summary.get("power_w")
    profile = summary.get("profile")

    if not monitor_enabled:
        connection_text = "Verbindung aus"
        connection_class = "neutral"
    elif connection_ok is True:
        connection_text = "Verbindung OK"
        connection_class = "ok"
    elif connection_ok is False:
        connection_text = "Keine Verbindung"
        connection_class = "bad"
    else:
        connection_text = "Verbindung unbekannt"
        connection_class = "neutral"

    return {
        "control_text": "Regelung" if control_enabled else "Nicht geregelt",
        "control_class": "ok" if control_enabled else "neutral",
        "connection_text": connection_text,
        "connection_class": connection_class,
        "runtime_state_text": f"Status: {summary.get('runtime_state') or 'unknown'}",
        "priority_text": f"Priorität: {summary.get('priority', 100)}",
        "profile_text": f"Profil: {profile}" if profile else "",
        "profile_visible": bool(profile),
        "power_text": f"{float(power_w):.0f} W" if power_w is not None else "",
        "power_visible": power_w is not None,
    }


def _render_miner_details_html(miner_view: dict) -> str:
    template = templates.get_template("partials/miner_details.html")
    return template.render({"miner": miner_view})


def _build_miners_live_payload(open_ids: set[str] | None = None) -> dict:
    open_ids = open_ids or set()
    runtime_map = _get_runtime_miner_map()
    miners_payload: list[dict] = []

    for miner_cfg in state.config.get("miners", []):
        miner_id = str(miner_cfg.get("id") or "")
        merged = deepcopy(miner_cfg)
        runtime = runtime_map.get(miner_id, {})
        merged.setdefault("settings", {})
        merged.setdefault("profiles", {})
        merged.setdefault("min_regulated_profile", "off")
        merged["driver"] = _normalize_miner_driver(merged.get("driver"))
        merged["uuid"] = _device_uuid(merged)
        merged["runtime"] = runtime
        merged["summary"] = _miner_card_summary(merged, runtime)

        item = {
            "id": miner_id,
            "uuid": _device_uuid(miner_cfg),
            "summary": _format_miner_summary_for_api(merged["summary"]),
        }

        if miner_id in open_ids:
            try:
                adapter = _get_runtime_adapter_by_id(miner_id)
                merged["details"] = adapter.get_details() if adapter else {}
            except Exception:
                logger.exception("Failed to refresh miner details for live payload: %s", miner_id)
                merged["details"] = {}
            item["details_html"] = _render_miner_details_html(merged)

        miners_payload.append(item)

    return {"status": "ok", "miners": miners_payload}



def _build_sources_live_payload() -> dict:
    source_debug = services.get_source_debug_info()
    battery_debug = services.get_battery_source_debug_info()
    source_cfg = state.config.get("source", {})
    battery_cfg = state.config.get("battery", {})
    sma_devices, selected_sma_device_serial = _build_sma_device_choices(source_cfg, source_debug)

    source_type = source_cfg.get("type", "simulator")
    battery_type = battery_cfg.get("type", "none")
    snapshot = state.snapshot

    source_device_label = _resolve_sma_device_label(source_debug)
    source_serial = source_debug.get("last_packet_serial_number") if source_debug else None
    source_susy = source_debug.get("last_packet_susy_id") if source_debug else None

    battery_enabled = bool(battery_cfg.get("enabled")) and battery_type != "none"

    return {
        "status": "ok",
        "gui_models": services.get_source_gui_models(),
        "source": {
            "id": _device_uuid(source_cfg),
            "type": source_type,
            "profile_label": _resolve_measurement_profile_label(source_type),
            "device_label": source_device_label or "—",
            "serial_number": str(source_serial or ""),
            "susy_id": str(source_susy or ""),
            "quality": getattr(snapshot, "quality", None) if snapshot else None,
            "grid_power_w": float(snapshot.grid_power_w) if snapshot else None,
            "discovered_devices": sma_devices,
            "selected_device_serial": selected_sma_device_serial,
            "debug": source_debug or {},
        },
        "battery": {
            "id": _device_uuid(battery_cfg),
            "type": battery_type,
            "profile_label": _resolve_battery_profile_label(battery_type),
            "enabled": battery_enabled,
            "status_label": "aktiv" if battery_enabled else "deaktiviert",
            "soc_pct": snapshot.battery_soc_pct if snapshot else None,
            "charge_power_w": snapshot.battery_charge_power_w if snapshot else None,
            "discharge_power_w": snapshot.battery_discharge_power_w if snapshot else None,
            "is_active": bool(snapshot.battery_is_active) if snapshot else False,
            "is_charging": bool(snapshot.battery_is_charging) if snapshot else False,
            "is_discharging": bool(snapshot.battery_is_discharging) if snapshot else False,
            "debug": battery_debug or {},
        },
    }


def _apply_source_config_form(target_config: dict[str, Any], form: Any) -> dict[str, Any]:
    source_type = str(form.get("source_type", "simulator")).strip()

    target_config.setdefault("source", {})
    target_config["source"]["type"] = source_type
    target_config["source"]["name"] = _resolve_measurement_profile_label(source_type)
    target_config["source"]["enabled"] = True
    target_config["source"].setdefault("settings", {})
    target_config["source"]["settings"]["multicast_ip"] = form.get(
        "multicast_ip", "239.12.255.254"
    )
    target_config["source"]["settings"]["bind_port"] = _safe_int(form.get("bind_port", 9522), 9522)
    target_config["source"]["settings"]["interface_ip"] = (
        form.get("interface_ip", "0.0.0.0").strip() or "0.0.0.0"
    )
    target_config["source"]["settings"]["packet_timeout_seconds"] = _safe_float(
        form.get("packet_timeout_seconds", 1.0), 1.0
    )
    target_config["source"]["settings"]["stale_after_seconds"] = _safe_float(
        form.get("stale_after_seconds", 8.0), 8.0
    )
    target_config["source"]["settings"]["offline_after_seconds"] = _safe_float(
        form.get("offline_after_seconds", 30.0), 30.0
    )
    selected_device_serial = _normalize_sma_serial_number(form.get("device_serial_number", ""))
    target_config["source"]["settings"]["device_serial_number"] = selected_device_serial
    target_config["source"]["settings"].pop("device_ip", None)
    target_config["source"]["settings"]["debug_dump_obis"] = form.get("debug_dump_obis") == "on"
    target_config["source"]["settings"]["simulator_import_power_w"] = _safe_float(
        form.get("simulator_import_power_w", 1000.0), 1000.0
    )
    target_config["source"]["settings"]["simulator_export_power_w"] = _safe_float(
        form.get("simulator_export_power_w", 10000.0), 10000.0
    )
    target_config["source"]["settings"]["simulator_ramp_rate_w_per_minute"] = _safe_float(
        form.get("simulator_ramp_rate_w_per_minute", 600.0), 600.0
    )

    battery_type = str(form.get("battery_type", "none")).strip()
    battery_enabled = (form.get("battery_enabled") == "on") and battery_type != "none"

    target_config.setdefault("battery", {})
    target_config["battery"]["type"] = battery_type
    target_config["battery"]["name"] = _resolve_battery_profile_label(battery_type)
    target_config["battery"]["enabled"] = battery_enabled
    target_config["battery"].setdefault("settings", {})
    target_config["battery"]["settings"]["modbus_profile"] = str(form.get("battery_modbus_profile", "")).strip()
    target_config["battery"]["settings"]["host"] = str(form.get("battery_host", "")).strip()
    target_config["battery"]["settings"]["port"] = _safe_int(form.get("battery_port", 502), 502)
    target_config["battery"]["settings"]["unit_id"] = _safe_int(form.get("battery_unit_id", 1), 1)
    target_config["battery"]["settings"]["poll_interval_ms"] = _safe_int(
        form.get("battery_poll_interval_ms", 1000), 1000
    )
    target_config["battery"]["settings"]["request_timeout_seconds"] = _safe_float(
        form.get("battery_request_timeout_seconds", 1.0), 1.0
    )
    target_config["battery"]["settings"]["soc"] = _parse_modbus_value_form(form, "battery_soc")
    target_config["battery"]["settings"]["charge_power"] = _parse_modbus_value_form(form, "battery_charge_power")
    target_config["battery"]["settings"]["discharge_power"] = _parse_modbus_value_form(form, "battery_discharge_power")
    target_config["battery"]["settings"]["voltage"] = _parse_modbus_value_form(form, "battery_voltage")
    target_config["battery"]["settings"]["current"] = _parse_modbus_value_form(form, "battery_current")
    target_config["battery"]["settings"]["soh"] = _parse_modbus_value_form(form, "battery_soh")
    target_config["battery"]["settings"]["temperature"] = _parse_modbus_value_form(form, "battery_temperature")
    target_config["battery"]["settings"]["capacity"] = _parse_modbus_value_form(form, "battery_capacity")
    target_config["battery"]["settings"]["max_charge_current"] = _parse_modbus_value_form(form, "battery_max_charge_current")
    target_config["battery"]["settings"]["max_discharge_current"] = _parse_modbus_value_form(form, "battery_max_discharge_current")

    return target_config


def _save_source_config_from_form(form: Any) -> dict[str, Any]:
    next_config = _apply_source_config_form(deepcopy(state.config), form)
    source_type = str(next_config.get("source", {}).get("type", "simulator")).strip()
    selected_device_serial = _normalize_sma_serial_number(
        next_config.get("source", {}).get("settings", {}).get("device_serial_number", "")
    )
    if source_type == "sma_meter_protocol" and not selected_device_serial:
        return {"status": "error", "message": "Bitte ein SMA-Gerät bzw. eine Seriennummer auswählen."}

    state.config = next_config
    save_config(state.config)
    logger.info(
        "Source settings saved: type=%s battery_type=%s battery_enabled=%s",
        source_type,
        state.config.get("battery", {}).get("type"),
        bool(state.config.get("battery", {}).get("enabled")),
    )
    reload_runtime()
    payload = _build_sources_live_payload()
    payload["message"] = "Messungen gespeichert."
    return payload


def _build_dashboard_live_payload() -> dict:
    snapshot = state.snapshot
    source_debug = services.get_source_debug_info()
    host_status = _get_host_status()
    controller_status = _build_controller_status()
    dashboard_miners = _build_dashboard_miner_rows()

    total_miner_power = sum(m.power_w for m in state.miners) if state.miners else 0.0

    config_miners = state.config.get("miners", []) if state.config else []
    active_count = sum(1 for miner in config_miners if bool(miner.get("control_enabled", True)))
    max_p4_power = 0.0
    for miner in config_miners:
        try:
            max_p4_power += float(miner.get("profiles", {}).get("p4", {}).get("power_w", 0.0) or 0.0)
        except Exception:
            pass

    if snapshot:
        grid_power_w = float(snapshot.grid_power_w)
        grid_value = f"{grid_power_w:.0f} W"
        grid_class = "good" if grid_power_w < 0 else "warn"
        source_label = _resolve_sma_device_label(source_debug) or snapshot.source or "—"
        source_quality = f"Qualität: {snapshot.quality}"
    else:
        grid_value = "—"
        grid_class = ""
        source_label = _resolve_sma_device_label(source_debug) or "—"
        source_quality = "Qualität: —"

    source_serial = source_debug.get("last_packet_serial_number") if source_debug else None
    source_susy = source_debug.get("last_packet_susy_id") if source_debug else None
    if source_serial:
        source_meta = f"SN: {source_serial}" + (f" · SUSy-ID: {source_susy}" if source_susy else "")
    elif source_susy:
        source_meta = f"SUSy-ID: {source_susy}"
    else:
        source_meta = ""

    battery_soc = snapshot.battery_soc_pct if snapshot and snapshot.battery_soc_pct is not None else None
    battery_charge_power = snapshot.battery_charge_power_w if snapshot and snapshot.battery_charge_power_w is not None else None
    battery_discharge_power = snapshot.battery_discharge_power_w if snapshot and snapshot.battery_discharge_power_w is not None else None
    battery_class = ""
    battery_state = "Batterie inaktiv"
    if snapshot and snapshot.battery_is_charging and battery_charge_power is not None:
        battery_class = "good"
        battery_state = f"Batterie wird geladen ({battery_charge_power:.0f} W)"
    elif snapshot and snapshot.battery_is_discharging and battery_discharge_power is not None:
        battery_class = "warn"
        battery_state = f"Batterie wird entladen ({battery_discharge_power:.0f} W)"
    elif snapshot and snapshot.battery_is_active:
        battery_state = "Batterie aktiv"
    elif battery_soc is None:
        battery_state = "Keine Batteriedaten verfügbar"

    return {
        "status": "ok",
        "cards": {
            "grid": {
                "value": grid_value,
                "class": grid_class,
                "hint": "negativ = Einspeisung",
            },
            "source": {
                "label": source_label,
                "meta": source_meta,
                "quality": source_quality,
            },
            "miners": {
                "power": f"{total_miner_power:.0f} W",
                "meta": f"{active_count} Miner aktiv (max. {max_p4_power:.0f} W)",
            },
            "battery": {
                "value": f"{battery_soc:.1f} %" if battery_soc is not None else "—",
                "class": battery_class,
                "state": battery_state,
            },
            "host": {
                "cpu": f"CPU {host_status.get('cpu_percent_text', '—')}",
                "ram": "RAM "
                + str(host_status.get("ram_text", "—"))
                + (
                    f" ({host_status.get('ram_percent_text')})"
                    if host_status.get("ram_percent_text") and host_status.get("ram_percent_text") != "—"
                    else ""
                ),
            },
            "controller": {
                "policy_mode": state.config.get("control", {}).get("policy_mode", "—"),
                "distribution_mode": state.config.get("control", {}).get("distribution_mode", "—"),
                "summary": controller_status.get("summary_text", "—"),
                "last_switch": f"Letzte Umschaltung: {controller_status.get('last_switch_at_text') or '—'}",
                "ring_state": controller_status.get("switch_ring_state", ""),
                "ring_progress": controller_status.get("switch_progress", 1),
                "ring_inner": controller_status.get("switch_inner_text", "—"),
                "ring_hint": controller_status.get("switch_hint_text", ""),
            },
        },
        "miners": dashboard_miners,
    }


def _build_miners_view() -> list[dict]:
    runtime_map = _get_runtime_miner_map()
    details_map = _get_runtime_details_map()
    device_settings_values_map = _get_runtime_device_settings_values_map()
    miner_views: list[dict] = []

    for miner_cfg in state.config.get("miners", []):
        merged = deepcopy(miner_cfg)
        runtime = runtime_map.get(miner_cfg.get("id"), {})
        merged.setdefault("settings", {})
        merged.setdefault("profiles", {})
        merged.setdefault("min_regulated_profile", "off")
        merged["driver"] = _normalize_miner_driver(merged.get("driver"))
        merged["uuid"] = _device_uuid(merged)
        merged["runtime"] = runtime
        merged["summary"] = _miner_card_summary(merged, runtime)
        merged["supports_gui_schema"] = _driver_supports_gui_schema(merged.get("driver"))
        merged["identity_fields"] = _core_identity_full_fields(merged)
        merged["control_fields"] = _core_control_full_fields(merged)
        merged["driver_fields"] = _driver_full_fields(merged.get("driver"), merged)
        merged["device_settings_fields"] = _driver_device_settings_fields(
            merged.get("driver"),
            device_settings_values_map.get(miner_cfg.get("id"), {}),
        )
        merged["action_fields"] = _driver_action_fields(merged.get("driver"))
        merged["details"] = details_map.get(miner_cfg.get("id"), {})
        miner_views.append(merged)

    return miner_views


def _miners_context(request: Request, *, error_message: str | None = None) -> dict:
    return {
        "request": request,
        "miners": _build_miners_view(),
        "saved": request.query_params.get("saved") == "1",
        "open_miner_id": request.query_params.get("open"),
        "error_message": error_message or request.query_params.get("error_message"),
        "driver_catalog": _build_driver_catalog(),
        "core_identity_basic_fields": _core_identity_basic_fields(),
        "refresh_seconds": _safe_int(state.config.get("app", {}).get("refresh_seconds", 5), 5),
        "source_gui_models": services.get_source_gui_models(),
        "wiki_links": {
            "overview": "https://github.com/phlupp/pv2hash/wiki/Miner",
            "profiles": "https://github.com/phlupp/pv2hash/wiki/Leistungsprofile",
            "battery": "https://github.com/phlupp/pv2hash/wiki/Batterieverhalten",
            "simulator": "https://github.com/phlupp/pv2hash/wiki/Simulator-Miner",
            "braiins": "https://github.com/phlupp/pv2hash/wiki/Braiins-OS%2B",
            "whatsminer_api3": "https://github.com/phlupp/pv2hash/wiki/WhatsMiner-API3",
            "axeos": "https://github.com/phlupp/pv2hash/wiki/axeOS-ESP-Miner",
        },
    }


def _build_miner_settings(
    form,
    driver: str,
    default_port: int,
    existing: dict | None = None,
) -> dict:
    existing = existing or {}
    driver = _normalize_miner_driver(driver)

    settings = {
        "port": int(form.get("port", existing.get("port", default_port))),
    }

    if driver == "braiins":
        username = str(form.get("username", existing.get("username") or "")).strip()
        password_raw = str(form.get("password", "")).strip()

        if username:
            settings["username"] = username
        elif existing.get("username"):
            settings["username"] = existing["username"]

        if password_raw:
            settings["password"] = password_raw
        elif existing.get("password"):
            settings["password"] = existing["password"]

    return settings



def _parse_profile_values(form, driver: str) -> dict[str, dict[str, float]]:
    _, default_p1, default_p2, default_p3, default_p4 = _driver_profile_defaults(driver)

    return {
        "p1": {"power_w": _safe_int(form.get("profile_p1_power_w", default_p1), default_p1)},
        "p2": {"power_w": _safe_int(form.get("profile_p2_power_w", default_p2), default_p2)},
        "p3": {"power_w": _safe_int(form.get("profile_p3_power_w", default_p3), default_p3)},
        "p4": {"power_w": _safe_int(form.get("profile_p4_power_w", default_p4), default_p4)},
    }

def _validate_profile_values(
    *,
    profile_values: dict[str, dict[str, float]],
    runtime_constraints: dict | None = None,
) -> str | None:
    values = {name: float(profile_values[name]["power_w"]) for name in EDITABLE_PROFILE_NAMES}

    for name, value in values.items():
        if value <= 0:
            return f"Profil {name} muss größer als 0 W sein."

    if not (values["p1"] <= values["p2"] <= values["p3"] <= values["p4"]):
        return "Profile müssen in aufsteigender Reihenfolge liegen: p1 ≤ p2 ≤ p3 ≤ p4."

    if runtime_constraints:
        min_power = runtime_constraints.get("power_target_min_w")
        max_power = runtime_constraints.get("power_target_max_w")

        if min_power is not None:
            for name in EDITABLE_PROFILE_NAMES:
                if values[name] < float(min_power):
                    return (
                        f"{name} liegt unter dem vom Miner gemeldeten Minimum "
                        f"von {float(min_power):.0f} W."
                    )

        if max_power is not None:
            for name in EDITABLE_PROFILE_NAMES:
                if values[name] > float(max_power):
                    return (
                        f"{name} liegt über dem vom Miner gemeldeten Maximum "
                        f"von {float(max_power):.0f} W."
                    )

    return None


async def control_loop() -> None:
    logger.info("Control loop started")

    while True:
        refresh_seconds = state.config["app"].get("refresh_seconds", 5)

        try:
            generation = services.reload_generation
            source = services.source
            battery_source = services.battery_source
            controller = services.controller
            miners = list(services.miners)
            distribution_mode = state.config["control"].get("distribution_mode", "equal")

            current_total_miner_power_w = (
                sum(m.power_w for m in state.miners) if state.miners else 0.0
            )

            if hasattr(source, "set_simulated_miner_power_w"):
                source.set_simulated_miner_power_w(current_total_miner_power_w)

            snapshot = await source.read()
            battery_snapshot = None
            if battery_source is not None:
                battery_snapshot = await battery_source.read()
                snapshot = _merge_battery_snapshot(snapshot, battery_snapshot)

            if not _runtime_matches(generation, source, battery_source, controller, miners):
                logger.info("Discarding stale control iteration after runtime reload")
                await asyncio.sleep(0.1)
                continue

            state.last_live_packet_at = getattr(source, "last_live_packet_at", None)

            aborted = False
            for miner in miners:
                if not _runtime_matches(generation, source, battery_source, controller, miners):
                    logger.info("Stopping stale pre-decision status refresh after runtime reload")
                    aborted = True
                    break
                await miner.get_status()

            if aborted or not _runtime_matches(generation, source, battery_source, controller, miners):
                logger.info("Discarding control iteration after pre-decision status refresh")
                await asyncio.sleep(0.1)
                continue

            decision = controller.decide(
                snapshot=snapshot,
                miners=miners,
                distribution_mode=distribution_mode,
            )

            if not _runtime_matches(generation, source, battery_source, controller, miners):
                logger.info("Discarding stale control decision after runtime reload")
                await asyncio.sleep(0.1)
                continue

            profile_switch_requested = len(miners) == len(decision.profiles) and any(
                miner.is_active_for_distribution()
                and getattr(miner.info, "profile", None) != profile
                for miner, profile in zip(miners, decision.profiles)
            )

            for miner, profile in zip(miners, decision.profiles):
                if not _runtime_matches(generation, source, battery_source, controller, miners):
                    logger.info("Stopping stale profile apply after runtime reload")
                    aborted = True
                    break
                if not miner.is_active_for_distribution():
                    continue
                await miner.set_profile(profile)

            if aborted:
                await asyncio.sleep(0.1)
                continue

            miner_states = []
            for miner in miners:
                if not _runtime_matches(generation, source, battery_source, controller, miners):
                    logger.info("Stopping stale status refresh after runtime reload")
                    aborted = True
                    break
                miner_states.append(await miner.get_status())

            if aborted or not _runtime_matches(generation, source, battery_source, controller, miners):
                logger.info("Discarding state update after runtime reload")
                await asyncio.sleep(0.1)
                continue

            socket_states = []
            for socket_adapter in list(services.sockets):
                try:
                    socket_states.append(socket_adapter.get_status())
                except Exception:
                    logger.exception("Failed to refresh socket status: %s", getattr(getattr(socket_adapter, "info", None), "id", "unknown"))

            state.snapshot = snapshot
            state.miners = miner_states
            state.sockets = socket_states
            state.last_decision = decision.summary
            state.last_decision_at = datetime.now(UTC)
            if profile_switch_requested:
                state.last_profile_switch_at = state.last_decision_at
                state.last_profile_switch_monotonic = monotonic()

        except Exception:
            logger.exception("Unhandled error in control loop")

        await asyncio.sleep(refresh_seconds)


@app.on_event("startup")
async def startup_event() -> None:
    logger.info("Application startup complete")
    asyncio.create_task(control_loop())
    asyncio.create_task(update_checker.run_background_loop())
    asyncio.create_task(data_logger.run())


def reload_runtime() -> None:
    logger.info("Reloading runtime")
    services.reload_from_config()

    retired_miners = services.pop_retired_miners()
    if retired_miners:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "Retired/disabled miners could not be shut down immediately because no event loop is running"
            )
        else:
            loop.create_task(_shutdown_retired_miners(retired_miners))

    state.snapshot = None
    state.miners = []
    state.sockets = []
    state.last_decision = None
    state.last_decision_at = None
    state.last_profile_switch_at = None
    state.last_profile_switch_monotonic = None
    state.last_live_packet_at = None
    state.last_reload_at = datetime.now(UTC)



@app.get("/")
async def dashboard(request: Request):
    total_miner_power = sum(m.power_w for m in state.miners) if state.miners else 0.0
    miner_count = len(state.miners)

    source_debug = services.get_source_debug_info()

    context = {
        "request": request,
        "snapshot": state.snapshot,
        "miners": state.miners,
        "total_miner_power": total_miner_power,
        "miner_count": miner_count,
        "policy_mode": state.config["control"]["policy_mode"],
        "distribution_mode": state.config["control"]["distribution_mode"],
        "started_at": state.started_at,
        "instance_name": state.config["system"]["instance_name"],
        "miner_action": request.query_params.get("miner_action", ""),
        "last_decision": state.last_decision,
        "last_decision_at_text": _format_local_time(state.last_decision_at),
        "controller_status": _build_controller_status(),
        "dashboard_miners": _build_dashboard_miner_rows(),
        "host_status": _get_host_status(),
        "last_reload_at": state.last_reload_at,
        "source_reloaded_at": state.source_reloaded_at,
        "last_live_packet_at": state.last_live_packet_at,
        "source_debug": source_debug,
        "source_device_label": _resolve_sma_device_label(source_debug),
        "source_device_serial_number": source_debug.get("last_packet_serial_number") if source_debug else None,
        "source_device_susy_id": source_debug.get("last_packet_susy_id") if source_debug else None,
        "app_version_full": APP_VERSION_FULL,
        "update_check": update_checker.snapshot(),
        "refresh_seconds": _safe_int(state.config.get("app", {}).get("refresh_seconds", 5), 5),
    }

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=context,
    )


@app.get("/settings")
async def settings_page(request: Request):
    context = {
        "request": request,
        "config": state.config,
        "saved": request.query_params.get("saved") == "1",
    }
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=context,
    )


@app.post("/settings")
async def save_settings(request: Request):
    form = await request.form()

    state.config["system"]["instance_name"] = form.get("instance_name", "PV2Hash Node")
    state.config["app"]["refresh_seconds"] = _safe_int(form.get("refresh_seconds", 5), 5)
    state.config["control"]["policy_mode"] = form.get("policy_mode", "coarse")
    state.config["control"]["distribution_mode"] = form.get("distribution_mode", "equal")
    state.config["control"]["switch_hysteresis_w"] = _safe_int(form.get("switch_hysteresis_w", 100), 100)
    state.config["control"]["min_switch_interval_seconds"] = _safe_int(
        form.get("min_switch_interval_seconds", 60), 60
    )
    state.config["control"]["max_import_w"] = max(0, _safe_int(form.get("max_import_w", 200), 200))
    state.config["control"]["import_hold_seconds"] = _safe_int(form.get("import_hold_seconds", 15), 15)

    state.config["control"].setdefault("source_loss", {})
    state.config["control"]["source_loss"]["stale"] = {
        "mode": form.get("stale_mode", "hold_current"),
        "fallback_profile": _normalize_fallback_profile(
            form.get("stale_fallback_profile", "p1")
        ),
        "hold_seconds": _safe_int(form.get("stale_hold_seconds", 0), 0),
    }
    state.config["control"]["source_loss"]["offline"] = {
        "mode": form.get("offline_mode", "off_all"),
        "fallback_profile": _normalize_fallback_profile(
            form.get("offline_fallback_profile", "p1")
        ),
        "hold_seconds": _safe_int(form.get("offline_hold_seconds", 0), 0),
    }

    save_config(state.config)
    setup_logging(state.config["system"].get("log_level", "INFO"))
    logger.info(
        "Settings saved: instance=%s refresh_seconds=%s policy_mode=%s distribution_mode=%s",
        state.config["system"].get("instance_name", "PV2Hash Node"),
        state.config["app"].get("refresh_seconds", 5),
        state.config["control"].get("policy_mode", "coarse"),
        state.config["control"].get("distribution_mode", "equal"),
    )
    reload_runtime()
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@app.get("/api/settings/model")
async def api_settings_model():
    return JSONResponse(content=jsonable_encoder({"status": "ok", "model": _build_settings_model()}))


@app.post("/api/settings/config")
async def api_settings_config(request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"status": "error", "message": "Ungültige Einstellungen."}, status_code=400)
    _apply_settings_payload(payload)
    save_config(state.config)
    setup_logging(state.config["system"].get("log_level", "INFO"))
    logger.info(
        "Settings saved: instance=%s refresh_seconds=%s policy_mode=%s distribution_mode=%s",
        state.config["system"].get("instance_name", "PV2Hash Node"),
        state.config["app"].get("refresh_seconds", 5),
        state.config["control"].get("policy_mode", "coarse"),
        state.config["control"].get("distribution_mode", "equal"),
    )
    reload_runtime()
    return JSONResponse(content=jsonable_encoder({"status": "ok", "message": "Einstellungen gespeichert.", "instance_name": state.config["system"].get("instance_name", "PV2Hash Node"), "model": _build_settings_model()}))


@app.get("/sources")
async def sources_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context={
            "request": request,
            "refresh_seconds": _safe_int(state.config.get("app", {}).get("refresh_seconds", 5), 5),
        },
    )


@app.post("/sources")
async def save_source(request: Request):
    result = _save_source_config_from_form(await request.form())
    if result.get("status") == "error":
        return RedirectResponse(url="/sources?serial_required=1", status_code=303)
    return RedirectResponse(url="/sources?saved=1", status_code=303)


@app.post("/api/sources/config")
async def api_save_sources_config(request: Request):
    result = _save_source_config_from_form(await request.form())
    status_code = 200 if result.get("status") == "ok" else 400
    return JSONResponse(content=jsonable_encoder(result), status_code=status_code)


@app.get("/api/sources/status")
async def api_sources_status():
    return JSONResponse(content=jsonable_encoder(_build_sources_live_payload()))


@app.get("/api/sources/gui")
async def api_sources_gui():
    return JSONResponse(content=jsonable_encoder({
        "status": "ok",
        "sources": services.get_source_gui_models(),
    }))


@app.post("/api/sources/gui/preview")
async def api_sources_gui_preview(request: Request):
    preview_config = _apply_source_config_form(deepcopy(state.config), await request.form())
    return JSONResponse(content=jsonable_encoder({
        "status": "ok",
        "sources": services.get_source_gui_models(config=preview_config),
    }))


@app.post("/api/sources/action")
async def api_sources_action(request: Request):
    form = await request.form()
    source_id = str(form.get("source_id", "")).strip()
    action_id = str(form.get("action_id", "")).strip()
    preview_config = _apply_source_config_form(deepcopy(state.config), form)

    if source_id == "battery" and action_id == "battery_modbus_apply_profile":
        battery_type = str(preview_config.get("battery", {}).get("type", "")).strip()
        if battery_type != "battery_modbus":
            return JSONResponse(
                content={"status": "error", "message": "Modbus-Profile sind nur für die Modbus TCP Batterie verfügbar."},
                status_code=400,
            )
        profile_id = str(form.get("battery_modbus_profile", "")).strip()
        if not profile_id:
            return JSONResponse(
                content={"status": "error", "message": "Bitte zuerst ein Modbus-Profil auswählen."},
                status_code=400,
            )
        try:
            from pv2hash.sources.battery_modbus_profiles import apply_battery_modbus_profile

            settings = preview_config.setdefault("battery", {}).setdefault("settings", {})
            ok, message = apply_battery_modbus_profile(settings, profile_id)
            return JSONResponse(content=jsonable_encoder({
                "status": "ok" if ok else "error",
                "message": message,
                "sources": services.get_source_gui_models(config=preview_config),
            }))
        except Exception as exc:
            logger.exception("Battery Modbus profile apply failed")
            return JSONResponse(
                content={"status": "error", "message": f"Modbus-Profil konnte nicht angewendet werden: {exc}"},
                status_code=500,
            )

    if source_id != "grid" or action_id != "sma_device_search":
        return JSONResponse(
            content={"status": "error", "message": "Unbekannte Source-Aktion."},
            status_code=400,
        )

    source_type = str(preview_config.get("source", {}).get("type", "")).strip()
    if source_type != "sma_meter_protocol":
        return JSONResponse(
            content={"status": "error", "message": "Geräte-Suche ist nur für SMA Energy Meter verfügbar."},
            status_code=400,
        )

    from pv2hash.factory import build_source

    adapter = None
    try:
        adapter = build_source(preview_config)
        result = await adapter.run_action(action_id, config=preview_config.get("source", {}))
        source_debug = result.get("debug_info") if isinstance(result, dict) else None
        if not source_debug:
            source_debug = getattr(adapter, "debug_info", {}) or {}
        return JSONResponse(content=jsonable_encoder({
            "status": result.get("status", "ok") if isinstance(result, dict) else "ok",
            "message": result.get("message", "Geräte-Suche abgeschlossen.") if isinstance(result, dict) else "Geräte-Suche abgeschlossen.",
            "sources": services.get_source_gui_models(config=preview_config, source_debug_override=source_debug),
        }))
    except Exception as exc:
        logger.exception("SMA device search failed")
        return JSONResponse(
            content={"status": "error", "message": f"Geräte-Suche fehlgeschlagen: {exc}"},
            status_code=500,
        )
    finally:
        close = getattr(adapter, "close", None) if adapter is not None else None
        if callable(close):
            close()


@app.get("/miners")
async def miners_page(request: Request):
    context = _miners_context(request)
    return templates.TemplateResponse(
        request=request,
        name="miners.html",
        context=context,
    )


async def _add_miner_result(form: Any) -> dict[str, Any]:
    miner_id = f"m-{uuid4().hex[:8]}"
    driver = _normalize_miner_driver(form.get("driver", "simulator"))
    if not _driver_supports_gui_schema(driver):
        return {"status": "error", "message": "Dieser Treiber ist noch nicht auf das neue GUI-Schema migriert."}

    miner_cfg = {
        "id": miner_id,
        "uuid": str(uuid4()),
        "name": "Miner",
        "driver": driver,
        "monitor_enabled": True,
        "control_enabled": True,
        "priority": 100,
        "host": "",
        "settings": {},
        "profiles": deepcopy(_normalize_profiles(driver, None)),
        "min_regulated_profile": "off",
        "use_battery_when_charging": False,
        "battery_charge_soc_min": 95,
        "battery_charge_profile": "p1",
        "use_battery_when_discharging": False,
        "battery_discharge_soc_min": 80,
        "battery_discharge_profile": "p1",
    }

    validation_error = None
    for field in _core_identity_schema() + _driver_schema(driver):
        if field.create_phase != "basic":
            continue
        raw = form.get(field.name)
        value = _coerce_field_value(field, raw)
        if field.required and value in (None, ""):
            validation_error = f"Feld '{field.label}' ist erforderlich."
            break
        if value is not None:
            _set_nested_value(miner_cfg, field.name, value)

    if validation_error:
        return {"status": "error", "message": validation_error}

    state.config.setdefault("miners", []).append(miner_cfg)
    save_config(state.config)
    logger.info(
        "Miner added via metadata UI: id=%s name=%s driver=%s host=%s",
        miner_id,
        miner_cfg.get("name"),
        driver,
        miner_cfg.get("host"),
    )
    reload_runtime()
    return {
        "status": "ok",
        "message": "Miner angelegt.",
        "miner_id": miner_id,
    }


@app.post("/api/miners/add")
async def api_add_miner(request: Request):
    result = await _add_miner_result(await request.form())
    status_code = 200 if result.get("status") == "ok" else 400
    return JSONResponse(result, status_code=status_code)


async def _update_miner_config_result(form: Any) -> dict[str, Any]:
    miner_id = str(form.get("miner_id", "")).strip()
    if not miner_id:
        return {"status": "error", "message": "Miner-ID fehlt."}

    runtime_map = _get_runtime_miner_map()

    for miner in state.config.get("miners", []):
        if miner.get("id") != miner_id:
            continue

        driver = _normalize_miner_driver(miner.get("driver", "simulator"))
        if not _driver_supports_gui_schema(driver):
            return {
                "status": "error",
                "message": "Dieser Treiber ist noch nicht auf das neue GUI-Schema migriert.",
                "miner_id": miner_id,
            }

        updated = deepcopy(miner)
        for field in _core_identity_schema() + _core_control_schema(driver) + _driver_schema(driver):
            if field.type == "checkbox":
                raw = form.get(field.name) == "on"
            else:
                raw = form.get(field.name)
            fallback = _field_value_from_config(field, miner)
            value = _coerce_field_value(field, raw, fallback=fallback)
            _set_nested_value(updated, field.name, value)

        if bool(updated.get("control_enabled", True)):
            updated["monitor_enabled"] = True

        validation_error = _validate_profile_values(
            profile_values=updated.get("profiles", {}),
            runtime_constraints=runtime_map.get(miner_id),
        )
        if validation_error:
            return {"status": "error", "message": validation_error, "miner_id": miner_id}

        miner.clear()
        miner.update(updated)
        save_config(state.config)
        reload_runtime()
        logger.info(
            "Miner updated via metadata UI: id=%s name=%s driver=%s host=%s control_enabled=%s",
            miner.get("id"), miner.get("name"), driver, miner.get("host"), miner.get("control_enabled")
        )
        return {
            "status": "ok",
            "message": "Miner-Konfiguration gespeichert.",
            "miner_id": miner_id,
            "control_enabled": bool(miner.get("control_enabled", True)),
            "monitor_enabled": bool(miner.get("monitor_enabled", True)),
        }

    return {"status": "error", "message": "Miner nicht gefunden.", "miner_id": miner_id}


@app.post("/api/miner/{miner_id}/config")
async def api_update_miner_config(miner_id: str, request: Request):
    form = await request.form()
    data = dict(form)
    data["miner_id"] = miner_id
    result = await _update_miner_config_result(data)
    status_code = 200 if result.get("status") == "ok" else 400
    return JSONResponse(result, status_code=status_code)



@app.post("/miners/set-control-enabled")
async def set_miner_control_enabled_from_dashboard(
    miner_id: str = Form(...),
    control_enabled: str = Form(...),
    next_url: str = Form("/"),
):
    target_url = str(next_url or "/").strip() or "/"
    desired_control_enabled = str(control_enabled).strip().lower() in {"1", "true", "on", "yes"}
    found = False

    for miner in state.config.get("miners", []):
        if miner.get("id") != miner_id:
            continue
        miner["control_enabled"] = desired_control_enabled
        if desired_control_enabled:
            miner["monitor_enabled"] = True
        found = True
        logger.info(
            "Miner control %s from dashboard: id=%s name=%s host=%s",
            "enabled" if desired_control_enabled else "disabled",
            miner_id,
            miner.get("name"),
            miner.get("host"),
        )
        break

    if found:
        save_config(state.config)
        reload_runtime()
        separator = "&" if "?" in target_url else "?"
        action_name = "control_enabled" if desired_control_enabled else "control_disabled"
        target_url = f"{target_url}{separator}miner_action={action_name}"

    return RedirectResponse(url=target_url, status_code=303)



async def _apply_miner_device_settings_result(miner_id: str, form: Any) -> dict[str, Any]:
    miner_id = str(miner_id).strip()

    for miner in state.config.get("miners", []):
        if miner.get("id") != miner_id:
            continue

        driver = _normalize_miner_driver(miner.get("driver", "simulator"))
        schema = _driver_device_settings_schema(driver)
        if not schema:
            return {"ok": False, "message": "Dieser Treiber unterstützt keine Geräte-Einstellungen."}

        values: dict[str, Any] = {}
        for field in schema:
            known_key = f"device__known__{field.name}"
            if form.get(known_key) is None:
                continue
            form_key = f"device__{field.name}"
            if field.type == "checkbox":
                raw = form.get(form_key) is not None
            else:
                raw = form.get(form_key)
            value = _coerce_field_value(field, raw, fallback=None)
            values[field.name] = value

        if not values:
            return {"ok": False, "message": "Keine lesbaren Geräte-Einstellungen verfügbar."}

        adapter = _get_runtime_adapter_by_id(miner_id)
        if adapter is None:
            return {"ok": False, "message": "Miner-Laufzeitadapter nicht verfügbar."}

        try:
            result = await asyncio.to_thread(adapter.apply_device_settings, values)
        except Exception as exc:
            return {"ok": False, "message": f"Geräte-Einstellung fehlgeschlagen: {exc}"}

        if not result or not result.get("ok"):
            return {
                "ok": False,
                "message": result.get("message", "Geräte-Einstellung fehlgeschlagen.") if isinstance(result, dict) else "Geräte-Einstellung fehlgeschlagen.",
            }

        return {
            "ok": True,
            "message": result.get("message", "Geräte-Einstellung erfolgreich angewendet.") if isinstance(result, dict) else "Geräte-Einstellung erfolgreich angewendet.",
        }

    return {"ok": False, "message": "Miner nicht gefunden."}



@app.post("/api/miner/{miner_id}/device-settings")
async def api_apply_miner_device_settings(miner_id: str, request: Request):
    result = await _apply_miner_device_settings_result(miner_id, await request.form())
    return JSONResponse(
        {
            "status": "ok" if result.get("ok") else "error",
            "message": result.get("message", ""),
        },
        status_code=200 if result.get("ok") else 400,
    )


async def _run_miner_action_impl(miner_id: str, action_name: str) -> dict[str, Any]:
    miner_id = str(miner_id).strip()
    action_name = str(action_name or "").strip()

    miner_cfg = next((miner for miner in state.config.get("miners", []) if miner.get("id") == miner_id), None)
    if miner_cfg is None:
        return {"ok": False, "message": "Miner nicht gefunden."}

    driver = _normalize_miner_driver(miner_cfg.get("driver", "simulator"))
    actions = {action.name: action for action in _driver_actions_schema(driver)}
    action = actions.get(action_name)
    if action is None:
        return {"ok": False, "message": "Diese Aktion wird vom Treiber nicht unterstützt."}

    if action.disabled_when_control_enabled and bool(miner_cfg.get("control_enabled", True)):
        return {"ok": False, "message": "Diese Aktion ist deaktiviert, solange der Miner in der Regelung ist."}

    adapter = _get_runtime_adapter_by_id(miner_id)
    if adapter is None:
        return {"ok": False, "message": "Miner-Laufzeitadapter nicht verfügbar."}

    try:
        result = await asyncio.to_thread(adapter.apply_action, action_name)
    except Exception as exc:
        return {"ok": False, "message": f"Miner-Aktion fehlgeschlagen: {exc}"}

    if not result or not result.get("ok"):
        return {
            "ok": False,
            "message": result.get("message", "Miner-Aktion fehlgeschlagen.") if isinstance(result, dict) else "Miner-Aktion fehlgeschlagen.",
        }

    return {
        "ok": True,
        "message": result.get("message", "Miner-Aktion erfolgreich ausgeführt.") if isinstance(result, dict) else "Miner-Aktion erfolgreich ausgeführt.",
    }


@app.post("/api/miner/{miner_id}/action")
async def api_run_miner_action(miner_id: str, request: Request):
    payload = await request.json()
    result = await _run_miner_action_impl(miner_id, str(payload.get("action_name", "")))
    return JSONResponse(
        {
            "status": "ok" if result.get("ok") else "error",
            "message": result.get("message", ""),
        },
        status_code=200 if result.get("ok") else 400,
    )



@app.post("/api/miner/{miner_id}/delete")
async def api_delete_miner(miner_id: str):
    miner_id = str(miner_id).strip()
    before = len(state.config.get("miners", []))
    state.config["miners"] = [
        miner for miner in state.config.get("miners", []) if miner.get("id") != miner_id
    ]
    if len(state.config.get("miners", [])) == before:
        return JSONResponse({"status": "error", "message": "Miner nicht gefunden."}, status_code=404)

    logger.info("Deleting miner: id=%s", miner_id)
    save_config(state.config)
    reload_runtime()
    return JSONResponse({"status": "ok", "message": "Miner gelöscht.", "miner_id": miner_id})


@app.get("/sockets")
async def sockets_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="sockets.html",
        context=_sockets_context(request),
    )


@app.get("/api/sockets/status")
async def api_sockets_status():
    socket_states = []
    for adapter in list(services.sockets):
        try:
            socket_states.append(await asyncio.to_thread(adapter.get_status))
        except Exception:
            logger.exception("Failed to refresh socket status: %s", getattr(getattr(adapter, "info", None), "id", "unknown"))
    state.sockets = socket_states
    return JSONResponse({"status": "ok", "sockets": _build_socket_snapshot_items()})



def _socket_driver_from_form(form: Any, fallback: str = "simulator") -> str:
    driver = str(form.get("driver") or fallback or "simulator").strip().lower()
    if driver not in {"simulator", "tasmota_http"}:
        driver = "simulator"
    return driver


def _socket_settings_from_form(form: Any, driver: str, current: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = dict(current or {})
    if driver == "tasmota_http":
        def as_int(name: str, default: int) -> int:
            try:
                return int(form.get(name) or settings.get(name, default) or default)
            except Exception:
                return default

        def as_float(name: str, default: float) -> float:
            try:
                return float(form.get(name) or settings.get(name, default) or default)
            except Exception:
                return default

        password_value = form.get("password")
        if password_value is None or str(password_value) == "__KEEP__":
            password = str(settings.get("password", "") or "")
        else:
            password = str(password_value or "")

        return {
            "port": as_int("port", 80),
            "relay": as_int("relay", 1),
            "username": str(form.get("username") or settings.get("username", "") or "").strip(),
            "password": password,
            "timeout_s": as_float("timeout_s", 2.0),
            "use_energy": form.get("use_energy") == "on" or bool(settings.get("use_energy", True)) and form.get("use_energy") is None,
        }

    def as_float(name: str, default: float) -> float:
        try:
            return float(form.get(name) or settings.get(name, default) or default)
        except Exception:
            return default

    return {
        "initial_on": bool(settings.get("initial_on", False)),
        "on_power_w": as_float("on_power_w", 50.0),
        "standby_power_w": as_float("standby_power_w", 0.0),
        "reachable": form.get("reachable") == "on",
    }


@app.post("/api/sockets/add")
async def api_add_socket(request: Request):
    form = await request.form()
    driver = _socket_driver_from_form(form)
    socket_id = f"s-{uuid4().hex[:8]}"
    default_name = "Tasmota Socket" if driver == "tasmota_http" else "Simulator Socket"
    name = str(form.get("name") or default_name).strip() or default_name
    host_default = "" if driver == "tasmota_http" else "simulator.local"
    host = str(form.get("host") or host_default).strip()
    socket_cfg = {
        "id": socket_id,
        "uuid": str(uuid4()),
        "name": name,
        "driver": driver,
        "host": host,
        "enabled": True,
        "monitor_enabled": True,
        "control_enabled": False,
        "priority": 100,
        "settings": _socket_settings_from_form(form, driver),
    }
    state.config.setdefault("sockets", []).append(socket_cfg)
    save_config(state.config)
    reload_runtime()
    return JSONResponse({"status": "ok", "message": "Socket angelegt.", "socket_id": socket_id})


@app.post("/api/socket/{socket_id}/config")
async def api_update_socket_config(socket_id: str, request: Request):
    form = await request.form()
    socket_id = str(socket_id).strip()
    for socket_cfg in state.config.get("sockets", []) or []:
        if socket_cfg.get("id") != socket_id:
            continue
        driver = _socket_driver_from_form(form, str(socket_cfg.get("driver") or "simulator"))
        socket_cfg["driver"] = driver
        socket_cfg["name"] = str(form.get("name") or socket_cfg.get("name") or "Socket").strip() or "Socket"
        socket_cfg["host"] = str(form.get("host") or "").strip()
        socket_cfg["monitor_enabled"] = form.get("monitor_enabled") == "on"
        socket_cfg["control_enabled"] = form.get("control_enabled") == "on"
        try:
            socket_cfg["priority"] = int(form.get("priority") or socket_cfg.get("priority", 100) or 100)
        except Exception:
            socket_cfg["priority"] = 100
        socket_cfg["settings"] = _socket_settings_from_form(form, driver, socket_cfg.get("settings", {}) or {})
        save_config(state.config)
        reload_runtime()
        return JSONResponse({"status": "ok", "message": "Socket-Konfiguration gespeichert.", "socket_id": socket_id})
    return JSONResponse({"status": "error", "message": "Socket nicht gefunden."}, status_code=404)


@app.post("/api/socket/{socket_id}/switch")
async def api_switch_socket(socket_id: str, request: Request):
    payload = await request.json()
    action = str(payload.get("action") or "").strip().lower()
    adapter = _get_runtime_socket_adapter_by_id(str(socket_id).strip())
    if adapter is None:
        return JSONResponse({"status": "error", "message": "Socket-Laufzeitadapter nicht verfügbar."}, status_code=404)
    try:
        if action == "on":
            result = await asyncio.to_thread(adapter.switch_on)
        elif action == "off":
            result = await asyncio.to_thread(adapter.switch_off)
        elif action == "reboot":
            result = await asyncio.to_thread(adapter.reboot)
        else:
            return JSONResponse({"status": "error", "message": "Unbekannte Socket-Aktion."}, status_code=400)
    except Exception as exc:
        logger.exception("Socket action failed: id=%s action=%s", socket_id, action)
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    try:
        state.sockets = [await asyncio.to_thread(socket.get_status) for socket in services.sockets]
    except Exception:
        logger.debug("Could not refresh sockets after action", exc_info=True)
    status = "ok" if result.get("ok") else "error"
    return JSONResponse({"status": status, **result})


@app.post("/api/sockets/discover/tasmota")
async def api_discover_tasmota(request: Request):
    form = await request.form()
    cidr = str(form.get("cidr") or "").strip()
    port = int(form.get("port") or 80)
    if not cidr:
        networks = get_local_ipv4_networks()
        cidr = str((networks[0] or {}).get("cidr") if networks else "")
    if not cidr:
        return JSONResponse({"status": "error", "message": "Kein lokales IPv4-Netz für die Suche gefunden."}, status_code=400)
    try:
        results = await asyncio.to_thread(discover_tasmota_http, cidr, port=port)
    except Exception as exc:
        logger.exception("Tasmota discovery failed")
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)
    return JSONResponse({"status": "ok", "cidr": cidr, "results": results})

@app.post("/api/socket/{socket_id}/delete")
async def api_delete_socket(socket_id: str):
    socket_id = str(socket_id).strip()
    before = len(state.config.get("sockets", []) or [])
    state.config["sockets"] = [socket_cfg for socket_cfg in state.config.get("sockets", []) or [] if socket_cfg.get("id") != socket_id]
    if len(state.config.get("sockets", []) or []) == before:
        return JSONResponse({"status": "error", "message": "Socket nicht gefunden."}, status_code=404)
    save_config(state.config)
    reload_runtime()
    return JSONResponse({"status": "ok", "message": "Socket gelöscht.", "socket_id": socket_id})


@app.get("/datalogger")
async def datalogger_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="datalogger.html",
        context={
            "request": request,
            "instance_name": state.config["system"].get("instance_name", "PV2Hash Node"),
            "status": data_logger.status(),
        },
    )


@app.get("/api/datalogger/status")
async def api_datalogger_status():
    return JSONResponse(content=jsonable_encoder({"status": "ok", "datalogger": data_logger.status()}))


@app.get("/api/datalogger/series")
async def api_datalogger_series(range: str = "24h", max_points: int = 720):
    return JSONResponse(content=jsonable_encoder({"status": "ok", "series": data_logger.series(range_name=range, max_points=max_points)}))


@app.get("/system")
async def system_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="system.html",
        context={
            "request": request,
            "instance_name": state.config["system"].get("instance_name", "PV2Hash Node"),
        },
    )


@app.get("/system/update-progress")
async def system_update_progress_page(request: Request):
    update_status = update_checker.snapshot()

    context = {
        "request": request,
        "instance_name": state.config["system"]["instance_name"],
        "app_version_full": APP_VERSION_FULL,
        "update_status": update_status,
        "update_runner_status": _update_runner_snapshot(update_status),
    }
    return templates.TemplateResponse(
        request=request,
        name="update_progress.html",
        context=context,
    )


@app.post("/api/system/reload")
async def api_system_reload():
    logger.info("Manual system reload triggered from UI")
    reload_runtime()
    return JSONResponse(content=jsonable_encoder({"status": "ok", "message": "Runtime wurde neu geladen.", "model": _build_system_model()}))


@app.post("/api/system/logging")
async def api_system_logging(request: Request):
    payload = await request.json()
    log_level = _normalize_log_level(payload.get("log_level"), state.config["system"].get("log_level", "INFO"))
    state.config["system"]["log_level"] = log_level
    save_config(state.config)
    setup_logging(log_level)
    logger.info("Log level changed to %s", log_level)
    return JSONResponse(content=jsonable_encoder({"status": "ok", "message": f"Log-Level auf {log_level} gesetzt.", "model": _build_system_model()}))


@app.get("/api/runtime/snapshot")
async def api_runtime_snapshot():
    return JSONResponse(content=jsonable_encoder(_build_runtime_snapshot_payload()))


@app.get("/api/dashboard/status")
async def api_dashboard_status():
    return JSONResponse(content=jsonable_encoder(_build_dashboard_live_payload()))


def _build_ui_versionstatus_payload() -> dict[str, Any]:
    update_status = update_checker.snapshot()
    status_value = str(update_status.get("status") or "unknown")
    update_available = status_value == "update_available"
    release_label = update_status.get("release_version_full") or update_status.get("release_tag")

    if update_available and release_label:
        label = f"Version {APP_VERSION_FULL} · Update verfügbar: {release_label}"
        title = "Update verfügbar – zur Systemseite wechseln"
    elif update_available:
        label = f"Version {APP_VERSION_FULL} · Update verfügbar"
        title = "Update verfügbar – zur Systemseite wechseln"
    else:
        label = f"Version {APP_VERSION_FULL}"
        title = "Zur Systemseite wechseln"

    return {
        "status": "ok",
        "version": APP_VERSION,
        "version_full": APP_VERSION_FULL,
        "label": label,
        "title": title,
        "href": "/system",
        "update_status": status_value,
        "update_available": update_available,
        "release_version_full": update_status.get("release_version_full"),
        "release_tag": update_status.get("release_tag"),
        "checked_at": update_status.get("checked_at"),
    }


@app.get("/api/ui/versionstatus")
async def api_ui_versionstatus():
    return JSONResponse(content=jsonable_encoder(_build_ui_versionstatus_payload()))


@app.get("/api/miners/status")
async def api_miners_status(open_ids: str = ""):
    selected_open_ids = {item.strip() for item in str(open_ids or "").split(",") if item.strip()}
    return JSONResponse(_build_miners_live_payload(selected_open_ids))


@app.get("/api/system/host-status")
async def api_system_host_status():
    return JSONResponse(content=jsonable_encoder(_get_host_status()))


@app.get("/system/config/export")
async def system_config_export():
    return FileResponse(
        path=CONFIG_PATH,
        filename=f"pv2hash-config-{APP_VERSION_FULL}.json",
        media_type="application/json",
    )


@app.post("/api/system/config/import")
async def api_system_config_import(request: Request):
    try:
        form = await request.form()
        upload = form.get("config_file")
        if upload is None:
            return JSONResponse(content={"status": "error", "message": "Keine Konfigurationsdatei ausgewählt."}, status_code=400)

        raw_bytes = await upload.read()
        if not raw_bytes:
            return JSONResponse(content={"status": "error", "message": "Die ausgewählte Datei ist leer."}, status_code=400)

        try:
            imported_config = json.loads(raw_bytes.decode("utf-8"))
        except Exception:
            return JSONResponse(content={"status": "error", "message": "Die Konfigurationsdatei ist kein gültiges JSON."}, status_code=400)

        if not isinstance(imported_config, dict):
            return JSONResponse(content={"status": "error", "message": "Die Konfigurationsdatei muss ein JSON-Objekt enthalten."}, status_code=400)

        if CONFIG_PATH.exists():
            backup_path = CONFIG_PATH.with_name(
                f"config.backup-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
            )
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CONFIG_PATH, backup_path)

        save_config(imported_config)
        reloaded_config = load_config()
        setup_logging(reloaded_config.get("system", {}).get("log_level", "INFO"))
        reload_runtime()
        logger.info("Configuration restored from uploaded JSON")
        return JSONResponse(content=jsonable_encoder({"status": "ok", "message": "Konfiguration wurde importiert und Runtime neu geladen.", "model": _build_system_model()}))
    except Exception:
        logger.exception("Failed to import configuration")
        return JSONResponse(content={"status": "error", "message": "Konfiguration konnte nicht importiert werden."}, status_code=500)


@app.get("/api/system/model")
async def api_system_model():
    return JSONResponse(content=jsonable_encoder(_build_system_model()))


@app.get("/api/system/update-model")
async def api_system_update_model():
    return JSONResponse(content=jsonable_encoder(_build_system_update_model()))


@app.get("/api/system/update-status")
async def api_system_update_status():
    return JSONResponse(content=jsonable_encoder(update_checker.snapshot()))


@app.post("/api/system/update-check")
async def api_system_update_check():
    payload = await update_checker.refresh()
    return JSONResponse(content=jsonable_encoder(payload))


@app.get("/api/system/self-update-status")
async def api_system_self_update_status():
    update_status = update_checker.snapshot()
    payload = _update_runner_snapshot(update_status)
    return JSONResponse(content=jsonable_encoder(payload))


@app.post("/api/system/self-update")
async def api_system_self_update():
    update_status = await update_checker.refresh()
    payload, status_code = _update_runner_start_latest(update_status)
    response_payload = dict(payload)
    response_payload["progress_url"] = "/system/update-progress"
    return JSONResponse(content=jsonable_encoder(response_payload), status_code=status_code)


@app.get("/api/status")
async def api_status():
    payload = {
        "snapshot": asdict(state.snapshot) if state.snapshot else None,
        "miners": [asdict(m) for m in state.miners],
        "config": state.config,
        "started_at": state.started_at.isoformat(),
        "last_decision": state.last_decision,
        "last_decision_at": state.last_decision_at.isoformat() if state.last_decision_at else None,
        "last_profile_switch_at": state.last_profile_switch_at.isoformat() if state.last_profile_switch_at else None,
        "last_reload_at": state.last_reload_at.isoformat(),
        "source_debug": services.get_source_debug_info(),
        "battery_source_debug": services.get_battery_source_debug_info(),
    }
    return JSONResponse(content=jsonable_encoder(payload))


@app.get("/api/config")
async def api_config():
    return JSONResponse(state.config)


@app.get("/api/logs")
async def api_logs():
    return JSONResponse({"lines": get_ringbuffer_lines()})


@app.get("/api/logs/download")
async def api_logs_download():
    log_file = get_log_file_path()
    return FileResponse(
        path=log_file,
        filename="pv2hash.log",
        media_type="text/plain",
    )
