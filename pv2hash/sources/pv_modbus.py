"""Optional read-only PV generation measurement via one Modbus/TCP value.

This adapter never feeds the mining controller. The configured *measurement*
has one starting register address. 32-bit values span two consecutive registers.
"""
from __future__ import annotations

import asyncio
import math
import socket
from datetime import UTC, datetime

from pv2hash.logging_ext.setup import get_logger
from pv2hash.sources.base import EnergySource
from pv2hash.sources.battery_modbus import ModbusValueConfig
from pv2hash.sources.modbus_register import ModbusTcpRegisterReader

logger = get_logger("pv2hash.source.pv_modbus")


class PvModbusSource(ModbusTcpRegisterReader, EnergySource):
    driver_id = "pv_modbus"
    driver_label = "Modbus TCP PV-Erzeugung"

    def __init__(
        self, *, host: str, port: int = 502, unit_id: int = 1,
        poll_interval_ms: int = 5000, request_timeout_seconds: float = 1.0,
        power: ModbusValueConfig | None = None,
    ):
        self.host = str(host).strip()
        self.port = int(port)
        self.unit_id = int(unit_id)
        self.poll_interval_ms = int(poll_interval_ms)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.power_cfg = power or ModbusValueConfig(name="power")
        self._transaction_id = 0
        self.power_w: float | None = None
        self.updated_at: datetime | None = None
        self.quality = "no_data"
        self.last_error: str | None = None
        self._last_reported_error: str | None = None
        self.debug_info: dict = {
            "current_quality": "no_data",
            "pv_power_w": None,
            "last_poll_at": None,
            "last_live_packet_at": None,
            "last_error": None,
        }
        self._validate_settings()

    def _validate_settings(self):
        if not self.host or any(c.isspace() for c in self.host):
            raise ValueError("PV-Modbus: Host/IP fehlt oder ist ungültig.")
        if not 1 <= self.port <= 65535:
            raise ValueError("PV-Modbus: Port muss zwischen 1 und 65535 liegen.")
        if not 0 <= self.unit_id <= 255:
            raise ValueError("PV-Modbus: Unit-ID muss zwischen 0 und 255 liegen.")
        if not 1000 <= self.poll_interval_ms <= 60000:
            raise ValueError("PV-Modbus: Abfrageintervall muss zwischen 1000 und 60000 ms liegen.")
        if not 0.2 <= self.request_timeout_seconds <= 5.0:
            raise ValueError("PV-Modbus: Timeout muss zwischen 0,2 und 5 s liegen.")
        c = self.power_cfg
        if c.register_type not in {"holding", "input"}:
            raise ValueError("PV-Modbus: nur Holding- und Input-Register werden unterstützt.")
        if c.value_type not in {"int16", "uint16", "int32", "uint32", "float32"}:
            raise ValueError("PV-Modbus: ungültiger Datentyp.")
        if c.endian not in {"big_endian", "little_endian"}:
            raise ValueError("PV-Modbus: ungültige Byte-Reihenfolge.")
        if c.address is None or not 0 <= c.address <= 65536 - c.register_count:
            raise ValueError("PV-Modbus: Registeradresse außerhalb 0–65535.")
        if not math.isfinite(c.factor) or c.factor == 0:
            raise ValueError("PV-Modbus: Faktor muss endlich und ungleich 0 sein.")

    def _poll_sync(self) -> float:
        with socket.create_connection((self.host, self.port), timeout=self.request_timeout_seconds) as sock:
            sock.settimeout(self.request_timeout_seconds)
            value = self._read_numeric_value(sock, self.power_cfg)
        if value is None or not math.isfinite(value):
            raise ValueError("PV-Modbus: ungültiger oder fehlender Leistungswert.")
        return value

    async def read(self):
        """Independent background polling. This is never called by the controller."""
        self.debug_info["last_poll_at"] = datetime.now(UTC).isoformat()
        try:
            value = await asyncio.to_thread(self._poll_sync)
        except Exception as exc:
            self.power_w = None  # Never display stale power as a live measurement.
            self.quality = "offline"
            self.last_error = str(exc)
            self.debug_info.update({"current_quality": "offline", "pv_power_w": None, "last_error": self.last_error})
            if self.last_error != self._last_reported_error:
                logger.warning("PV-Modbus-Abfrage fehlgeschlagen: %s", self.last_error)
                self._last_reported_error = self.last_error
            return None

        self.power_w = value
        self.updated_at = datetime.now(UTC)
        self.quality = "live"
        self.last_error = None
        self._last_reported_error = None
        self.debug_info.update({
            "current_quality": "live", "pv_power_w": value,
            "last_error": None, "last_live_packet_at": self.updated_at.isoformat(),
        })
        return value

    @classmethod
    def config_fields_from_settings(cls, settings: dict) -> list[dict]:
        c = settings.get("power", {}) if isinstance(settings.get("power"), dict) else {}
        def field(name, label, typ, value, width="quarter", **kw):
            return {"name": name, "label": label, "type": typ, "value": value,
                    "required": True, "layout": {"width": width}, **kw}
        return [
            field("pv_host", "Host / IP", "text", settings.get("host", ""), "half"),
            field("pv_port", "Port", "number", settings.get("port", 502), min=1, max=65535),
            field("pv_unit_id", "Unit-ID", "number", settings.get("unit_id", 1), min=0, max=255),
            field("pv_poll_interval_ms", "Intervall (ms)", "number", settings.get("poll_interval_ms", 5000),
                  min=1000, max=60000, step=1000),
            field("pv_request_timeout_seconds", "Timeout (s)", "number",
                  settings.get("request_timeout_seconds", 1.0), min=0.2, max=5, step=0.1),
            {
                "type": "fieldset", "title": "PV-Leistung", "layout": {"width": "full"},
                "help": "Ein Messwert mit einer Startadresse. 32-Bit-Werte benötigen automatisch zwei aufeinanderfolgende Register. Faktor z. B. 1 für W, 1000 für kW; -1 dreht das Vorzeichen.",
                "fields": [
                    field("pv_power_address", "Registeradresse (0-basiert)", "number", c.get("address", ""),
                          min=0, max=65535, step=1),
                    field("pv_power_register_type", "Registerart", "select", c.get("register_type", "holding"),
                          options=[{"value":"holding","label":"Holding (FC03)"},{"value":"input","label":"Input (FC04)"}]),
                    field("pv_power_value_type", "Wertetyp", "select", c.get("value_type", "uint16"),
                          options=[{"value":t,"label":t} for t in ("uint16","int16","uint32","int32","float32")]),
                    field("pv_power_endian", "Byte-Reihenfolge", "select", c.get("endian", "big_endian"),
                          options=[{"value":"big_endian","label":"Big Endian"},{"value":"little_endian","label":"Little Endian"}]),
                    field("pv_power_factor", "Faktor (→ W)", "number", c.get("factor", 1.0), step="any"),
                ],
            },
        ]

    def get_config_fields(self, *, config=None):
        return self.config_fields_from_settings((config or {}).get("settings", {}) or {})

    def get_header_fields(self, *, snapshot=None, debug_info=None, status=None, detail_groups=None):
        debug = debug_info or self.debug_info
        return [
            {"label": "Leistung", "value": debug.get("pv_power_w"), "unit": "W", "precision": 0},
            {"label": "Status", "value": (status or {}).get("text", "—")},
        ]

    def get_detail_groups(self, *, snapshot=None, debug_info=None):
        debug = debug_info or self.debug_info
        return [{"title":"Messung","fields":[
            {"label":"PV-Erzeugung", "value": debug.get("pv_power_w"),"unit":"W","precision":0},
            {"label":"Letzte Live-Messung","value":debug.get("last_live_packet_at")},
        ]}]

    def get_gui_model(self, **kwargs):
        model = super().get_gui_model(**kwargs)
        debug = kwargs.get("debug_info") or self.debug_info
        # Standalone non-controller measurement uses its own independent quality.
        quality = str(debug.get("current_quality") or "no_data")
        model["status"]["state"] = quality
        model["status"]["text"] = self._format_quality_text(quality)
        model["status"]["updated_at"] = debug.get("last_live_packet_at")
        return model
