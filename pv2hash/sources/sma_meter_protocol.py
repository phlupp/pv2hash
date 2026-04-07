import asyncio
import socket
import struct
from datetime import UTC, datetime

from pv2hash.logging_ext.setup import get_logger
from pv2hash.models.energy import EnergySnapshot
from pv2hash.sources.base import EnergySource


logger = get_logger("pv2hash.source.sma_meter_protocol")


class IncompleteSmaPacketError(ValueError):
    pass


class SmaMeterProtocolSource(EnergySource):
    EMETER_PROTOCOL_ID = b"\x60\x69"

    POWER_INDEXES = {
        1: "W",
        2: "W",
        3: "var",
        4: "var",
        9: "VA",
        10: "VA",
        21: "W",
        22: "W",
        23: "var",
        24: "var",
        29: "VA",
        30: "VA",
        41: "W",
        42: "W",
        43: "var",
        44: "var",
        49: "VA",
        50: "VA",
        61: "W",
        62: "W",
        63: "var",
        64: "var",
        69: "VA",
        70: "VA",
    }
    CURRENT_INDEXES = {31, 51, 71}
    VOLTAGE_INDEXES = {32, 52, 72}
    COSPHI_INDEXES = {13}

    def __init__(
        self,
        multicast_ip: str = "239.12.255.254",
        bind_port: int = 9522,
        interface_ip: str = "0.0.0.0",
        packet_timeout_seconds: float = 1.0,
        stale_after_seconds: float = 8.0,
        offline_after_seconds: float = 30.0,
        device_ip: str = "",
    ) -> None:
        self.multicast_ip = multicast_ip
        self.bind_port = bind_port
        self.interface_ip = interface_ip.strip() or "0.0.0.0"
        self.packet_timeout_seconds = packet_timeout_seconds
        self.stale_after_seconds = stale_after_seconds
        self.offline_after_seconds = offline_after_seconds
        self.device_ip = device_ip.strip()

        self.last_snapshot: EnergySnapshot | None = None
        self.last_live_packet_at: datetime | None = None

        self.debug_dump_obis = True
        self._last_logged_quality: str | None = None

        self.debug_info = {
            "received_packets": 0,
            "parsed_packets": 0,
            "ignored_packets": 0,
            "timeouts": 0,
            "parse_errors": 0,
            "incomplete_packets": 0,
            "last_sender_ip": None,
            "last_sender_port": None,
            "last_packet_len": None,
            "last_protocol": None,
            "last_error": None,
            "last_packet_decision": None,
            "last_packet_rejected_reason": None,
            "device_ip_filter": self.device_ip or None,
            "multicast_ip": self.multicast_ip,
            "bind_port": self.bind_port,
            "interface_ip": self.interface_ip,
            "multicast_joined": False,
            "active_plus_w": None,
            "active_minus_w": None,
            "grid_power_w": None,
            "has_active_plus": False,
            "has_active_minus": False,
            "last_used_active_plus_obis": None,
            "last_used_active_minus_obis": None,
            "packet_timeout_seconds": self.packet_timeout_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            "offline_after_seconds": self.offline_after_seconds,
            "last_live_packet_at": None,
            "current_quality": "no_data",
            "last_packet_device_address_hex": None,
            "last_packet_susy_id": None,
            "last_packet_serial_number": None,
            "last_packet_measuring_time_ms": None,
            "last_packet_entry_count": 0,
            "last_packet_channels": [],
            "last_packet_obis_ids": [],
            "last_packet_manufacturer_specific_count": 0,
        }

        self.sock = self._create_socket()

    def _create_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind(("", self.bind_port))
        except OSError:
            sock.bind(("0.0.0.0", self.bind_port))

        if self.interface_ip == "0.0.0.0":
            membership = socket.inet_aton(self.multicast_ip) + socket.inet_aton("0.0.0.0")
        else:
            membership = socket.inet_aton(self.multicast_ip) + socket.inet_aton(self.interface_ip)

        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.settimeout(self.packet_timeout_seconds)

        self.debug_info["multicast_joined"] = True

        logger.info(
            "SMA meter protocol source initialized: multicast=%s port=%s interface=%s device_filter=%s",
            self.multicast_ip,
            self.bind_port,
            self.interface_ip,
            self.device_ip or "-",
        )

        return sock

    async def read(self) -> EnergySnapshot:
        try:
            data, addr = await asyncio.to_thread(self.sock.recvfrom, 4096)
            sender_ip, sender_port = addr[0], addr[1]

            self.debug_info["received_packets"] += 1
            self.debug_info["last_sender_ip"] = sender_ip
            self.debug_info["last_sender_port"] = sender_port
            self.debug_info["last_packet_len"] = len(data)
            self.debug_info["last_packet_decision"] = None
            self.debug_info["last_packet_rejected_reason"] = None

            if self.device_ip and sender_ip != self.device_ip:
                self.debug_info["ignored_packets"] += 1
                self.debug_info["last_protocol"] = "filtered_ip"
                self.debug_info["last_packet_decision"] = "filtered"
                self.debug_info["last_packet_rejected_reason"] = "sender_ip_mismatch"
                return self._fallback_snapshot()

            proto_index = data.find(self.EMETER_PROTOCOL_ID)
            if proto_index < 0:
                other_index = data.find(b"\x60\x65")
                if other_index >= 0:
                    self.debug_info["last_protocol"] = "0x6065"
                else:
                    self.debug_info["last_protocol"] = "unknown"
                self.debug_info["ignored_packets"] += 1
                self.debug_info["last_packet_decision"] = "ignored"
                self.debug_info["last_packet_rejected_reason"] = "protocol_not_0x6069"
                return self._fallback_snapshot()

            self.debug_info["last_protocol"] = "0x6069"
            snapshot = self._parse_emeter_packet(data)
            self.last_snapshot = snapshot
            self.last_live_packet_at = snapshot.updated_at
            self.debug_info["parsed_packets"] += 1
            self.debug_info["last_error"] = None
            self.debug_info["last_live_packet_at"] = snapshot.updated_at.isoformat()
            self.debug_info["last_packet_decision"] = "accepted"
            self._set_quality("live")

            return snapshot

        except IncompleteSmaPacketError as exc:
            self.debug_info["ignored_packets"] += 1
            self.debug_info["incomplete_packets"] += 1
            self.debug_info["last_packet_decision"] = "rejected"
            self.debug_info["last_packet_rejected_reason"] = str(exc)
            self.debug_info["last_error"] = None
            logger.debug("Rejected SMA packet: %s", exc)
        except TimeoutError:
            self.debug_info["timeouts"] += 1
        except socket.timeout:
            self.debug_info["timeouts"] += 1
        except Exception as exc:
            self.debug_info["parse_errors"] += 1
            self.debug_info["last_error"] = str(exc)
            self.debug_info["last_packet_decision"] = "error"
            logger.exception("Error while reading SMA meter protocol packet")

        return self._fallback_snapshot()

    def _set_quality(self, quality: str) -> None:
        self.debug_info["current_quality"] = quality
        if quality != self._last_logged_quality:
            logger.info("SMA source quality changed to %s", quality)
            self._last_logged_quality = quality

    def _fallback_snapshot(self) -> EnergySnapshot:
        now = datetime.now(UTC)

        if self.last_snapshot is not None and self.last_live_packet_at is not None:
            age = (now - self.last_live_packet_at).total_seconds()

            if age < self.stale_after_seconds:
                quality = "live"
            elif age < self.offline_after_seconds:
                quality = "stale"
            else:
                quality = "offline"

            self._set_quality(quality)

            return EnergySnapshot(
                grid_power_w=self.last_snapshot.grid_power_w,
                pv_power_w=self.last_snapshot.pv_power_w,
                house_power_w=self.last_snapshot.house_power_w,
                battery_charge_power_w=self.last_snapshot.battery_charge_power_w,
                battery_discharge_power_w=self.last_snapshot.battery_discharge_power_w,
                battery_soc_pct=self.last_snapshot.battery_soc_pct,
                updated_at=now,
                source="sma_meter_protocol",
                quality=quality,
            )

        self._set_quality("no_data")
        return EnergySnapshot(
            grid_power_w=0.0,
            updated_at=now,
            source="sma_meter_protocol",
            quality="no_data",
        )

    @staticmethod
    def _extract_obis_parts(obis_num: int) -> tuple[int, int, int, int]:
        channel = (obis_num >> 24) & 0xFF
        index = (obis_num >> 16) & 0xFF
        measurement_type = (obis_num >> 8) & 0xFF
        tariff = obis_num & 0xFF
        return channel, index, measurement_type, tariff

    def _format_obis_num(self, obis_num: int) -> str:
        channel, index, measurement_type, tariff = self._extract_obis_parts(obis_num)
        return (
            f"{obis_num:08x} / channel={channel} / "
            f"1-{channel}:{index}.{measurement_type}.{tariff}"
        )

    @staticmethod
    def _is_manufacturer_specific(channel: int, index: int, measurement_type: int, tariff: int) -> bool:
        return (
            128 <= channel <= 199
            or index in set(range(128, 200)) | {240}
            or 128 <= measurement_type <= 254
            or 128 <= tariff <= 254
        )

    def _decode_obis_value(
        self,
        *,
        obis_num: int,
        length: int,
        raw_value: int,
    ) -> tuple[float | int, str, str | None]:
        _channel, index, measurement_type, _tariff = self._extract_obis_parts(obis_num)

        if length == 8:
            if measurement_type == 8 and index in self.POWER_INDEXES:
                return raw_value / 3600000.0, "kWh", "energy_meter_reading_ws"
            return raw_value, "raw64", None

        if length != 4:
            return raw_value, "raw", None

        if measurement_type != 4:
            return raw_value, "raw32", None

        if index in self.POWER_INDEXES:
            return raw_value / 10.0, self.POWER_INDEXES[index], "current_average"
        if index in self.COSPHI_INDEXES:
            return raw_value / 1000.0, "cosphi", "current_average"
        if index in self.CURRENT_INDEXES:
            return raw_value / 1000.0, "A", "current_average"
        if index in self.VOLTAGE_INDEXES:
            return raw_value / 1000.0, "V", "current_average"

        return raw_value, "raw32", None

    def _dump_obis_entries(
        self,
        *,
        entries: list[dict],
        packet_meta: dict,
        active_plus_w: float | None,
        active_minus_w: float | None,
    ) -> None:
        if not self.debug_dump_obis:
            return

        logger.debug("---- SMA packet dump start ----")
        logger.debug(
            "sender=%s:%s len=%s protocol=%s device=%s susy_id=%s serial=%s measure_time_ms=%s entries=%s channels=%s filter=%s",
            self.debug_info.get("last_sender_ip"),
            self.debug_info.get("last_sender_port"),
            self.debug_info.get("last_packet_len"),
            self.debug_info.get("last_protocol"),
            packet_meta["device_address_hex"],
            packet_meta["susy_id"],
            packet_meta["serial_number"],
            packet_meta["measuring_time_ms"],
            len(entries),
            sorted({entry["channel"] for entry in entries}),
            self.device_ip or "-",
        )
        logger.debug(
            "grid_inputs has_active_plus=%s active_plus_w=%s has_active_minus=%s active_minus_w=%s",
            active_plus_w is not None,
            active_plus_w,
            active_minus_w is not None,
            active_minus_w,
        )

        for entry in entries:
            manufacturer_specific = self._is_manufacturer_specific(
                entry["channel"],
                entry["index"],
                entry["measurement_type"],
                entry["tariff"],
            )
            logger.debug(
                "obis=0x%08x (%s) len=%s raw=%s decoded=%s %s scale=%s manufacturer_specific=%s",
                entry["obis_num"],
                self._format_obis_num(entry["obis_num"]),
                entry["length"],
                entry["raw_value"],
                entry["decoded_value"],
                entry["decoded_unit"],
                entry["scale_hint"] or "-",
                manufacturer_specific,
            )

        logger.debug("---- SMA packet dump end ----")

    def _parse_emeter_packet(self, packet: bytes) -> EnergySnapshot:
        if len(packet) < 32:
            raise ValueError(f"Packet too short: {len(packet)}")

        protocol_id = struct.unpack(">H", packet[16:18])[0]
        if protocol_id != 0x6069:
            raise ValueError(f"Unexpected protocol id: 0x{protocol_id:04x}")

        susy_id = struct.unpack(">H", packet[18:20])[0]
        serial_number = struct.unpack(">I", packet[20:24])[0]
        measuring_time_ms = struct.unpack(">I", packet[24:28])[0]
        device_address_hex = packet[18:24].hex()

        self.debug_info["last_packet_device_address_hex"] = device_address_hex
        self.debug_info["last_packet_susy_id"] = susy_id
        self.debug_info["last_packet_serial_number"] = serial_number
        self.debug_info["last_packet_measuring_time_ms"] = measuring_time_ms

        pos = 28
        active_plus_w: float | None = None
        active_minus_w: float | None = None
        entries: list[dict] = []

        while pos + 4 <= len(packet):
            obis_num = struct.unpack(">I", packet[pos:pos + 4])[0]

            if obis_num == 0 and pos == len(packet) - 4:
                break

            channel, index, measurement_type, tariff = self._extract_obis_parts(obis_num)
            obis_length = measurement_type
            pos += 4

            if obis_length not in (4, 8):
                continue

            if pos + obis_length > len(packet):
                break

            if obis_length == 4:
                raw_value = struct.unpack(">i", packet[pos:pos + 4])[0]
            else:
                raw_value = struct.unpack(">q", packet[pos:pos + 8])[0]

            decoded_value, decoded_unit, scale_hint = self._decode_obis_value(
                obis_num=obis_num,
                length=obis_length,
                raw_value=raw_value,
            )

            entry = {
                "obis_num": obis_num,
                "length": obis_length,
                "raw_value": raw_value,
                "decoded_value": decoded_value,
                "decoded_unit": decoded_unit,
                "scale_hint": scale_hint,
                "channel": channel,
                "index": index,
                "measurement_type": measurement_type,
                "tariff": tariff,
            }
            entries.append(entry)

            if obis_num == 0x00010400:
                active_plus_w = float(decoded_value)
            elif obis_num == 0x00020400:
                active_minus_w = float(decoded_value)

            pos += obis_length

        packet_meta = {
            "device_address_hex": device_address_hex,
            "susy_id": susy_id,
            "serial_number": serial_number,
            "measuring_time_ms": measuring_time_ms,
        }
        self._dump_obis_entries(
            entries=entries,
            packet_meta=packet_meta,
            active_plus_w=active_plus_w,
            active_minus_w=active_minus_w,
        )

        self.debug_info["last_packet_entry_count"] = len(entries)
        self.debug_info["last_packet_channels"] = sorted({entry["channel"] for entry in entries})
        self.debug_info["last_packet_obis_ids"] = [f"0x{entry['obis_num']:08x}" for entry in entries]
        self.debug_info["last_packet_manufacturer_specific_count"] = sum(
            1
            for entry in entries
            if self._is_manufacturer_specific(
                entry["channel"],
                entry["index"],
                entry["measurement_type"],
                entry["tariff"],
            )
        )
        self.debug_info["has_active_plus"] = active_plus_w is not None
        self.debug_info["has_active_minus"] = active_minus_w is not None
        self.debug_info["last_used_active_plus_obis"] = "0x00010400" if active_plus_w is not None else None
        self.debug_info["last_used_active_minus_obis"] = "0x00020400" if active_minus_w is not None else None

        if active_plus_w is None or active_minus_w is None:
            missing = []
            if active_plus_w is None:
                missing.append("active_plus")
            if active_minus_w is None:
                missing.append("active_minus")
            raise IncompleteSmaPacketError("missing_" + "_and_".join(missing))

        grid_power_w = active_plus_w - active_minus_w

        self.debug_info["active_plus_w"] = active_plus_w
        self.debug_info["active_minus_w"] = active_minus_w
        self.debug_info["grid_power_w"] = grid_power_w

        return EnergySnapshot(
            grid_power_w=grid_power_w,
            pv_power_w=None,
            house_power_w=None,
            updated_at=datetime.now(UTC),
            source="sma_meter_protocol",
            quality="live",
        )
