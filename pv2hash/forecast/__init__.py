"""Provider-independent PV forecast model. NO access to the mining controller."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Generator:
    inverter_id: str
    inverter_name: str
    generator_id: str
    name: str
    kwp: float
    tilt: float
    azimuth: float
    loss_percent: float


def generators_from_config(config: dict) -> list[Generator]:
    """Inverter -> multiple independent physical generator surfaces."""
    devices = (config.get("pv") or {}).get("devices") or []
    generators: list[Generator] = []
    seen: set[str] = set()
    for device in devices:
        if not isinstance(device, dict):
            continue
        # Only physical installed plant settings count, even if an inverter is
        # temporarily offline or its telemetry is disabled.
        inverter_id = str(device.get("id") or "")
        if not inverter_id:
            raise ValueError("PV-Wechselrichter benötigt eine eindeutige ID.")
        for surface in device.get("generators") or []:
            if not isinstance(surface, dict) or not surface.get("enabled", True):
                continue
            surface_id = str(surface.get("id") or "")
            if not surface_id or (inverter_id + "/" + surface_id) in seen:
                raise ValueError("PV-Generatorfläche benötigt eine eindeutige ID.")
            seen.add(inverter_id + "/" + surface_id)
            kwp = float(surface.get("kwp", 0))
            tilt = float(surface.get("tilt", -1))
            azimuth = float(surface.get("azimuth", 181))
            loss = float(surface.get("loss_percent", 14))
            if not all(math.isfinite(v) for v in (kwp, tilt, azimuth, loss)):
                raise ValueError("PV-Generatorparameter müssen endlich sein.")
            if not 0 < kwp <= 100_000 or not 0 <= tilt <= 90 or not -180 <= azimuth <= 180 or not 0 <= loss < 100:
                raise ValueError("PV-Generator: ungültige kWp/Neigung/Ausrichtung/Verlustangabe.")
            generators.append(Generator(
                inverter_id=inverter_id,
                inverter_name=str(device.get("name") or inverter_id),
                generator_id=surface_id,
                name=str(surface.get("name") or surface_id),
                kwp=kwp, tilt=tilt, azimuth=azimuth, loss_percent=loss,
            ))
    if len(generators) > 64:
        raise ValueError("Maximal 64 aktive Generatorflächen pro Standort.")
    return generators


def estimate_power_w(generator: Generator, irradiance_w_m2: float) -> float:
    """Deliberately simple first-pass DC→AC approximation, not calibrated."""
    if not math.isfinite(irradiance_w_m2):
        raise ValueError("Ungültiger Einstrahlungswert")
    dc_w = generator.kwp * 1000
    return round(min(dc_w, max(0.0, irradiance_w_m2) / 1000 * dc_w * (1 - generator.loss_percent / 100)), 2)


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Naive Zeitstempel sind für Prognosen verboten.")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def forecast_config_fields(provider: str) -> list[dict[str, Any]]:
    from .open_meteo import OpenMeteoDriver
    if provider != OpenMeteoDriver.driver_id:
        raise ValueError("Unbekannter Prognose-Treiber.")
    return OpenMeteoDriver.config_fields()
