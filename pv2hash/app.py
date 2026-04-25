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

initial_config = load_config()
setup_logging(initial_config.get("system", {}).get("log_level", "INFO"))
logger = get_logger("pv2hash.app")

app = FastAPI(title="PV2Hash", version=APP_VERSION_FULL)
app.mount("/static", StaticFiles(directory="pv2hash/static"), name="static")
templates = Jinja2Templates(directory="pv2hash/templates")

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

EDITABLE_PROFILE_NAMES = ("p1", "p2", "p3", "p4")
ALL_PROFILE_NAMES = ("off", "p1", "p2", "p3", "p4")
EDITABLE_MIN_REGULATED_PROFILE_NAMES = ("off", "p1", "p2", "p3", "p4")
MODBUS_REGISTER_TYPES = ("holding", "input", "coil", "discrete_input")
MODBUS_VALUE_TYPES = ("int8", "uint8", "int16", "uint16", "int32", "uint32", "float32")
MODBUS_ENDIAN_TYPES = ("big_endian", "little_endian")


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
        DriverField(name="monitor_enabled", label="Verbindung", type="checkbox", default=True, help="PV2Hash baut den Miner-Adapter auf, liest Status und erlaubt Geräte-Einstellungen."),
        DriverField(name="control_enabled", label="In Regelung einbeziehen", type="checkbox", default=True, help="Der PV-Regler darf diesen Miner steuern. Erfordert Verbindung."),
        DriverField(name="priority", label="Priorität", type="number", default=100, placeholder="100"),
        DriverField(name="min_regulated_profile", label="Min. Regelprofil", type="select", default="off", choices=min_profile_choices),
        DriverField(name="profiles.p1.power_w", label="Profil p1 (W)", type="number", default=900, read_only=fixed_power_profiles),
        DriverField(name="profiles.p2.power_w", label="Profil p2 (W)", type="number", default=1800, read_only=fixed_power_profiles),
        DriverField(name="profiles.p3.power_w", label="Profil p3 (W)", type="number", default=3000, read_only=fixed_power_profiles),
        DriverField(name="profiles.p4.power_w", label="Profil p4 (W)", type="number", default=4200, read_only=fixed_power_profiles),
        DriverField(name="use_battery_when_charging", label="Beim Laden Batterie nutzen", type="checkbox", default=False),
        DriverField(name="battery_charge_soc_min", label="Mindest-SOC Laden (%)", type="number", default=95),
        DriverField(name="battery_charge_profile", label="Profil bei Laden", type="select", default="p1", choices=battery_choices),
        DriverField(name="use_battery_when_discharging", label="Beim Entladen Batterie nutzen", type="checkbox", default=False),
        DriverField(name="battery_discharge_soc_min", label="Mindest-SOC Entladen (%)", type="number", default=80),
        DriverField(name="battery_discharge_profile", label="Profil bei Entladen", type="select", default="p1", choices=battery_choices),
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


def _render_field(field: DriverField, value: Any) -> dict:
    return {
        **asdict(field),
        "value": value,
        "id": field.name.replace('.', '-'),
    }


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


_HOST_CPU_SAMPLE: tuple[int, int] | None = None


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


def _get_host_status() -> dict:
    hostname = socket.gethostname()
    try:
        fqdn = socket.getfqdn()
    except Exception:
        fqdn = ''
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
    return {
        'hostname': hostname or '—',
        'fqdn': fqdn if fqdn and fqdn != hostname else '',
        'cpu_percent': cpu_percent,
        'cpu_percent_text': f"{cpu_percent:.0f}%" if cpu_percent is not None else '—',
        'ram_used_bytes': mem_used,
        'ram_total_bytes': mem_total,
        'ram_text': (f"{_format_bytes(mem_used)} / {_format_bytes(mem_total)}" if mem_used is not None and mem_total is not None else '—'),
        'ram_percent': mem_percent,
        'ram_percent_text': f"{mem_percent:.0f}%" if mem_percent is not None else '—',
        'load_text': load_text,
        'uptime_seconds': _read_uptime_seconds(),
        'uptime_text': _format_duration(_read_uptime_seconds()),
        'platform_text': f"{platform.system()} {platform.release()}",
        'python_text': sys.version.split()[0],
        'storage': _build_storage_summary(),
    }


def _redirect_with_system_message(*, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    params = []
    if notice:
        params.append(f"config_import_notice={quote_plus(notice)}")
    if error:
        params.append(f"config_import_error={quote_plus(error)}")
    suffix = f"?{'&'.join(params)}" if params else ''
    return RedirectResponse(url=f"/system{suffix}", status_code=303)


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
        merged["runtime"] = runtime
        merged["summary"] = _miner_card_summary(merged, runtime)

        item = {
            "id": miner_id,
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

            state.snapshot = snapshot
            state.miners = miner_states
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


@app.get("/sources")
async def sources_page(request: Request):
    from pv2hash.netutils import get_local_ipv4_addresses

    source_debug = services.get_source_debug_info()
    source_cfg = state.config["source"]
    battery_cfg = state.config.get("battery", {})
    sma_discovered_devices, selected_sma_device_serial = _build_sma_device_choices(source_cfg, source_debug)

    context = {
        "request": request,
        "source": source_cfg,
        "battery": battery_cfg,
        "snapshot": state.snapshot,
        "source_debug": source_debug,
        "source_profile_label": _resolve_measurement_profile_label(source_cfg.get("type", "simulator")),
        "battery_profile_label": _resolve_battery_profile_label(battery_cfg.get("type", "none")),
        "source_device_label": _resolve_sma_device_label(source_debug),
        "source_device_serial_number": source_debug.get("last_packet_serial_number") if source_debug else None,
        "source_device_susy_id": source_debug.get("last_packet_susy_id") if source_debug else None,
        "sma_discovered_devices": sma_discovered_devices,
        "selected_sma_device_serial": selected_sma_device_serial,
        "saved": request.query_params.get("saved") == "1",
        "serial_required": request.query_params.get("serial_required") == "1",
        "local_interface_ips": get_local_ipv4_addresses(),
        "modbus_register_types": MODBUS_REGISTER_TYPES,
        "modbus_value_types": MODBUS_VALUE_TYPES,
        "modbus_endian_types": MODBUS_ENDIAN_TYPES,
        "refresh_seconds": _safe_int(state.config.get("app", {}).get("refresh_seconds", 5), 5),
        "wiki_links": {
            "overview": "https://github.com/phlupp/pv2hash/wiki/Messungen",
            "nap": "https://github.com/phlupp/pv2hash/wiki/Netz-Messung",
            "sma": "https://github.com/phlupp/pv2hash/wiki/SMA-Energy-Meter",
            "battery": "https://github.com/phlupp/pv2hash/wiki/Batterie",
            "battery_modbus": "https://github.com/phlupp/pv2hash/wiki/Batterie-Modbus-TCP",
        },
    }
    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context=context,
    )


@app.post("/sources")
async def save_source(request: Request):
    form = await request.form()

    source_type = str(form.get("source_type", "simulator")).strip()

    state.config["source"]["type"] = source_type
    state.config["source"]["name"] = _resolve_measurement_profile_label(source_type)
    state.config["source"]["enabled"] = True
    state.config["source"].setdefault("settings", {})
    state.config["source"]["settings"]["multicast_ip"] = form.get(
        "multicast_ip", "239.12.255.254"
    )
    state.config["source"]["settings"]["bind_port"] = _safe_int(form.get("bind_port", 9522), 9522)
    state.config["source"]["settings"]["interface_ip"] = (
        form.get("interface_ip", "0.0.0.0").strip() or "0.0.0.0"
    )
    state.config["source"]["settings"]["packet_timeout_seconds"] = _safe_float(
        form.get("packet_timeout_seconds", 1.0), 1.0
    )
    state.config["source"]["settings"]["stale_after_seconds"] = _safe_float(
        form.get("stale_after_seconds", 8.0), 8.0
    )
    state.config["source"]["settings"]["offline_after_seconds"] = _safe_float(
        form.get("offline_after_seconds", 30.0), 30.0
    )
    selected_device_serial = _normalize_sma_serial_number(form.get("device_serial_number", ""))
    if source_type == "sma_meter_protocol" and not selected_device_serial:
        return RedirectResponse(url="/sources?serial_required=1", status_code=303)

    state.config["source"]["settings"]["device_serial_number"] = selected_device_serial
    state.config["source"]["settings"].pop("device_ip", None)
    state.config["source"]["settings"]["debug_dump_obis"] = form.get("debug_dump_obis") == "on"
    state.config["source"]["settings"]["simulator_import_power_w"] = _safe_float(
        form.get("simulator_import_power_w", 1000.0), 1000.0
    )
    state.config["source"]["settings"]["simulator_export_power_w"] = _safe_float(
        form.get("simulator_export_power_w", 10000.0), 10000.0
    )
    state.config["source"]["settings"]["simulator_ramp_rate_w_per_minute"] = _safe_float(
        form.get("simulator_ramp_rate_w_per_minute", 600.0), 600.0
    )

    battery_type = str(form.get("battery_type", "none")).strip()
    battery_enabled = (form.get("battery_enabled") == "on") and battery_type != "none"

    state.config.setdefault("battery", {})
    state.config["battery"]["type"] = battery_type
    state.config["battery"]["name"] = _resolve_battery_profile_label(battery_type)
    state.config["battery"]["enabled"] = battery_enabled
    state.config["battery"].setdefault("settings", {})
    state.config["battery"]["settings"]["host"] = str(form.get("battery_host", "")).strip()
    state.config["battery"]["settings"]["port"] = _safe_int(form.get("battery_port", 502), 502)
    state.config["battery"]["settings"]["unit_id"] = _safe_int(form.get("battery_unit_id", 1), 1)
    state.config["battery"]["settings"]["poll_interval_ms"] = _safe_int(
        form.get("battery_poll_interval_ms", 1000), 1000
    )
    state.config["battery"]["settings"]["request_timeout_seconds"] = _safe_float(
        form.get("battery_request_timeout_seconds", 1.0), 1.0
    )
    state.config["battery"]["settings"]["soc"] = _parse_modbus_value_form(form, "battery_soc")
    state.config["battery"]["settings"]["charge_power"] = _parse_modbus_value_form(form, "battery_charge_power")
    state.config["battery"]["settings"]["discharge_power"] = _parse_modbus_value_form(form, "battery_discharge_power")

    save_config(state.config)
    logger.info(
        "Source settings saved: type=%s battery_type=%s battery_enabled=%s",
        source_type,
        battery_type,
        battery_enabled,
    )
    reload_runtime()
    return RedirectResponse(url="/sources?saved=1", status_code=303)


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


@app.get("/system")
async def system_page(request: Request):
    update_status = update_checker.snapshot()
    host_status = _get_host_status()

    context = {
        "request": request,
        "instance_name": state.config["system"]["instance_name"],
        "log_level": state.config["system"].get("log_level", "INFO"),
        "started_at": state.started_at,
        "last_reload_at": state.last_reload_at,
        "last_live_packet_at": state.last_live_packet_at,
        "source_type": state.config["source"].get("type", "unknown"),
        "source_profile_label": _resolve_measurement_profile_label(state.config["source"].get("type")),
        "battery_profile_label": _resolve_battery_profile_label(state.config.get("battery", {}).get("type")),
        "miner_count": len(state.miners),
        "app_version": APP_VERSION,
        "app_version_full": APP_VERSION_FULL,
        "update_status": update_status,
        "update_runner_status": _update_runner_snapshot(update_status),
        "allowed_log_levels": ("INFO", "DEBUG"),
        "host_status": host_status,
        "config_import_notice": request.query_params.get("config_import_notice"),
        "config_import_error": request.query_params.get("config_import_error"),
    }
    return templates.TemplateResponse(
        request=request,
        name="system.html",
        context=context,
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


@app.post("/system/reload")
async def system_reload():
    logger.info("Manual system reload triggered from UI")
    reload_runtime()
    return RedirectResponse(url="/", status_code=303)


@app.post("/system/logging")
async def system_logging(request: Request):
    form = await request.form()
    log_level = _normalize_log_level(form.get("log_level", state.config["system"].get("log_level", "INFO")))
    state.config["system"]["log_level"] = log_level
    save_config(state.config)
    setup_logging(log_level)
    logger.info("Log level changed to %s", log_level)
    return RedirectResponse(url="/system", status_code=303)



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


@app.post("/system/config/import")
async def system_config_import(request: Request):
    try:
        form = await request.form()
        upload = form.get("config_file")
        if upload is None:
            return _redirect_with_system_message(error="Keine Konfigurationsdatei ausgewählt.")

        raw_bytes = await upload.read()
        if not raw_bytes:
            return _redirect_with_system_message(error="Die ausgewählte Datei ist leer.")

        try:
            imported_config = json.loads(raw_bytes.decode("utf-8"))
        except Exception:
            return _redirect_with_system_message(error="Die Konfigurationsdatei ist kein gültiges JSON.")

        if not isinstance(imported_config, dict):
            return _redirect_with_system_message(error="Die Konfigurationsdatei muss ein JSON-Objekt enthalten.")

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
        return _redirect_with_system_message(notice="Konfiguration wurde importiert und Runtime neu geladen.")
    except Exception:
        logger.exception("Failed to import configuration")
        return _redirect_with_system_message(error="Konfiguration konnte nicht importiert werden.")


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
