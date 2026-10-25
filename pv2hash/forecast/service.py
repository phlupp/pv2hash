"""Forecast service and read-only local actuals, no control-loop dependency."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import sqlite3
import math

from . import generators_from_config, forecast_config_fields, utc_iso
from .open_meteo import OpenMeteoDriver
from .schedule import FORECAST_REFRESH_MINUTES
from .store import ForecastStore
from .energy import energy_summary
from .shadow import signature as shadow_signature, plant_summary

DRIVERS = {"open_meteo": OpenMeteoDriver}
UPDATE_LOCK = asyncio.Lock()
FORECAST_DEFAULT = {
    "enabled": False, "driver": "open_meteo",
    "settings": {"mode": "free", "api_key": "", "refresh_minutes": FORECAST_REFRESH_MINUTES, "resolution_minutes": 15},
}


def settings_model(config: dict) -> dict:
    forecast = config.get("forecast") or FORECAST_DEFAULT
    settings = forecast.get("settings") or {}
    driver = str(forecast.get("driver", "open_meteo"))
    devices = (config.get("pv") or {}).get("devices") or []
    return {
        "enabled": bool(forecast.get("enabled")),
        "driver": driver,
        "drivers": [{"id": cls.driver_id, "name": cls.name}
                    for cls in DRIVERS.values()],
        "fields": forecast_config_fields(driver),
        "settings": {
            "mode": settings.get("mode", "free"),
            "refresh_minutes": FORECAST_REFRESH_MINUTES,  # legacy API compatibility; no longer configurable
            "resolution_minutes": settings.get("resolution_minutes", 15),
            "api_key_configured": bool(settings.get("api_key")),
        },
        "inverters": [
            {"id": str(dev.get("id")), "name": str(dev.get("name") or dev.get("id")),
             "generators": deepcopy(dev.get("generators") or [])}
            for dev in devices if isinstance(dev, dict)
        ],
        "location_ready": all(
            isinstance((config.get("location") or {}).get(x), (int,float))
            for x in ("lat","lon")
        ),
    }


def validate_generator_coverage(config: dict) -> None:
    """A total forecast must cover every configured physical inverter."""
    devices = (config.get("pv") or {}).get("devices") or []
    uncovered = [
        str(device.get("name") or device.get("id") or "Unbenannt")
        for device in devices if isinstance(device, dict)
        and not any(isinstance(g, dict) and g.get("enabled", True)
                    for g in (device.get("generators") or []))
    ]
    if uncovered:
        raise ValueError("Aktive Generatorflächen fehlen für: " + ", ".join(uncovered))


def apply_settings(config: dict, payload: dict) -> dict:
    """Validate and return a deep-copied config without touching miner setup."""
    if not isinstance(payload, dict):
        raise ValueError("Ungültige Prognoseeinstellungen.")
    new = deepcopy(config)
    current = new.get("forecast") or deepcopy(FORECAST_DEFAULT)
    driver = str(payload.get("driver", current.get("driver", "open_meteo")))
    if driver not in DRIVERS:
        raise ValueError("Nicht unterstützter Prognoseanbieter.")
    settings = deepcopy(current.get("settings") or {})
    incoming = payload.get("settings") or {}
    mode = str(incoming.get("mode", settings.get("mode", "free")))
    api_key = str(incoming.get("api_key") or settings.get("api_key") or "")
    # Prior versions persisted independent intervals. The automatic
    # scheduler now always issues every 15 minutes. Ignore an obsolete
    # refresh_minutes field from a cached older client; preserve other
    # settings and normalize the saved value on the next save.
    try:
        resolution = int(str(incoming.get(
            "resolution_minutes", settings.get("resolution_minutes", 15),
        )))
    except (ValueError, TypeError):
        raise ValueError("Prognoseauflösung muss 15 oder 60 Minuten betragen.") from None
    if resolution not in (15, 60):
        raise ValueError("Prognoseauflösung muss 15 oder 60 Minuten betragen.")
    DRIVERS[driver](mode=mode, api_key=api_key, resolution_minutes=resolution)
    new["forecast"] = {
        "enabled": bool(payload.get("enabled", current.get("enabled", False))),
        "driver": driver,
        "settings": {
            "mode": mode, "api_key": api_key,
            "refresh_minutes": FORECAST_REFRESH_MINUTES, "resolution_minutes": resolution,
        },
    }
    if "inverters" in payload:
        provided = payload["inverters"]
        if not isinstance(provided, list):
            raise ValueError("Wechselrichterliste ungültig.")
        by_id = {str(d["id"]): d for d in provided if isinstance(d, dict) and "id" in d}
        physical = new.setdefault("pv", {}).setdefault("devices", [])
        if set(by_id) != {str(d.get("id")) for d in physical}:
            raise ValueError("Wechselrichter-IDs entsprechen nicht der Konfiguration.")
        for device in physical:
            proposed = by_id[str(device["id"])].get("generators", [])
            if not isinstance(proposed, list):
                raise ValueError("Generatorflächen müssen eine Liste sein.")
            if len(proposed) > 16:
                raise ValueError("Maximal 16 Generatorflächen pro Wechselrichter.")
            device["generators"] = deepcopy(proposed)
    # A disabled forecast may be configured incrementally; generation values
    # are fully checked when activating the forecast or computing a refresh.
    if new["forecast"]["enabled"]:
        validate_generator_coverage(new)
        if not generators_from_config(new):
            raise ValueError("Vor Aktivierung mindestens eine PV-Generatorfläche einrichten.")
        loc = new.get("location") or {}
        if not isinstance(loc.get("lat"), (float,int)) or not isinstance(loc.get("lon"), (float,int)):
            raise ValueError("Für die Prognose muss ein Standort mit Koordinaten hinterlegt sein.")
    return new


async def update(config: dict, store: ForecastStore) -> dict:
    if not (config.get("forecast") or {}).get("enabled"):
        raise ValueError("Prognose ist deaktiviert.")
    validate_generator_coverage(config)
    group = generators_from_config(config)
    if not group:
        raise ValueError("Keine aktiven Generatorflächen eingerichtet.")
    fc = config["forecast"]
    driver_class = DRIVERS.get(fc.get("driver"))
    if driver_class is None:
        raise ValueError("Unbekannter Prognosetreiber.")
    settings = fc.get("settings") or {}
    driver = driver_class(
        mode=settings.get("mode", "free"), api_key=settings.get("api_key", ""),
        resolution_minutes=int(settings.get("resolution_minutes", 15)),
    )
    loc = config.get("location") or {}
    # A single completed provider round enters the store atomically:
    # no partial weather/WR result is ever persisted.
    async with UPDATE_LOCK:
        zone, individual_surfaces = await driver.fetch(float(loc["lat"]),float(loc["lon"]),group)
        result = store.save_future(
            now=datetime.now(UTC),provider=driver.driver_id,
            timezone=zone, surfaces=individual_surfaces,
            resolution_minutes=driver.resolution_minutes,
            shadow_config_hash=shadow_signature(
                group, loc, driver.driver_id, driver.resolution_minutes),
            shadow_config_summary=plant_summary(group),
        )
        return result


def actual_day_with_energy(start: str, end: str, db_path: Path = Path("data/history.sqlite")) -> tuple[list[dict], dict]:
    """Day chart and accurate sampled energy from the same history read.

    Only intervals with two consecutive valid LIVE readings count. A missing,
    DEGRADED, or SMA-NaN-contaminated sample breaks the integral. This avoids
    assigning five minutes' production to a five-minute bucket with one sample.
    """
    lower = datetime.fromisoformat(start.replace("Z", "+00:00"))
    upper = datetime.fromisoformat(end.replace("Z", "+00:00"))
    rows = []
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as con:
                rows = con.execute(
                    "SELECT ts,pv_power_w,pv_quality FROM history_samples WHERE ts>=? AND ts<? ORDER BY ts",
                    (start.replace("Z", "+00:00"), end.replace("Z", "+00:00")),
                ).fetchall()
        except sqlite3.Error:
            pass
    buckets: dict[int, list[float]] = {}
    # Mark broken intervals by splitting into contiguous valid runs. No joins
    # across partial sources, bad SMA sentinels, or raw history gaps.
    runs: list[list[tuple[datetime, float]]] = []
    current_run: list[tuple[datetime, float]] = []
    for time_str, power, quality in rows:
        try:
            instant = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            measured = float(power) if power is not None else float("nan")
            valid = (quality == "live" and math.isfinite(measured)
                     and measured >= -1_000_000)
        except (TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            if current_run:
                runs.append(current_run)
                current_run = []
            continue
        epoch = int(instant.timestamp())
        buckets.setdefault(epoch // 300 * 300, []).append(measured)
        current_run.append((instant, measured))
    if current_run:
        runs.append(current_run)
    # Never join distinct good runs across invalid/degraded samples.
    energy_parts = [energy_summary(run, start=lower, end=upper,
                                   max_gap_seconds=35) for run in runs]
    day_hours = (upper - lower).total_seconds() / 3600
    coverage = sum(part["coverage_hours"] for part in energy_parts)
    total = sum(part["kwh"] for part in energy_parts if part["kwh"] is not None)
    energy = {"kwh": round(total, 6) if coverage else None,
              "coverage_hours": round(coverage, 6),
              "day_hours": round(day_hours, 3),
              "complete": coverage >= day_hours - 0.05}
    actual = [
        {"ts": utc_iso(datetime.fromtimestamp(t, tz=UTC)),
         "power_w": round(sum(values) / len(values), 2)}
        for t, values in sorted(buckets.items())
    ]
    return actual, energy


def actual_day(start: str, end: str, db_path: Path = Path("data/history.sqlite")) -> list[dict]:
    """Backward-compatible read-only chart API for live PV power samples."""
    return actual_day_with_energy(start, end, db_path)[0]
