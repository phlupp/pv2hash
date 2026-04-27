from __future__ import annotations

import asyncio
import socket
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

from pv2hash.logging_ext.setup import get_logger
from pv2hash.models.energy import EnergySnapshot
from pv2hash.sources.base import EnergySource
from pv2hash.sources.battery_modbus_profiles import (
    battery_modbus_profile_choices,
    battery_modbus_profile_warnings,
    get_battery_modbus_profile,
)


logger = get_logger("pv2hash.source.battery_modbus")


class RequiredModbusValueError(RuntimeError):
    pass


REGISTER_TYPE_TO_FUNCTION = {
    "holding": 0x03,
    "input": 0x04,
    "coil": 0x01,
    "discrete_input": 0x02,
}

VALUE_TYPE_REGISTER_COUNT = {
    "int8": 1,
    "uint8": 1,
    "int16": 1,
    "uint16": 1,
    "int32": 2,
    "uint32": 2,
    "float32": 2,
}


@dataclass(slots=True)
class ModbusValueConfig:
    name: str
    register_type: str = "holding"
    address: int | None = None
    value_type: str = "uint16"
    endian: str = "big_endian"
    factor: float = 1.0

    @property
    def register_count(self) -> int:
        if self.register_type in {"coil", "discrete_input"}:
            return 1
        return VALUE_TYPE_REGISTER_COUNT.get(self.value_type, 1)

    @property
    def enabled(self) -> bool:
        return self.address is not None and self.address >= 0


class BatteryModbusSource(EnergySource):
    driver_id = "battery_modbus"
    driver_label = "Modbus TCP Batterie"

    def __init__(
        self,
        host: str,
        port: int = 502,
        unit_id: int = 1,
        poll_interval_ms: int = 1000,
        request_timeout_seconds: float = 1.0,
        soc: ModbusValueConfig | None = None,
        charge_power: ModbusValueConfig | None = None,
        discharge_power: ModbusValueConfig | None = None,
        voltage: ModbusValueConfig | None = None,
        current: ModbusValueConfig | None = None,
        soh: ModbusValueConfig | None = None,
        temperature: ModbusValueConfig | None = None,
        capacity: ModbusValueConfig | None = None,
        max_charge_current: ModbusValueConfig | None = None,
        max_discharge_current: ModbusValueConfig | None = None,
    ) -> None:
        self.host = host.strip()
        self.port = max(1, int(port))
        self.unit_id = max(0, min(255, int(unit_id)))
        self.poll_interval_ms = max(100, int(poll_interval_ms))
        self.poll_interval_seconds = self.poll_interval_ms / 1000.0
        self.request_timeout_seconds = max(0.2, float(request_timeout_seconds))
        self.soc_cfg = soc or ModbusValueConfig(name="soc")
        self.charge_power_cfg = charge_power or ModbusValueConfig(name="charge_power")
        self.discharge_power_cfg = discharge_power or ModbusValueConfig(name="discharge_power")
        self.voltage_cfg = voltage or ModbusValueConfig(name="voltage")
        self.current_cfg = current or ModbusValueConfig(name="current")
        self.soh_cfg = soh or ModbusValueConfig(name="soh")
        self.temperature_cfg = temperature or ModbusValueConfig(name="temperature")
        self.capacity_cfg = capacity or ModbusValueConfig(name="capacity")
        self.max_charge_current_cfg = max_charge_current or ModbusValueConfig(name="max_charge_current")
        self.max_discharge_current_cfg = max_discharge_current or ModbusValueConfig(name="max_discharge_current")

        self._transaction_id = 0
        self._last_poll_monotonic = 0.0
        self._read_lock = asyncio.Lock()
        self.last_snapshot: EnergySnapshot | None = None
        self.last_live_packet_at: datetime | None = None
        self._last_logged_quality: str | None = None
        self._last_logged_poll_error: str | None = None

        self.debug_info = {
            "host": self.host,
            "port": self.port,
            "unit_id": self.unit_id,
            "poll_interval_ms": self.poll_interval_ms,
            "request_timeout_seconds": self.request_timeout_seconds,
            "current_quality": "no_data",
            "last_error": None,
            "last_poll_at": None,
            "last_live_packet_at": None,
            "battery_soc_pct": None,
            "battery_charge_power_w": None,
            "battery_discharge_power_w": None,
            "battery_is_charging": None,
            "battery_is_discharging": None,
            "battery_is_active": None,
            "battery_voltage_v": None,
            "battery_current_a": None,
            "battery_soh_pct": None,
            "battery_temperature_c": None,
            "battery_nominal_capacity_kwh": None,
            "battery_max_charge_current_a": None,
            "battery_max_discharge_current_a": None,
            "optional_errors": {},
            "configs": {
                "soc": self._config_to_debug(self.soc_cfg),
                "charge_power": self._config_to_debug(self.charge_power_cfg),
                "discharge_power": self._config_to_debug(self.discharge_power_cfg),
                "voltage": self._config_to_debug(self.voltage_cfg),
                "current": self._config_to_debug(self.current_cfg),
                "soh": self._config_to_debug(self.soh_cfg),
                "temperature": self._config_to_debug(self.temperature_cfg),
                "capacity": self._config_to_debug(self.capacity_cfg),
                "max_charge_current": self._config_to_debug(self.max_charge_current_cfg),
                "max_discharge_current": self._config_to_debug(self.max_discharge_current_cfg),
            },
        }

        logger.info(
            "Battery Modbus source initialized: host=%s port=%s unit_id=%s poll_ms=%s",
            self.host,
            self.port,
            self.unit_id,
            self.poll_interval_ms,
        )

    def get_config_fields(self, *, config: dict | None = None) -> list[dict]:
        settings = (config or {}).get("settings", {}) or {}

        def modbus_fields(title: str, prefix: str, value_cfg: object, *, required: bool = True, unit: str | None = None) -> dict:
            cfg = settings.get(prefix.replace("battery_", ""), None)
            if prefix == "battery_soc":
                cfg = settings.get("soc", cfg)
            elif prefix == "battery_charge_power":
                cfg = settings.get("charge_power", cfg)
            elif prefix == "battery_discharge_power":
                cfg = settings.get("discharge_power", cfg)
            elif prefix == "battery_voltage":
                cfg = settings.get("voltage", cfg)
            elif prefix == "battery_current":
                cfg = settings.get("current", cfg)
            elif prefix == "battery_soh":
                cfg = settings.get("soh", cfg)
            elif prefix == "battery_temperature":
                cfg = settings.get("temperature", cfg)
            elif prefix == "battery_capacity":
                cfg = settings.get("capacity", cfg)
            elif prefix == "battery_max_charge_current":
                cfg = settings.get("max_charge_current", cfg)
            elif prefix == "battery_max_discharge_current":
                cfg = settings.get("max_discharge_current", cfg)

            if not isinstance(cfg, dict):
                cfg = {}
            return {
                "type": "fieldset",
                "title": title,
                "unit": unit,
                "layout": {"width": "full"},
                "fields": [
                    {"name": f"{prefix}_address", "label": "Adresse", "type": "number", "value": cfg.get("address", getattr(value_cfg, "address", "")), "step": 1, "required": required, "layout": {"width": "quarter"}},
                    {
                        "name": f"{prefix}_register_type",
                        "label": "Registerart",
                        "type": "select",
                        "value": cfg.get("register_type", getattr(value_cfg, "register_type", "holding")),
                        "required": required,
                        "layout": {"width": "quarter"},
                        "options": [
                            {"value": "holding", "label": "holding"},
                            {"value": "input", "label": "input"},
                            {"value": "coil", "label": "coil"},
                            {"value": "discrete_input", "label": "discrete_input"},
                        ],
                    },
                    {
                        "name": f"{prefix}_value_type",
                        "label": "Wertetyp",
                        "type": "select",
                        "value": cfg.get("value_type", getattr(value_cfg, "value_type", "uint16")),
                        "required": required,
                        "layout": {"width": "quarter"},
                        "options": [
                            {"value": "uint8", "label": "uint8"},
                            {"value": "int8", "label": "int8"},
                            {"value": "uint16", "label": "uint16"},
                            {"value": "int16", "label": "int16"},
                            {"value": "uint32", "label": "uint32"},
                            {"value": "int32", "label": "int32"},
                            {"value": "float32", "label": "float32"},
                        ],
                    },
                    {
                        "name": f"{prefix}_endian",
                        "label": "Endian",
                        "type": "select",
                        "value": cfg.get("endian", getattr(value_cfg, "endian", "big_endian")),
                        "required": required,
                        "layout": {"width": "quarter"},
                        "options": [
                            {"value": "big_endian", "label": "big_endian"},
                            {"value": "little_endian", "label": "little_endian"},
                        ],
                    },
                    {"name": f"{prefix}_factor", "label": "Faktor", "type": "number", "value": cfg.get("factor", getattr(value_cfg, "factor", 1.0)), "step": "any", "required": required, "layout": {"width": "quarter"}},
                ],
                "help": (
                    "Alle Felder sind erforderlich. Der Messwert wird mit dem Faktor multipliziert."
                    if required
                    else "Optional: leer lassen, wenn dieser Wert nicht abgefragt werden soll."
                ),
            }
        selected_profile = str(settings.get("modbus_profile", "") or "")
        if selected_profile:
            selected = get_battery_modbus_profile(selected_profile)
            if selected is not None:
                selected_profile = selected.key


        return [
            {
                "name": "battery_modbus_profile",
                "label": "Modbus-Profil",
                "type": "select",
                "value": selected_profile,
                "required": False,
                "layout": {"width": "full"},
                "options": battery_modbus_profile_choices(),
                "action_on_change": "battery_modbus_apply_profile",
                "action_on_change_busy_text": "Profil wird geladen …",
                "action_on_change_empty": "preview",
                "help": "Optional: Profil wählen. Die Werte werden direkt ins Formular übernommen und erst beim Speichern dauerhaft gesichert.",
            },
            {"name": "battery_host", "label": "Host / IP", "type": "text", "value": settings.get("host", self.host), "required": True, "layout": {"width": "half"}},
            {"name": "battery_port", "label": "Port", "type": "number", "value": settings.get("port", self.port), "step": 1, "required": True, "layout": {"width": "quarter"}},
            {"name": "battery_unit_id", "label": "Unit-ID", "type": "number", "value": settings.get("unit_id", self.unit_id), "step": 1, "required": True, "layout": {"width": "quarter"}},
            {"name": "battery_poll_interval_ms", "label": "Poll-Intervall", "type": "number", "value": settings.get("poll_interval_ms", self.poll_interval_ms), "unit": "ms", "step": 100, "required": True, "layout": {"width": "half"}},
            {"name": "battery_request_timeout_seconds", "label": "Request-Timeout", "type": "number", "value": settings.get("request_timeout_seconds", self.request_timeout_seconds), "unit": "s", "step": 0.1, "required": True, "layout": {"width": "half"}},
            modbus_fields("SOC", "battery_soc", self.soc_cfg, unit="%"),
            modbus_fields("Ladeleistung", "battery_charge_power", self.charge_power_cfg, unit="W"),
            modbus_fields("Entladeleistung", "battery_discharge_power", self.discharge_power_cfg, unit="W"),
            modbus_fields("Spannung", "battery_voltage", self.voltage_cfg, required=False, unit="V"),
            modbus_fields("Strom", "battery_current", self.current_cfg, required=False, unit="A"),
            modbus_fields("SOH", "battery_soh", self.soh_cfg, required=False, unit="%"),
            modbus_fields("Temperatur", "battery_temperature", self.temperature_cfg, required=False, unit="°C"),
            modbus_fields("Nennkapazität", "battery_capacity", self.capacity_cfg, required=False, unit="kWh"),
            modbus_fields("Max. Ladestrom", "battery_max_charge_current", self.max_charge_current_cfg, required=False, unit="A"),
            modbus_fields("Max. Entladestrom", "battery_max_discharge_current", self.max_discharge_current_cfg, required=False, unit="A"),
        ]


    def get_actions(self, *, config: dict | None = None) -> list[dict]:
        # Modbus profiles are applied automatically when the profile select field changes.
        # The action remains available via /api/sources/action, but no separate button is rendered.
        return []



    def get_warnings(self, *, config: dict | None = None) -> list[str]:
        return battery_modbus_profile_warnings()

    def get_header_fields(self, *, snapshot=None, debug_info: dict | None = None, status: dict | None = None, detail_groups=None) -> list[dict]:
        debug_info = debug_info or self.debug_info
        fields = super().get_header_fields(snapshot=snapshot, debug_info=debug_info, status=status, detail_groups=detail_groups)
        soc = getattr(snapshot, "battery_soc_pct", None) if snapshot is not None else debug_info.get("battery_soc_pct")
        charge = getattr(snapshot, "battery_charge_power_w", None) if snapshot is not None else debug_info.get("battery_charge_power_w")
        discharge = getattr(snapshot, "battery_discharge_power_w", None) if snapshot is not None else debug_info.get("battery_discharge_power_w")
        fields.append({"label": "SoC", "value": soc, "unit": "%", "precision": 1})
        fields.append({"label": "Ladeleistung", "value": charge, "unit": "W", "precision": 0})
        fields.append({"label": "Entladeleistung", "value": discharge, "unit": "W", "precision": 0})
        return fields

    def get_detail_groups(self, *, snapshot=None, debug_info: dict | None = None) -> list[dict]:
        debug_info = debug_info or self.debug_info

        fields: list[dict] = []
        optional_values = [
            ("Spannung", debug_info.get("battery_voltage_v"), "V", 1),
            ("Strom", debug_info.get("battery_current_a"), "A", 2),
            ("SOH", debug_info.get("battery_soh_pct"), "%", 1),
            ("Temperatur", debug_info.get("battery_temperature_c"), "°C", 1),
            ("Nennkapazität", debug_info.get("battery_nominal_capacity_kwh"), "kWh", 1),
            ("Max. Ladestrom", debug_info.get("battery_max_charge_current_a"), "A", 1),
            ("Max. Entladestrom", debug_info.get("battery_max_discharge_current_a"), "A", 1),
        ]
        for label, value, unit, precision in optional_values:
            if value is not None:
                fields.append({"label": label, "value": value, "unit": unit, "precision": precision})

        if not fields:
            return []

        return [{"title": "Details", "fields": fields}]

    async def read(self) -> EnergySnapshot:
        async with self._read_lock:
            now_mono = monotonic()
            if self.last_snapshot is not None and (now_mono - self._last_poll_monotonic) < self.poll_interval_seconds:
                return self.last_snapshot

            self._last_poll_monotonic = now_mono
            self.debug_info["last_poll_at"] = datetime.now(UTC).isoformat()

            try:
                snapshot = await asyncio.to_thread(self._poll_device)
                self.last_snapshot = snapshot
                self.last_live_packet_at = snapshot.updated_at
                self.debug_info["last_live_packet_at"] = snapshot.updated_at.isoformat()
                self.debug_info["last_error"] = None
                self._last_logged_poll_error = None
                self._set_quality("live")
                return snapshot
            except RequiredModbusValueError as exc:
                error_text = str(exc)
                self.debug_info["last_error"] = error_text
                if self._last_logged_poll_error != error_text:
                    logger.warning("Battery Modbus required value unavailable: %s", error_text)
                    self._last_logged_poll_error = error_text
                return self._fallback_snapshot(force_quality="offline")
            except Exception as exc:
                error_text = str(exc)
                self.debug_info["last_error"] = error_text
                if self._last_logged_poll_error != error_text:
                    logger.warning("Battery Modbus poll failed: %s", error_text)
                    self._last_logged_poll_error = error_text
                return self._fallback_snapshot(force_quality="offline")

    def _poll_device(self) -> EnergySnapshot:
        if not self.host:
            raise RuntimeError("battery_modbus host is empty")

        with socket.create_connection(
            (self.host, self.port),
            timeout=self.request_timeout_seconds,
        ) as sock:
            sock.settimeout(self.request_timeout_seconds)

            soc_pct = self._read_required_numeric_value(sock, self.soc_cfg)
            charge_power_w = self._read_required_numeric_value(sock, self.charge_power_cfg)
            discharge_power_w = self._read_required_numeric_value(sock, self.discharge_power_cfg)
            voltage_v = self._read_optional_numeric_value(sock, self.voltage_cfg)
            current_a = self._read_optional_numeric_value(sock, self.current_cfg)
            soh_pct = self._read_optional_numeric_value(sock, self.soh_cfg)
            temperature_c = self._read_optional_numeric_value(sock, self.temperature_cfg)
            nominal_capacity_kwh = self._read_optional_numeric_value(sock, self.capacity_cfg)
            max_charge_current_a = self._read_optional_numeric_value(sock, self.max_charge_current_cfg)
            max_discharge_current_a = self._read_optional_numeric_value(sock, self.max_discharge_current_cfg)

        is_charging = charge_power_w > 0 if charge_power_w is not None else None
        is_discharging = discharge_power_w > 0 if discharge_power_w is not None else None
        is_active = None
        if is_charging is not None or is_discharging is not None:
            is_active = bool(is_charging or is_discharging)

        now = datetime.now(UTC)

        self.debug_info["battery_soc_pct"] = soc_pct
        self.debug_info["battery_charge_power_w"] = charge_power_w
        self.debug_info["battery_discharge_power_w"] = discharge_power_w
        self.debug_info["battery_is_charging"] = is_charging
        self.debug_info["battery_is_discharging"] = is_discharging
        self.debug_info["battery_is_active"] = is_active
        self.debug_info["battery_voltage_v"] = voltage_v
        self.debug_info["battery_current_a"] = current_a
        self.debug_info["battery_soh_pct"] = soh_pct
        self.debug_info["battery_temperature_c"] = temperature_c
        self.debug_info["battery_nominal_capacity_kwh"] = nominal_capacity_kwh
        self.debug_info["battery_max_charge_current_a"] = max_charge_current_a
        self.debug_info["battery_max_discharge_current_a"] = max_discharge_current_a

        return EnergySnapshot(
            grid_power_w=0.0,
            battery_charge_power_w=charge_power_w,
            battery_discharge_power_w=discharge_power_w,
            battery_soc_pct=soc_pct,
            battery_is_charging=is_charging,
            battery_is_discharging=is_discharging,
            battery_is_active=is_active,
            updated_at=now,
            source="battery_modbus",
            quality="live",
        )

    def _fallback_snapshot(self, *, force_quality: str | None = None) -> EnergySnapshot:
        now = datetime.now(UTC)

        if self.last_snapshot is not None and self.last_live_packet_at is not None:
            age = (now - self.last_live_packet_at).total_seconds()
            stale_after_seconds = max(self.poll_interval_seconds * 3.0, 3.0)
            offline_after_seconds = max(self.poll_interval_seconds * 10.0, 15.0)

            quality = force_quality
            if quality is None:
                quality = "stale" if age < offline_after_seconds else "offline"
                if age < stale_after_seconds:
                    quality = "live"

            self._set_quality(quality)

            return EnergySnapshot(
                grid_power_w=0.0,
                battery_charge_power_w=self.last_snapshot.battery_charge_power_w,
                battery_discharge_power_w=self.last_snapshot.battery_discharge_power_w,
                battery_soc_pct=self.last_snapshot.battery_soc_pct,
                battery_is_charging=self.last_snapshot.battery_is_charging,
                battery_is_discharging=self.last_snapshot.battery_is_discharging,
                battery_is_active=self.last_snapshot.battery_is_active,
                updated_at=now,
                source="battery_modbus",
                quality=quality,
            )

        quality = force_quality or "no_data"
        self._set_quality(quality)
        return EnergySnapshot(
            grid_power_w=0.0,
            battery_charge_power_w=None,
            battery_discharge_power_w=None,
            battery_soc_pct=None,
            battery_is_charging=None,
            battery_is_discharging=None,
            battery_is_active=None,
            updated_at=now,
            source="battery_modbus",
            quality=quality,
        )

    def _read_required_numeric_value(self, sock: socket.socket, cfg: ModbusValueConfig) -> float | None:
        try:
            return self._read_numeric_value(sock, cfg)
        except Exception as exc:
            raise RequiredModbusValueError(f"Required battery Modbus value '{cfg.name}' failed: {exc}") from exc

    def _read_optional_numeric_value(self, sock: socket.socket, cfg: ModbusValueConfig) -> float | None:
        errors = self.debug_info.setdefault("optional_errors", {})
        if not cfg.enabled:
            errors.pop(cfg.name, None)
            return None

        try:
            value = self._read_numeric_value(sock, cfg)
        except Exception as exc:
            errors[cfg.name] = str(exc)
            return None

        errors.pop(cfg.name, None)
        return value

    def _read_numeric_value(self, sock: socket.socket, cfg: ModbusValueConfig) -> float | None:
        if not cfg.enabled:
            return None

        register_type = cfg.register_type if cfg.register_type in REGISTER_TYPE_TO_FUNCTION else "holding"
        function_code = REGISTER_TYPE_TO_FUNCTION[register_type]
        raw_bytes = self._read_modbus_value(sock, function_code, int(cfg.address), cfg.register_count)
        decoded = self._decode_value(raw_bytes=raw_bytes, cfg=cfg, register_type=register_type)
        if decoded is None:
            return None
        return float(decoded) * float(cfg.factor)

    def _read_modbus_value(
        self,
        sock: socket.socket,
        function_code: int,
        address: int,
        quantity: int,
    ) -> bytes:
        self._transaction_id = (self._transaction_id + 1) % 0x10000
        transaction_id = self._transaction_id

        request_pdu = struct.pack(
            ">BHH",
            function_code,
            address,
            quantity,
        )
        request_mbap = struct.pack(
            ">HHHB",
            transaction_id,
            0,
            len(request_pdu) + 1,
            self.unit_id,
        )
        sock.sendall(request_mbap + request_pdu)

        header = self._recv_exact(sock, 7)
        rx_transaction_id, protocol_id, length, unit_id = struct.unpack(
            ">HHHB",
            header,
        )
        if rx_transaction_id != transaction_id:
            raise RuntimeError("Modbus transaction id mismatch")
        if protocol_id != 0:
            raise RuntimeError("Modbus protocol id mismatch")
        if unit_id != self.unit_id:
            raise RuntimeError("Modbus unit id mismatch")
        payload = self._recv_exact(sock, max(0, length - 1))
        if not payload:
            raise RuntimeError("Modbus response payload is empty")

        response_function = payload[0]
        if response_function & 0x80:
            exc_code = payload[1] if len(payload) > 1 else None
            raise RuntimeError(f"Modbus exception response: function=0x{response_function:02x} code={exc_code}")
        if response_function != function_code:
            raise RuntimeError("Modbus function code mismatch")

        if function_code in (0x01, 0x02):
            byte_count = payload[1]
            data = payload[2 : 2 + byte_count]
            if len(data) != byte_count:
                raise RuntimeError("Modbus bit response length mismatch")
            return data

        byte_count = payload[1]
        data = payload[2 : 2 + byte_count]
        if len(data) != byte_count:
            raise RuntimeError("Modbus register response length mismatch")
        expected_len = quantity * 2
        if byte_count < expected_len:
            raise RuntimeError(
                f"Modbus register response too short: got={byte_count} expected={expected_len}"
            )
        return data[:expected_len]

    def _decode_value(
        self,
        *,
        raw_bytes: bytes,
        cfg: ModbusValueConfig,
        register_type: str,
    ) -> float | int | None:
        if register_type in {"coil", "discrete_input"}:
            return 1 if raw_bytes and (raw_bytes[0] & 0x01) else 0

        data = bytes(raw_bytes)
        if cfg.endian == "little_endian":
            data = data[::-1]

        value_type = cfg.value_type

        if value_type == "int8":
            return struct.unpack(">b", data[:1])[0]
        if value_type == "uint8":
            return struct.unpack(">B", data[:1])[0]
        if value_type == "int16":
            return struct.unpack(">h", data[:2])[0]
        if value_type == "uint16":
            return struct.unpack(">H", data[:2])[0]
        if value_type == "int32":
            return struct.unpack(">i", data[:4])[0]
        if value_type == "uint32":
            return struct.unpack(">I", data[:4])[0]
        if value_type == "float32":
            return struct.unpack(">f", data[:4])[0]

        raise RuntimeError(f"Unsupported Modbus value type: {value_type}")

    def _recv_exact(self, sock: socket.socket, length: int) -> bytes:
        chunks: list[bytes] = []
        received = 0
        while received < length:
            chunk = sock.recv(length - received)
            if not chunk:
                raise RuntimeError("Modbus socket closed unexpectedly")
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _format_quality_text(quality: str) -> str:
        if str(quality or "") == "offline":
            return "Batterie getrennt"
        return EnergySource._format_quality_text(quality)

    def _set_quality(self, quality: str) -> None:
        self.debug_info["current_quality"] = quality
        if quality != self._last_logged_quality:
            logger.info("Battery Modbus source quality changed to %s", quality)
            self._last_logged_quality = quality

    def _config_to_debug(self, cfg: ModbusValueConfig) -> dict:
        return {
            "register_type": cfg.register_type,
            "address": cfg.address,
            "value_type": cfg.value_type,
            "endian": cfg.endian,
            "factor": cfg.factor,
        }
