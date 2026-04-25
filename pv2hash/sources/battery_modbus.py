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


logger = get_logger("pv2hash.source.battery_modbus")

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

        self._transaction_id = 0
        self._last_poll_monotonic = 0.0
        self._read_lock = asyncio.Lock()
        self.last_snapshot: EnergySnapshot | None = None
        self.last_live_packet_at: datetime | None = None
        self._last_logged_quality: str | None = None

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
            "configs": {
                "soc": self._config_to_debug(self.soc_cfg),
                "charge_power": self._config_to_debug(self.charge_power_cfg),
                "discharge_power": self._config_to_debug(self.discharge_power_cfg),
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

        def modbus_fields(title: str, prefix: str, value_cfg: object) -> dict:
            cfg = settings.get(prefix.replace("battery_", ""), None)
            if prefix == "battery_soc":
                cfg = settings.get("soc", cfg)
            elif prefix == "battery_charge_power":
                cfg = settings.get("charge_power", cfg)
            elif prefix == "battery_discharge_power":
                cfg = settings.get("discharge_power", cfg)

            if not isinstance(cfg, dict):
                cfg = {}
            return {
                "type": "fieldset",
                "title": title,
                "fields": [
                    {"name": f"{prefix}_address", "label": "Adresse", "type": "number", "value": cfg.get("address", getattr(value_cfg, "address", "")), "step": 1},
                    {
                        "name": f"{prefix}_register_type",
                        "label": "Registerart",
                        "type": "select",
                        "value": cfg.get("register_type", getattr(value_cfg, "register_type", "holding")),
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
                        "options": [
                            {"value": "big_endian", "label": "big_endian"},
                            {"value": "little_endian", "label": "little_endian"},
                        ],
                    },
                    {"name": f"{prefix}_factor", "label": "Faktor", "type": "number", "value": cfg.get("factor", getattr(value_cfg, "factor", 1.0)), "step": "any"},
                ],
                "help": "Leer lassen, wenn dieser Wert aktuell nicht abgefragt werden soll. Der Messwert wird mit dem Faktor multipliziert.",
            }

        return [
            {"name": "battery_host", "label": "Host / IP", "type": "text", "value": settings.get("host", self.host)},
            {"name": "battery_port", "label": "Port", "type": "number", "value": settings.get("port", self.port), "step": 1},
            {"name": "battery_unit_id", "label": "Unit-ID", "type": "number", "value": settings.get("unit_id", self.unit_id), "step": 1},
            {"name": "battery_poll_interval_ms", "label": "Poll-Intervall", "type": "number", "value": settings.get("poll_interval_ms", self.poll_interval_ms), "unit": "ms", "step": 100},
            {"name": "battery_request_timeout_seconds", "label": "Request-Timeout", "type": "number", "value": settings.get("request_timeout_seconds", self.request_timeout_seconds), "unit": "s", "step": 0.1},
            modbus_fields("SOC", "battery_soc", self.soc_cfg),
            modbus_fields("Ladeleistung", "battery_charge_power", self.charge_power_cfg),
            modbus_fields("Entladeleistung", "battery_discharge_power", self.discharge_power_cfg),
        ]


    def get_detail_groups(self, *, snapshot=None, debug_info: dict | None = None) -> list[dict]:
        debug_info = debug_info or self.debug_info
        soc = getattr(snapshot, "battery_soc_pct", None) if snapshot is not None else debug_info.get("battery_soc_pct")
        charge = getattr(snapshot, "battery_charge_power_w", None) if snapshot is not None else debug_info.get("battery_charge_power_w")
        discharge = getattr(snapshot, "battery_discharge_power_w", None) if snapshot is not None else debug_info.get("battery_discharge_power_w")
        return [
            {
                "title": "Batterie",
                "fields": [
                    {"label": "SOC", "value": soc, "unit": "%", "precision": 1},
                    {"label": "Ladeleistung", "value": charge, "unit": "W", "precision": 0},
                    {"label": "Entladeleistung", "value": discharge, "unit": "W", "precision": 0},
                    {"label": "Host", "value": self.host or None},
                ],
            }
        ]

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
                self._set_quality("live")
                return snapshot
            except Exception as exc:
                self.debug_info["last_error"] = str(exc)
                logger.exception("Battery Modbus poll failed")
                return self._fallback_snapshot()

    def _poll_device(self) -> EnergySnapshot:
        if not self.host:
            raise RuntimeError("battery_modbus host is empty")

        with socket.create_connection(
            (self.host, self.port),
            timeout=self.request_timeout_seconds,
        ) as sock:
            sock.settimeout(self.request_timeout_seconds)

            soc_pct = self._read_numeric_value(sock, self.soc_cfg)
            charge_power_w = self._read_numeric_value(sock, self.charge_power_cfg)
            discharge_power_w = self._read_numeric_value(sock, self.discharge_power_cfg)

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

    def _fallback_snapshot(self) -> EnergySnapshot:
        now = datetime.now(UTC)

        if self.last_snapshot is not None and self.last_live_packet_at is not None:
            age = (now - self.last_live_packet_at).total_seconds()
            stale_after_seconds = max(self.poll_interval_seconds * 3.0, 3.0)
            offline_after_seconds = max(self.poll_interval_seconds * 10.0, 15.0)

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

        self._set_quality("no_data")
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
            quality="no_data",
        )

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
