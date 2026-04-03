import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from pv2hash.config.defaults import DEFAULT_CONFIG

CONFIG_PATH = Path("data/config.json")
PROFILE_NAMES = ("floor", "eco", "mid", "high")
FALLBACK_PROFILE_NAMES = ("floor", "eco", "mid", "high")


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


def _normalize_miner_profiles(config: dict[str, Any]) -> None:
    for miner in config.get("miners", []):
        profiles = miner.setdefault("profiles", {})

        if "floor" not in profiles and "off" in profiles:
            profiles["floor"] = deepcopy(profiles["off"])

        profiles.pop("off", None)

        default_profiles = DEFAULT_CONFIG["miners"][0]["profiles"]
        for name in PROFILE_NAMES:
            if name not in profiles:
                profiles[name] = deepcopy(default_profiles[name])


def _normalize_source_loss_profiles(config: dict[str, Any]) -> None:
    source_loss = config.setdefault("control", {}).setdefault("source_loss", {})

    for quality in ("stale", "offline"):
        behavior = source_loss.setdefault(quality, {})
        fallback_profile = str(behavior.get("fallback_profile", "eco")).strip().lower()

        if fallback_profile == "off":
            fallback_profile = "floor"

        if fallback_profile not in FALLBACK_PROFILE_NAMES:
            fallback_profile = "eco"

        behavior["fallback_profile"] = fallback_profile


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(config)
    _normalize_miner_profiles(normalized)
    _normalize_source_loss_profiles(normalized)
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
    return normalize_config(merged)


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
