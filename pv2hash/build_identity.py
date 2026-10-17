"""Immutable identity of the running Core/GUI release.

Built release archives include pv2hash/build_info.json. This file is the
authoritative identity for both the API and the InfluxDB exporter, even if
/etc/pv2hash/install.env is absent or contains legacy version keys.
"""
from __future__ import annotations

import json
from pathlib import Path

from pv2hash.version import APP_VERSION_FULL

BUILD_INFO_FILE = Path(__file__).resolve().parent / "build_info.json"


def read_build_identity(path: Path | None = None) -> dict[str, str | None]:
    """Read the installed package identity without inspecting mutable settings.

    Development / legacy source checkouts without a build_info.json retain
    their Core version and report no unverified release or GUI identity.
    """
    fallback: dict[str, str | None] = {
        "release_id": None,
        "core_version": APP_VERSION_FULL,
        "gui_version": None,
    }
    try:
        payload = json.loads((path if path is not None else BUILD_INFO_FILE).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return fallback
        release_id = str(payload.get("release_id") or "").strip()
        core_version = str(payload.get("core_version") or "").strip()
        gui_version = str(payload.get("gui_version") or "").strip()
        return {
            "release_id": release_id or None,
            "core_version": core_version or APP_VERSION_FULL,
            "gui_version": gui_version or None,
        }
    except (OSError, ValueError, TypeError):
        return fallback
