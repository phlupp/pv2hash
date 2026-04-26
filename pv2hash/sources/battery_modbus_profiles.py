from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pv2hash.logging_ext.setup import get_logger


logger = get_logger("pv2hash.source.battery_modbus.profiles")

PACKAGE_PROFILE_DIR = Path(__file__).resolve().parent.parent / "modbus_profiles" / "battery"
USER_PROFILE_DIR = Path("/var/lib/pv2hash/modbus_profiles/battery")

SUPPORTED_REGISTER_TYPES = ("holding", "input", "coil", "discrete_input")
SUPPORTED_VALUE_TYPES = ("int8", "uint8", "int16", "uint16", "int32", "uint32", "float32")
SUPPORTED_ENDIAN_TYPES = ("big_endian", "little_endian")
REGISTER_VALUE_KEYS = (
    "soc",
    "charge_power",
    "discharge_power",
    "voltage",
    "current",
    "soh",
    "temperature",
    "capacity",
    "max_charge_current",
    "max_discharge_current",
)


@dataclass(frozen=True, slots=True)
class BatteryModbusProfile:
    id: str
    name: str
    vendor: str = ""
    description: str = ""
    path: Path | None = None
    values: dict[str, Any] | None = None

    @property
    def label(self) -> str:
        if self.vendor:
            return f"{self.vendor} – {self.name}"
        return self.name


def _profile_dirs() -> list[Path]:
    dirs = [PACKAGE_PROFILE_DIR]
    if USER_PROFILE_DIR != PACKAGE_PROFILE_DIR:
        dirs.append(USER_PROFILE_DIR)
    return dirs


def _safe_profile_id(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in raw)
    return safe or fallback


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return value[:index].rstrip()
    return value.strip()


def _parse_scalar(value: str) -> Any:
    value = _strip_inline_comment(value).strip()
    if value == "":
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    lower = value.lower()
    if lower in {"true", "yes", "on"}:
        return True
    if lower in {"false", "no", "off"}:
        return False
    if lower in {"null", "none", "~"}:
        return None
    try:
        if any(ch in value for ch in (".", "e", "E")):
            return float(value)
        return int(value, 10)
    except Exception:
        return value


def _load_simple_yaml_mapping(path: Path) -> dict[str, Any]:
    """Load the small YAML subset used by PV2Hash Modbus profiles.

    Supported syntax is intentionally simple: comments, scalar key/value pairs
    and nested mappings by indentation. This avoids adding a runtime dependency
    for user-editable profile files while keeping the format YAML-compatible.
    """
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            raise ValueError(f"line {line_number}: expected key: value")
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"line {line_number}: empty key")

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"line {line_number}: invalid indentation")
        parent = stack[-1][1]

        value = value.strip()
        if value == "" or value.startswith("#"):
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(value)

    return root


def _load_profile_file(path: Path) -> dict[str, Any]:
    data = _load_simple_yaml_mapping(path)
    if not isinstance(data, dict):
        raise ValueError("profile root must be a YAML mapping")
    return data


def iter_battery_modbus_profiles(*, include_hidden: bool = False) -> list[BatteryModbusProfile]:
    profiles: dict[str, BatteryModbusProfile] = {}
    for directory in _profile_dirs():
        if not directory.exists() or not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                data = _load_profile_file(path)
                if bool(data.get("hidden", False)) and not include_hidden:
                    continue
                profile_id = _safe_profile_id(data.get("id"), path.stem)
                values = data.get("values") or {}
                if not isinstance(values, dict):
                    raise ValueError("profile values must be a YAML mapping")
                profiles[profile_id] = BatteryModbusProfile(
                    id=profile_id,
                    name=str(data.get("name") or profile_id),
                    vendor=str(data.get("vendor") or ""),
                    description=str(data.get("description") or ""),
                    path=path,
                    values=values,
                )
            except Exception as exc:
                logger.warning("Ignoring invalid battery Modbus profile %s: %s", path, exc)
    return sorted(profiles.values(), key=lambda item: item.label.lower())


def get_battery_modbus_profile(profile_id: str) -> BatteryModbusProfile | None:
    wanted = _safe_profile_id(profile_id, "")
    if not wanted:
        return None
    for profile in iter_battery_modbus_profiles(include_hidden=False):
        if profile.id == wanted:
            return profile
    return None


def battery_modbus_profile_choices() -> list[dict[str, str]]:
    choices = [{"value": "", "label": "Manuell"}]
    choices.extend({"value": profile.id, "label": profile.label} for profile in iter_battery_modbus_profiles())
    return choices


def _normalize_register_values(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, Any] = {}
    address = raw.get("address")
    if address not in (None, ""):
        try:
            normalized["address"] = int(address)
        except Exception:
            pass

    register_type = str(raw.get("register_type", raw.get("register", "holding")) or "holding").strip().lower()
    if register_type in SUPPORTED_REGISTER_TYPES:
        normalized["register_type"] = register_type

    value_type = str(raw.get("type", raw.get("value_type", "uint16")) or "uint16").strip().lower()
    if value_type in SUPPORTED_VALUE_TYPES:
        normalized["value_type"] = value_type

    endian = str(raw.get("endian", "big_endian") or "big_endian").strip().lower()
    if endian in SUPPORTED_ENDIAN_TYPES:
        normalized["endian"] = endian

    factor = raw.get("factor")
    if factor not in (None, ""):
        try:
            normalized["factor"] = float(factor)
        except Exception:
            pass

    return normalized


def apply_battery_modbus_profile_values(settings: dict[str, Any], profile: BatteryModbusProfile) -> dict[str, Any]:
    values = dict(profile.values or {})

    for key in ("port", "unit_id", "poll_interval_ms"):
        if key in values and values[key] not in (None, ""):
            try:
                settings[key] = int(values[key])
            except Exception:
                settings[key] = values[key]

    if "timeout_ms" in values and values["timeout_ms"] not in (None, ""):
        try:
            settings["request_timeout_seconds"] = float(values["timeout_ms"]) / 1000.0
        except Exception:
            pass

    if "request_timeout_seconds" in values and values["request_timeout_seconds"] not in (None, ""):
        try:
            settings["request_timeout_seconds"] = float(values["request_timeout_seconds"])
        except Exception:
            settings["request_timeout_seconds"] = values["request_timeout_seconds"]

    for key in REGISTER_VALUE_KEYS:
        normalized = _normalize_register_values(values.get(key))
        if normalized:
            current = settings.get(key)
            if not isinstance(current, dict):
                current = {}
            merged = dict(current)
            merged.update(normalized)
            settings[key] = merged

    settings["modbus_profile"] = profile.id
    return settings


def apply_battery_modbus_profile(settings: dict[str, Any], profile_id: str) -> tuple[bool, str]:
    profile = get_battery_modbus_profile(profile_id)
    if profile is None:
        return False, "Modbus-Profil nicht gefunden."
    apply_battery_modbus_profile_values(settings, profile)
    return True, f"Modbus-Profil angewendet: {profile.label}"
