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

_logged_invalid_profiles: set[str] = set()
_last_profile_warnings: list[str] = []


@dataclass(frozen=True, slots=True)
class BatteryModbusProfile:
    id: str
    key: str
    name: str
    vendor: str = ""
    description: str = ""
    path: Path | None = None
    values: dict[str, Any] | None = None
    source: str = "builtin"
    is_custom: bool = False
    duplicate_id: bool = False

    @property
    def label(self) -> str:
        parts: list[str] = []
        if self.vendor:
            parts.append(f"{self.vendor} – {self.name}")
        else:
            parts.append(self.name)
        if self.is_custom:
            parts.append("(Custom)")
        if self.duplicate_id:
            parts.append(f"[ID: {self.id}]")
        return " ".join(parts)


def _profile_dirs() -> list[tuple[str, Path]]:
    dirs: list[tuple[str, Path]] = [("builtin", PACKAGE_PROFILE_DIR)]
    if USER_PROFILE_DIR != PACKAGE_PROFILE_DIR:
        dirs.append(("custom", USER_PROFILE_DIR))
    return dirs


def _safe_profile_id(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in raw)
    return safe or fallback


def _safe_key_part(value: Any, fallback: str = "") -> str:
    return _safe_profile_id(value, fallback).replace(":", "_")


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


def _warn_invalid_profile(path: Path, exc: Exception) -> str:
    message = f"Ungültiges Modbus-Batterieprofil ignoriert: {path} ({exc})"
    key = f"{path}:{type(exc).__name__}:{exc}"
    if key not in _logged_invalid_profiles:
        _logged_invalid_profiles.add(key)
        logger.warning("Ignoring invalid battery Modbus profile %s: %s", path, exc)
    return message


def _profile_key(source: str, path: Path, profile_id: str) -> str:
    return f"{source}:{_safe_key_part(path.stem, 'profile')}:{_safe_key_part(profile_id, 'profile')}"


def _load_profiles(*, include_hidden: bool = False) -> tuple[list[BatteryModbusProfile], list[str]]:
    loaded: list[BatteryModbusProfile] = []
    warnings: list[str] = []

    for source, directory in _profile_dirs():
        if not directory.exists() or not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml"), key=lambda item: item.name.lower()):
            try:
                data = _load_profile_file(path)
                if bool(data.get("hidden", False)) and not include_hidden:
                    continue
                profile_id = _safe_profile_id(data.get("id"), path.stem)
                values = data.get("values") or {}
                if not isinstance(values, dict):
                    raise ValueError("profile values must be a YAML mapping")
                loaded.append(BatteryModbusProfile(
                    id=profile_id,
                    key=_profile_key(source, path, profile_id),
                    name=str(data.get("name") or profile_id),
                    vendor=str(data.get("vendor") or ""),
                    description=str(data.get("description") or ""),
                    path=path,
                    values=values,
                    source=source,
                    is_custom=source == "custom",
                ))
            except Exception as exc:
                warnings.append(_warn_invalid_profile(path, exc))

    id_counts: dict[str, int] = {}
    for profile in loaded:
        id_counts[profile.id] = id_counts.get(profile.id, 0) + 1

    profiles = [
        BatteryModbusProfile(
            id=profile.id,
            key=profile.key,
            name=profile.name,
            vendor=profile.vendor,
            description=profile.description,
            path=profile.path,
            values=profile.values,
            source=profile.source,
            is_custom=profile.is_custom,
            duplicate_id=id_counts.get(profile.id, 0) > 1,
        )
        for profile in loaded
    ]
    profiles.sort(key=lambda item: item.label.lower())
    return profiles, warnings


def iter_battery_modbus_profiles(*, include_hidden: bool = False) -> list[BatteryModbusProfile]:
    global _last_profile_warnings
    profiles, warnings = _load_profiles(include_hidden=include_hidden)
    _last_profile_warnings = warnings
    return profiles


def battery_modbus_profile_warnings() -> list[str]:
    global _last_profile_warnings
    _profiles, warnings = _load_profiles(include_hidden=False)
    _last_profile_warnings = warnings
    return list(warnings)


def get_battery_modbus_profile(profile_key_or_id: str) -> BatteryModbusProfile | None:
    wanted = str(profile_key_or_id or "").strip()
    if not wanted:
        return None
    safe_wanted = _safe_profile_id(wanted, "")
    profiles = iter_battery_modbus_profiles(include_hidden=False)

    for profile in profiles:
        if profile.key == wanted:
            return profile

    # Backward compatibility: older saved configs stored only the profile id.
    for profile in profiles:
        if profile.id == safe_wanted:
            return profile
    return None


def battery_modbus_profile_choices() -> list[dict[str, str]]:
    choices = [{"value": "", "label": "Manuell"}]
    choices.extend({"value": profile.key, "label": profile.label} for profile in iter_battery_modbus_profiles())
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

    settings["modbus_profile"] = profile.key
    return settings


def apply_battery_modbus_profile(settings: dict[str, Any], profile_key_or_id: str) -> tuple[bool, str]:
    profile = get_battery_modbus_profile(profile_key_or_id)
    if profile is None:
        return False, "Modbus-Profil nicht gefunden."
    apply_battery_modbus_profile_values(settings, profile)
    return True, f"Modbus-Profil angewendet: {profile.label}"
