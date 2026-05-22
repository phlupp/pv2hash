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
DATALOGGER_PORTAL_LIMIT = 120
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


def _connect_logger_db() -> sqlite3.Connection:
    LOGGER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(LOGGER_DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=3000")
    return con


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return set()
    return {str(row[1]) for row in rows}


def _ensure_datalogger_portal_schema(con: sqlite3.Connection) -> None:
    columns = _table_columns(con, "history_samples")
    if not columns:
        return
    if "portal_sent_at" not in columns:
        con.execute("ALTER TABLE history_samples ADD COLUMN portal_sent_at TEXT")
    if "upload_attempts" not in columns:
        con.execute("ALTER TABLE history_samples ADD COLUMN upload_attempts INTEGER NOT NULL DEFAULT 0")
    if "last_upload_error" not in columns:
        con.execute("ALTER TABLE history_samples ADD COLUMN last_upload_error TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_history_samples_portal ON history_samples(portal_sent_at, ts)")


def _row_to_portal_sample(row: sqlite3.Row, miners_by_ts: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    ts = row["ts"]
    return {
        "sample_id": ts,
        "ts": ts,
        "instance_id": row["instance_id"],
        "grid_power_w": row["grid_power_w"],
        "source_quality": row["source_quality"],
        "battery_quality": row["battery_quality"],
        "battery_soc_pct": _valid_battery_soc(row["battery_soc_pct"]),
        "battery_charge_power_w": row["battery_charge_power_w"],
        "battery_discharge_power_w": row["battery_discharge_power_w"],
        "battery_is_charging": bool(row["battery_is_charging"]),
        "battery_is_discharging": bool(row["battery_is_discharging"]),
        "miner_power_w_total": row["miner_power_w_total"],
        "miner_hashrate_ghs_total": row["miner_hashrate_ghs_total"],
        "control_enabled_miner_count": row["control_enabled_miner_count"],
        "monitor_enabled_miner_count": row["monitor_enabled_miner_count"],
        "reachable_miner_count": row["reachable_miner_count"],
        "controller_summary": row["controller_summary"],
        "controller_last_decision": row["controller_last_decision"],
        "host_cpu_percent": row["host_cpu_percent"],
        "host_memory_percent": row["host_memory_percent"],
        "host_disk_percent": row["host_disk_percent"],
        "host_uptime_seconds": row["host_uptime_seconds"],
        "miners": miners_by_ts.get(str(ts), []),
    }


def _unsent_datalogger_samples_for_portal(limit: int = DATALOGGER_PORTAL_LIMIT) -> list[dict[str, Any]]:
    limit = max(1, min(500, int(limit or DATALOGGER_PORTAL_LIMIT)))
    if not LOGGER_DB_PATH.exists():
        return []
    with _connect_logger_db() as con:
        con.row_factory = sqlite3.Row
        _ensure_datalogger_portal_schema(con)
        if "portal_sent_at" not in _table_columns(con, "history_samples"):
            return []
        rows = con.execute(
            """
            SELECT *
            FROM history_samples
            WHERE portal_sent_at IS NULL
            ORDER BY ts ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        if not rows:
            return []
        timestamps = [str(row["ts"]) for row in rows]
        placeholders = ",".join("?" for _ in timestamps)
        miner_rows = con.execute(
            f"""
            SELECT *
            FROM history_miner_samples
            WHERE ts IN ({placeholders})
            ORDER BY ts ASC, miner_id ASC
            """,
            timestamps,
        ).fetchall()
    miners_by_ts: dict[str, list[dict[str, Any]]] = {}
    for miner in miner_rows:
        ts = str(miner["ts"])
        miners_by_ts.setdefault(ts, []).append({
            "id": miner["miner_id"],
            "key": miner["miner_key"],
            "name": miner["name"],
            "driver": miner["driver"],
            "profile": miner["profile"],
            "power_w": miner["power_w"],
            "hashrate_ghs": miner["hashrate_ghs"],
            "temp_c": miner["temp_c"] if "temp_c" in miner.keys() else None,
            "temp_asic_min_c": miner["temp_asic_min_c"] if "temp_asic_min_c" in miner.keys() else None,
            "temp_asic_max_c": miner["temp_asic_max_c"] if "temp_asic_max_c" in miner.keys() else None,
            "reachable": bool(miner["reachable"]),
            "monitor_enabled": bool(miner["monitor_enabled"]),
            "control_enabled": bool(miner["control_enabled"]),
            "runtime_state": miner["runtime_state"],
        })
    return [_row_to_portal_sample(row, miners_by_ts) for row in rows]


def _mark_datalogger_samples_uploaded(sample_ids: list[str]) -> None:
    ids = [str(item) for item in sample_ids if item]
    if not ids:
        return
    now = datetime.now(UTC).isoformat()
    with _connect_logger_db() as con:
        _ensure_datalogger_portal_schema(con)
        con.executemany(
            "UPDATE history_samples SET portal_sent_at = ?, last_upload_error = NULL WHERE ts = ?",
            [(now, item) for item in ids],
        )


def _mark_datalogger_samples_failed(sample_ids: list[str], error: str) -> None:
    ids = [str(item) for item in sample_ids if item]
    if not ids:
        return
    message = str(error or "Portal upload failed")[:500]
    with _connect_logger_db() as con:
        _ensure_datalogger_portal_schema(con)
        con.executemany(
            "UPDATE history_samples SET upload_attempts = upload_attempts + 1, last_upload_error = ? WHERE ts = ?",
            [(message, item) for item in ids],
        )


def _datalogger_has_more_unsent(limit: int = DATALOGGER_PORTAL_LIMIT) -> bool:
    if not LOGGER_DB_PATH.exists():
        return False
    with _connect_logger_db() as con:
        _ensure_datalogger_portal_schema(con)
        if "portal_sent_at" not in _table_columns(con, "history_samples"):
            return False
        count = con.execute(
            "SELECT COUNT(*) FROM history_samples WHERE portal_sent_at IS NULL"
        ).fetchone()[0]
    return int(count or 0) > int(limit or DATALOGGER_PORTAL_LIMIT)


def _clean_datalogger_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    cleaned = deepcopy(snapshot)
    battery = cleaned.get("battery")
    if isinstance(battery, dict):
        battery["soc_pct"] = _valid_battery_soc(battery.get("soc_pct"))
    return cleaned


def _patch_datalogger() -> None:
    try:
        from pv2hash.datalogger import DataLogger
    except Exception:
        return
    if getattr(DataLogger, "_pv2hash_portal_queue_patched", False):
        return

    original_ensure_schema = DataLogger._ensure_schema
    original_write_snapshot = DataLogger._write_snapshot
    original_record_controller_event = DataLogger.record_controller_event
    original_record_controller_debug_event = DataLogger.record_controller_debug_event
    original_series = DataLogger.series

    def ensure_schema(self):
        result = original_ensure_schema(self)
        with self._connect() as con:
            _ensure_datalogger_portal_schema(con)
        return result

    def write_snapshot(self, snapshot, cfg):
        return original_write_snapshot(self, _clean_datalogger_snapshot(snapshot), cfg)

    def record_controller_event(self, event):
        event = dict(event or {})
        event["battery_soc_pct"] = _valid_battery_soc(event.get("battery_soc_pct"))
        return original_record_controller_event(self, event)

    def record_controller_debug_event(self, event):
        event = dict(event or {})
        event["battery_soc_pct"] = _valid_battery_soc(event.get("battery_soc_pct"))
        return original_record_controller_debug_event(self, event)

    def series(self, *args, **kwargs):
        payload = original_series(self, *args, **kwargs)
        for point in payload.get("points") or []:
            if isinstance(point, dict):
                point["battery_soc_pct"] = _valid_battery_soc(point.get("battery_soc_pct"))
        return payload

    DataLogger._ensure_schema = ensure_schema
    DataLogger._write_snapshot = write_snapshot
    DataLogger.record_controller_event = record_controller_event
    DataLogger.record_controller_debug_event = record_controller_debug_event
    DataLogger.series = series
    DataLogger.unsent_samples_for_portal = staticmethod(_unsent_datalogger_samples_for_portal)
    DataLogger.mark_samples_uploaded = staticmethod(_mark_datalogger_samples_uploaded)
    DataLogger.mark_samples_upload_failed = staticmethod(_mark_datalogger_samples_failed)
    DataLogger._pv2hash_portal_queue_patched = True


def _portal_location_from_config(config: dict[str, Any] | None) -> dict[str, Any]:
    portal = (config or {}).get("portal", {}) if isinstance(config, dict) else {}
    location = portal.get("location", {}) if isinstance(portal, dict) else {}
    if not isinstance(location, dict):
        location = {}
    lat = _float_or_none(location.get("lat"))
    lon = _float_or_none(location.get("lon"))
    if lat is not None and not (-90.0 <= lat <= 90.0):
        lat = None
    if lon is not None and not (-180.0 <= lon <= 180.0):
        lon = None
    return {
        "address": str(location.get("address") or "").strip(),
        "lat": lat,
        "lon": lon,
        "has_coordinates": lat is not None and lon is not None,
        "source": str(location.get("source") or "manual").strip() or "manual",
    }


def _sample_ids_for_upload_marking(samples: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for item in samples:
        if not isinstance(item, dict):
            continue
        raw_ids = item.get("sample_ids")
        if isinstance(raw_ids, list):
            ids.extend(str(raw_id) for raw_id in raw_ids if raw_id)
        elif item.get("sample_id"):
            ids.append(str(item.get("sample_id")))
    return ids


def _patch_portal_send_snapshot() -> None:
    try:
        import pv2hash.portal as portal_mod
    except Exception:
        return
    if getattr(portal_mod, "_pv2hash_datalogger_payload_patched", False):
        return
    original_send_snapshot = portal_mod.send_snapshot

    def send_snapshot_with_datalogger(base_url, api_token, payload, *args, **kwargs):
        payload = _sanitize_snapshot_payload(payload if isinstance(payload, dict) else {})
        samples = _unsent_datalogger_samples_for_portal(DATALOGGER_PORTAL_LIMIT)
        sample_ids = _sample_ids_for_upload_marking(samples)
        payload["datalogger"] = {
            "schema_version": 1,
            "upload_mode": "queued",
            "sample_count": len(samples),
            "has_more": _datalogger_has_more_unsent(DATALOGGER_PORTAL_LIMIT),
            "samples": samples,
        }
        try:
            response = original_send_snapshot(base_url, api_token, payload, *args, **kwargs)
        except Exception as exc:
            _mark_datalogger_samples_failed(sample_ids, str(exc))
            raise
        _mark_datalogger_samples_uploaded(sample_ids)
        return response

    portal_mod.send_snapshot = send_snapshot_with_datalogger
    portal_mod._pv2hash_datalogger_payload_patched = True


def _geocode_address(address: str) -> dict[str, Any]:
    address = str(address or "").strip()
    if not address:
        raise ValueError("Adresse fehlt.")
    query = urllib.parse.urlencode({"q": address, "format": "jsonv2", "limit": "1", "addressdetails": "0"})
    url = f"https://nominatim.openstreetmap.org/search?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "PV2Hash local instance geocoder (https://pv2hash.xyz)",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        raise ValueError("Keine Koordinaten gefunden.")
    hit = data[0]
    lat = _float_or_none(hit.get("lat"))
    lon = _float_or_none(hit.get("lon"))
    if lat is None or lon is None:
        raise ValueError("Geocoder hat keine gültigen Koordinaten geliefert.")
    return {
        "address": address,
        "display_name": str(hit.get("display_name") or address),
        "lat": lat,
        "lon": lon,
        "source": "nominatim",
    }


def _patch_app_module(app_mod: Any) -> None:
    if getattr(app_mod, "_pv2hash_local_instance_patched", False):
        return
    _patch_datalogger()
    _patch_portal_send_snapshot()

    original_merge_battery_snapshot = app_mod._merge_battery_snapshot
    original_build_runtime_snapshot_payload = app_mod._build_runtime_snapshot_payload
    original_build_portal_instance_payload = app_mod._build_portal_instance_payload
    original_build_settings_model = app_mod._build_settings_model
    original_apply_settings_payload = app_mod._apply_settings_payload

    def merge_battery_snapshot(main_snapshot, battery_snapshot):
        return _sanitize_snapshot_obj(original_merge_battery_snapshot(main_snapshot, battery_snapshot))

    def build_runtime_snapshot_payload():
        payload = _sanitize_snapshot_payload(original_build_runtime_snapshot_payload())
        location = _portal_location_from_config(getattr(app_mod.state, "config", {}))
        instance = payload.setdefault("instance", {})
        instance["location"] = location
        return payload

    def build_portal_instance_payload():
        payload = original_build_portal_instance_payload()
        payload["location"] = _portal_location_from_config(getattr(app_mod.state, "config", {}))
        return payload

    def build_settings_model():
        model = original_build_settings_model()
        portal = app_mod._portal_config()
        location = _portal_location_from_config(getattr(app_mod.state, "config", {}))
        sections = model.setdefault("sections", [])
        sections.append({
            "id": "portal-location",
            "title": "Portal-Standort",
            "subtitle": "Optionaler Standort der lokalen Instanz. Koordinaten werden nur bei aktiver Portal-Synchronisierung übertragen.",
            "fields": [
                app_mod._setting_field("portal_location_address", "Standort-Adresse", "text", location.get("address", ""), help="Adresse wird lokal gespeichert. Koordinaten können manuell eingetragen oder per API ermittelt werden.", layout={"width": "full"}),
                app_mod._setting_field("portal_location_lat", "Latitude", "number", location.get("lat"), min=-90, max=90, step="0.000001", layout={"width": "half"}),
                app_mod._setting_field("portal_location_lon", "Longitude", "number", location.get("lon"), min=-180, max=180, step="0.000001", layout={"width": "half"}),
            ],
        })
        return model

    def apply_settings_payload(payload):
        original_apply_settings_payload(payload)
        portal = app_mod._portal_config()
        location = portal.setdefault("location", {})
        location["address"] = str(payload.get("portal_location_address") or "").strip()
        location["lat"] = _float_or_none(payload.get("portal_location_lat"))
        location["lon"] = _float_or_none(payload.get("portal_location_lon"))
        location["source"] = "manual"

    app_mod._merge_battery_snapshot = merge_battery_snapshot
    app_mod._build_runtime_snapshot_payload = build_runtime_snapshot_payload
    app_mod._build_portal_instance_payload = build_portal_instance_payload
    app_mod._build_settings_model = build_settings_model
    app_mod._apply_settings_payload = apply_settings_payload

    @app_mod.app.post("/api/portal/location/geocode")
    async def api_portal_location_geocode(request: app_mod.Request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        portal = app_mod._portal_config()
        current_location = portal.get("location", {}) if isinstance(portal.get("location"), dict) else {}
        address = str(payload.get("address") or current_location.get("address") or "").strip()
        save = bool(payload.get("save", False))
        try:
            result = await app_mod.asyncio.to_thread(_geocode_address, address)
        except Exception as exc:
            return app_mod.JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
        if save:
            portal["location"] = {
                "address": address,
                "lat": result["lat"],
                "lon": result["lon"],
                "source": result.get("source") or "nominatim",
            }
            app_mod.save_config(app_mod.state.config)
        return app_mod.JSONResponse(app_mod.jsonable_encoder({"status": "ok", "location": result, "model": app_mod._build_settings_model()}))

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
    _patch_datalogger()
    _patch_portal_send_snapshot()
    _install_app_import_hook()
