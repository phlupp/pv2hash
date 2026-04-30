import json
from uuid import uuid4
from copy import deepcopy
from pathlib import Path
from typing import Any

from pv2hash.config.defaults import DEFAULT_CONFIG
from pv2hash.datalogger import normalize_datalogger_config

CONFIG_PATH = Path("data/config.json")

PROFILE_NAMES = ("p1", "p2", "p3", "p4")
FALLBACK_PROFILE_NAMES = ("off", "p1", "p2", "p3", "p4")
MIN_REGULATED_PROFILE_NAMES = ("off", "p1", "p2", "p3", "p4")
BATTERY_OVERRIDE_PROFILE_NAMES = ("p1", "p2", "p3", "p4")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)

    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result




def _ensure_uuid(value: Any | None = None) -> str:
    text = str(value or "").strip()
    return text or str(uuid4())


def _normalize_identity_fields(config: dict[str, Any]) -> None:
    source = config.setdefault("source", {})
    source["uuid"] = _ensure_uuid(source.get("uuid"))

    battery = config.setdefault("battery", {})
    battery["uuid"] = _ensure_uuid(battery.get("uuid"))

    for miner in config.get("miners", []):
        miner["uuid"] = _ensure_uuid(miner.get("uuid"))

def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _clamp_float(value: Any, default: float, min_value: float, max_value: float) -> float:
    parsed = _coerce_float(value, default)
    return max(min_value, min(parsed, max_value))


def _normalize_miner_profiles(config: dict[str, Any]) -> None:
    default_profiles = DEFAULT_CONFIG["miners"][0]["profiles"]
    default_miner = DEFAULT_CONFIG["miners"][0]

    for miner in config.get("miners", []):
        # TRANSITION 0.6.x: migrate the old single enabled flag into
        # the new explicit flags. This block should be removed once
        # all installations have saved configs with monitor_enabled/control_enabled.
        if "monitor_enabled" not in miner or "control_enabled" not in miner:
            legacy_enabled = bool(miner.get("enabled", True))
            miner["monitor_enabled"] = legacy_enabled
            miner["control_enabled"] = legacy_enabled
        miner.pop("enabled", None)
        if bool(miner.get("control_enabled", True)):
            miner["monitor_enabled"] = True


    for miner in config.get("miners", []):
        profiles = miner.setdefault("profiles", {})
        normalized_profiles: dict[str, dict[str, float]] = {}

        for name in PROFILE_NAMES:
            raw_profile = profiles.get(name, {})
            default_power = default_profiles[name]["power_w"]

            if isinstance(raw_profile, dict):
                raw_power = raw_profile.get("power_w", default_power)
            else:
                raw_power = default_power

            try:
                power_w = float(raw_power)
            except Exception:
                power_w = float(default_power)

            if power_w <= 0:
                power_w = float(default_power)

            normalized_profiles[name] = {"power_w": power_w}

        miner["profiles"] = normalized_profiles

        min_regulated_profile = str(
            miner.get("min_regulated_profile", "off")
        ).strip().lower()

        if min_regulated_profile not in MIN_REGULATED_PROFILE_NAMES:
            min_regulated_profile = "off"

        miner["min_regulated_profile"] = min_regulated_profile

        miner["use_battery_when_charging"] = bool(
            miner.get("use_battery_when_charging", False)
        )
        miner["battery_charge_soc_min"] = _clamp_float(
            miner.get(
                "battery_charge_soc_min",
                default_miner.get("battery_charge_soc_min", 95.0),
            ),
            default=float(default_miner.get("battery_charge_soc_min", 95.0)),
            min_value=0.0,
            max_value=100.0,
        )
        battery_charge_profile = str(
            miner.get(
                "battery_charge_profile",
                default_miner.get("battery_charge_profile", "p1"),
            )
        ).strip().lower()
        if battery_charge_profile not in BATTERY_OVERRIDE_PROFILE_NAMES:
            battery_charge_profile = str(default_miner.get("battery_charge_profile", "p1"))
        miner["battery_charge_profile"] = battery_charge_profile

        miner["use_battery_when_discharging"] = bool(
            miner.get("use_battery_when_discharging", False)
        )
        miner["battery_discharge_soc_min"] = _clamp_float(
            miner.get(
                "battery_discharge_soc_min",
                default_miner.get("battery_discharge_soc_min", 80.0),
            ),
            default=float(default_miner.get("battery_discharge_soc_min", 80.0)),
            min_value=0.0,
            max_value=100.0,
        )
        battery_discharge_profile = str(
            miner.get(
                "battery_discharge_profile",
                default_miner.get("battery_discharge_profile", "p1"),
            )
        ).strip().lower()
        if battery_discharge_profile not in BATTERY_OVERRIDE_PROFILE_NAMES:
            battery_discharge_profile = str(
                default_miner.get("battery_discharge_profile", "p1")
            )
        miner["battery_discharge_profile"] = battery_discharge_profile


def _normalize_source_loss_profiles(config: dict[str, Any]) -> None:
    control = config.setdefault("control", {})
    source_loss = control.setdefault("source_loss", {})

    try:
        control["max_import_w"] = max(0.0, float(control.get("max_import_w", 200)))
    except Exception:
        control["max_import_w"] = 200.0

    for quality in ("stale", "offline"):
        behavior = source_loss.setdefault(quality, {})

        fallback_profile = str(
            behavior.get("fallback_profile", "p1")
        ).strip().lower()

        if fallback_profile not in FALLBACK_PROFILE_NAMES:
            fallback_profile = "p1"

        behavior["fallback_profile"] = fallback_profile




def _normalize_source_settings(config: dict[str, Any]) -> None:
    source = config.setdefault("source", {})
    settings = source.setdefault("settings", {})
    settings["debug_dump_obis"] = bool(settings.get("debug_dump_obis", False))

def _normalize_datalogger_settings(config: dict[str, Any]) -> None:
    config["datalogger"] = normalize_datalogger_config(config.get("datalogger", {}))


def _normalize_sockets(config: dict[str, Any]) -> None:
    sockets = config.setdefault("sockets", [])
    if not isinstance(sockets, list):
        config["sockets"] = []
        return

    for socket_cfg in sockets:
        if not isinstance(socket_cfg, dict):
            continue
        socket_cfg["uuid"] = _ensure_uuid(socket_cfg.get("uuid"))
        socket_cfg["id"] = str(socket_cfg.get("id") or f"s-{uuid4().hex[:8]}").strip()
        socket_cfg["name"] = str(socket_cfg.get("name") or "Socket").strip() or "Socket"
        socket_cfg["driver"] = str(socket_cfg.get("driver") or "simulator").strip().lower() or "simulator"
        socket_cfg["host"] = str(socket_cfg.get("host") or "").strip()
        socket_cfg["enabled"] = bool(socket_cfg.get("enabled", True))
        socket_cfg["monitor_enabled"] = bool(socket_cfg.get("monitor_enabled", True))
        socket_cfg["control_enabled"] = bool(socket_cfg.get("control_enabled", False))
        try:
            socket_cfg["priority"] = int(socket_cfg.get("priority", 100) or 100)
        except Exception:
            socket_cfg["priority"] = 100
        settings = socket_cfg.setdefault("settings", {})
        if not isinstance(settings, dict):
            socket_cfg["settings"] = {}


def _normalize_battery_settings(config: dict[str, Any]) -> None:
    battery = config.setdefault("battery", {})
    defaults = DEFAULT_CONFIG.get("battery", {})

    battery["charge_active_threshold_w"] = max(
        0.0,
        _coerce_float(
            battery.get(
                "charge_active_threshold_w",
                defaults.get("charge_active_threshold_w", 100.0),
            ),
            float(defaults.get("charge_active_threshold_w", 100.0)),
        ),
    )
    battery["discharge_active_threshold_w"] = max(
        0.0,
        _coerce_float(
            battery.get(
                "discharge_active_threshold_w",
                defaults.get("discharge_active_threshold_w", 100.0),
            ),
            float(defaults.get("discharge_active_threshold_w", 100.0)),
        ),
    )


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(config)
    _normalize_identity_fields(normalized)
    _normalize_miner_profiles(normalized)
    _normalize_source_settings(normalized)
    _normalize_source_loss_profiles(normalized)
    _normalize_battery_settings(normalized)
    _normalize_sockets(normalized)
    _normalize_datalogger_settings(normalized)
    return normalized


def ensure_config_exists() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)


def load_config() -> dict[str, Any]:
    ensure_config_exists()

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        user_config = json.load(f)

    merged = deep_merge(DEFAULT_CONFIG, user_config)
    normalized = normalize_config(merged)
    if normalized != merged:
        save_config(normalized)
    return normalized


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_config(config)

    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)


def update_config(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_config()
    merged = deep_merge(current, patch)
    normalized = normalize_config(merged)
    save_config(normalized)
    return normalized
