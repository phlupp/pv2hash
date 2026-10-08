from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import json
import math
import sqlite3
import sys
import urllib.parse
import urllib.request
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER_DB_PATH = Path("data/history.sqlite")
_INSTALLED = False
_APP_HOOK_INSTALLED = False


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _valid_battery_soc(value: Any) -> float | None:
    number = _float_or_none(value)
    if number is None:
        return None
    if number < 0.0 or number > 150.0:
        return None
    return number


def _sanitize_snapshot_obj(snapshot: Any) -> Any:
    soc = _valid_battery_soc(getattr(snapshot, "battery_soc_pct", None))
    if soc == getattr(snapshot, "battery_soc_pct", None):
        return snapshot
    try:
        return replace(snapshot, battery_soc_pct=soc)
    except Exception:
        try:
            snapshot.battery_soc_pct = soc
        except Exception:
            pass
        return snapshot


def _sanitize_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    battery = payload.get("battery")
    if isinstance(battery, dict):
        battery["soc_pct"] = _valid_battery_soc(battery.get("soc_pct"))
    totals = payload.get("totals")
    if isinstance(totals, dict):
        totals["battery_soc"] = _valid_battery_soc(totals.get("battery_soc"))
    controller = payload.get("controller")
    if isinstance(controller, dict):
        event = controller.get("last_decision_event")
        if isinstance(event, dict):
            event["battery_soc_pct"] = _valid_battery_soc(event.get("battery_soc_pct"))
        events = controller.get("decision_events")
        if isinstance(events, list):
            for item in events:
                if isinstance(item, dict):
                    item["battery_soc_pct"] = _valid_battery_soc(item.get("battery_soc_pct"))
    return payload


def _patch_app_module(app_mod: Any) -> None:
    if getattr(app_mod, "_pv2hash_local_instance_patched", False):
        return
    original_merge = app_mod._merge_battery_snapshot
    def merge_battery_snapshot(main_snapshot, battery_snapshot):
        return _sanitize_snapshot_obj(original_merge(main_snapshot, battery_snapshot))
    app_mod._merge_battery_snapshot = merge_battery_snapshot
    app_mod._pv2hash_local_instance_patched = True


class _AppPatchLoader(importlib.abc.Loader):
    def __init__(self, original_loader: importlib.abc.Loader) -> None:
        self.original_loader = original_loader

    def create_module(self, spec):
        create_module = getattr(self.original_loader, "create_module", None)
        if create_module:
            return create_module(spec)
        return None

    def exec_module(self, module):
        self.original_loader.exec_module(module)
        _patch_app_module(module)


class _AppPatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "pv2hash.app":
            return None
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None:
            return spec
        spec.loader = _AppPatchLoader(spec.loader)
        return spec


def _install_app_import_hook() -> None:
    global _APP_HOOK_INSTALLED
    if _APP_HOOK_INSTALLED:
        return
    if "pv2hash.app" in sys.modules:
        _patch_app_module(sys.modules["pv2hash.app"])
        _APP_HOOK_INSTALLED = True
        return
    sys.meta_path.insert(0, _AppPatchFinder())
    _APP_HOOK_INSTALLED = True


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    _install_app_import_hook()
