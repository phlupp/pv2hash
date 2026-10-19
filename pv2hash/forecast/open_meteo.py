"""Open-Meteo forecast driver: one GTI curve for each generator orientation."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import math

import httpx

from . import Generator, estimate_power_w, utc_iso


class OpenMeteoDriver:
    driver_id = "open_meteo"
    name = "Open-Meteo"
    FREE_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
    CUSTOMER_ENDPOINT = "https://customer-api.open-meteo.com/v1/forecast"

    @staticmethod
    def config_fields() -> list[dict]:
        return [
            {"name": "mode", "label": "Zugang", "type": "select",
             "options": [
                 {"value": "free", "label": "Kostenlos (kein API-Key)"},
                 {"value": "customer", "label": "Kunden-API (API-Key)"},
             ]},
            {"name": "api_key", "label": "API-Key (nur Kunden-API)",
             "type": "password", "optional": True,
             "visible_when": {"name": "mode", "value": "customer"}},
            {"name": "resolution_minutes", "label": "Prognoseauflösung",
             "type": "select", "options": [
                 {"value": "15", "label": "15 Minuten (96 Werte/Tag)"},
                 {"value": "60", "label": "60 Minuten (24 Werte/Tag)"},
             ]},
            {"name": "refresh_minutes", "label": "Abrufintervall (Minuten)",
             "type": "number", "min": 15, "max": 360},
        ]

    def __init__(self, *, mode: str = "free", api_key: str = "",
                 resolution_minutes: int = 15):
        if mode not in ("free", "customer"):
            raise ValueError("Open-Meteo Zugangsart ungültig.")
        if mode == "customer" and not api_key:
            raise ValueError("Open-Meteo Kunden-API benötigt einen Schlüssel.")
        if resolution_minutes not in (15, 60):
            raise ValueError("Open-Meteo-Prognoseauflösung muss 15 oder 60 Minuten betragen.")
        self.resolution_minutes = resolution_minutes
        self.mode = mode
        self._api_key = api_key
        self.endpoint = self.CUSTOMER_ENDPOINT if mode == "customer" else self.FREE_ENDPOINT

    async def fetch(self, latitude: float, longitude: float, generators: list[Generator]) -> tuple[str, dict[str, dict[str, float]]]:
        """Return individual surface forecasts indexed by real UTC instants."""
        if not generators:
            raise ValueError("Mindestens eine PV-Generatorfläche ist erforderlich.")
        if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("Ungültige Standortkoordinaten.")
        async with httpx.AsyncClient(timeout=12.0) as client:
            results = await asyncio.gather(*(self._fetch_one(client, latitude, longitude, gen) for gen in generators))
        zones = {zone for zone, _ in results}
        if len(zones) != 1:
            raise ValueError("Inkonsistente Zeitzone des Wetteranbieters.")
        return zones.pop(), {
            gen.inverter_id + "/" + gen.generator_id: points
            for gen, (_, points) in zip(generators, results)
        }

    async def _fetch_one(self, client: httpx.AsyncClient, lat: float, lon: float, gen: Generator) -> tuple[str, dict[str, float]]:
        # Use native provider time series. Never invent quarter-hour values by
        # interpolating an hourly forecast in PV2Hash.
        series = "minutely_15" if self.resolution_minutes == 15 else "hourly"
        params = {
            "latitude": lat, "longitude": lon,
            series: "global_tilted_irradiance_instant",
            "tilt": gen.tilt, "azimuth": gen.azimuth,
            "timezone": "auto", "timeformat": "unixtime",
            "forecast_days": 3,
        }
        if self.mode == "customer":
            params["apikey"] = self._api_key
        # Never log full request URLs: they contain credentials on the paid tier.
        try:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Open-Meteo-Abfrage fehlgeschlagen (Netzwerk/API).") from None
        if not isinstance(payload, dict) or not isinstance(payload.get(series), dict):
            raise RuntimeError("Open-Meteo lieferte keine Prognose in der gewählten Auflösung.")
        series_data = payload[series]
        times = series_data.get("time") or []
        radiation = series_data.get("global_tilted_irradiance_instant") or []
        if not times or len(times) != len(radiation):
            raise RuntimeError("Open-Meteo lieferte unvollständige Einstrahlungsdaten.")
        try:
            timezone = str(payload["timezone"])
            step = self.resolution_minutes * 60
            if any(not isinstance(t, int) or t % step != 0 for t in times):
                raise ValueError("Open-Meteo-Zeitpunkte passen nicht zur Prognoseauflösung.")
            if len(times) != len(set(times)) or times != sorted(times):
                raise ValueError("Open-Meteo-Zeitpunkte sind nicht eindeutig und aufsteigend.")
            points = {
                utc_iso(datetime.fromtimestamp(ts, tz=UTC)): estimate_power_w(gen, float(irr))
                for ts, irr in zip(times, radiation) if irr is not None
            }
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RuntimeError("Open-Meteo-Datenformat ungültig.") from None
        if not points:
            raise RuntimeError("Keine verwertbaren Open-Meteo-Prognosepunkte.")
        return timezone, points
