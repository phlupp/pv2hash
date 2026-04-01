import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from pv2hash.config.defaults import DEFAULT_CONFIG


CONFIG_PATH = Path("data/config.json")


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


def ensure_config_exists() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)


def load_config() -> dict[str, Any]:
    ensure_config_exists()

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        user_config = json.load(f)

    return deep_merge(DEFAULT_CONFIG, user_config)


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def update_config(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_config()
    merged = deep_merge(current, patch)
    save_config(merged)
    return merged
