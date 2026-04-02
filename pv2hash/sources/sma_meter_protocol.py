import asyncio
import socket
import struct
from datetime import UTC, datetime

from pv2hash.logging_ext.setup import get_logger
from pv2hash.models.energy import EnergySnapshot
from pv2hash.sources.base import EnergySource


logger = get_logger("pv2hash.source.sma_meter_protocol")


class SmaMeterProtocolSource(EnergySource):
    EMETER_PROTOCOL_ID = b"\x60\x69"

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

        self.debug_dump_obis = False
        self._last_obis_signature: str | None = None
        self._last_logged_quality: str | None = None

        self.debug_info = {
            "received_packets": 0,
            "parsed_packets": 0,
            "ignored_packets": 0,
            "timeouts": 0,
            "parse_errors": 0,
            "last_sender_ip": None,
            "last_sender_port": None,
            "last_packet_len": None,
            "last_protocol": None,
            "last_error": None,
            "device_ip_filter": self.device_ip or None,
            "multicast_ip": self.multicast_ip,
            "bind_port": self.bind_port,
            "interface_ip": self.interface_ip,
            "multicast_joined": False,
            "active_plus_w": None,
            "active_minus_w": None,
            "grid_power_w": None,
            "packet_timeout_seconds": self.packet_timeout_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            "offline_after_seconds": self.offline_after_seconds,
            "last_live_packet_at": None,
            "current_quality": "no_data",
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

            if self.device_ip and sender_ip != self.device_ip:
                self.debug_info["ignored_packets"] += 1
                self.debug_info["last_protocol"] = "filtered_ip"
                return self._fallback_snapshot()

            proto_index = data.find(self.EMETER_PROTOCOL_ID)
            if proto_index < 0:
                other_index = data.find(b"\x60\x65")
                if other_index >= 0:
                    self.debug_info["last_protocol"] = "0x6065"
                else:
                    self.debug_info["last_protocol"] = "unknown"
                self.debug_info["ignored_packets"] += 1
                return self._fallback_snapshot()

            self.debug_info["last_protocol"] = "0x6069"
            snapshot = self._parse_emeter_packet(data)
            self.last_snapshot = snapshot
            self.last_live_packet_at = snapshot.updated_at
            self.debug_info["parsed_packets"] += 1
            self.debug_info["last_error"] = None
            self.debug_info["last_live_packet_at"] = snapshot.updated_at.isoformat()
            self._set_quality("live")

            return snapshot

        except TimeoutError:
            self.debug_info["timeouts"] += 1
        except socket.timeout:
            self.debug_info["timeouts"] += 1
        except Exception as exc:
            self.debug_info["parse_errors"] += 1
            self.debug_info["last_error"] = str(exc)
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

    def _format_obis_num(self, obis_num: int) -> str:
        a = (obis_num >> 24) & 0xFF
        b = (obis_num >> 16) & 0xFF
        c = (obis_num >> 8) & 0xFF
        d = obis_num & 0xFF
        return f"{a:02x}{b:02x}{c:02x}{d:02x} / {a}:{b}.{c}.{d}"

    def _dump_obis_entries(self, entries: list[dict]) -> None:
        if not self.debug_dump_obis:
            return

        interesting = []
        for entry in entries:
            if entry["obis_num"] == 0x00010400:
                interesting.append(entry)
            elif entry["obis_num"] == 0x00020400:
                interesting.append(entry)
            elif entry["length"] == 4 and ((entry["obis_num"] >> 8) & 0xFF) == 4:
                interesting.append(entry)

        if not interesting:
            return

        signature = "|".join(
            f"{entry['obis_num']:08x}:{entry['value']}"
            for entry in interesting
        )

        if signature == self._last_obis_signature:
            return

        self._last_obis_signature = signature

        logger.debug("---- SMA OBIS packet dump start ----")
        for entry in interesting:
            logger.debug(
                "obis=0x%08x (%s) len=%s raw=%s value=%s",
                entry["obis_num"],
                self._format_obis_num(entry["obis_num"]),
                entry["length"],
                entry["raw_value"],
                entry["value"],
            )
        logger.debug("---- SMA OBIS packet dump end ----")

    def _parse_emeter_packet(self, packet: bytes) -> EnergySnapshot:
        if len(packet) < 32:
            raise ValueError(f"Packet too short: {len(packet)}")

        protocol_id = struct.unpack(">H", packet[16:18])[0]
        if protocol_id != 0x6069:
            raise ValueError(f"Unexpected protocol id: 0x{protocol_id:04x}")

        pos = 28
        active_plus_w = 0.0
        active_minus_w = 0.0
        entries: list[dict] = []

        while pos + 4 <= len(packet):
            obis_num = struct.unpack(">I", packet[pos:pos + 4])[0]

            if obis_num == 0 and pos == len(packet) - 4:
                break

            obis_length = (obis_num >> 8) & 0xFF
            pos += 4

            if obis_length not in (4, 8):
                continue

            if pos + obis_length > len(packet):
                break

            if obis_length == 4:
                raw_value = struct.unpack(">i", packet[pos:pos + 4])[0]
                value = raw_value / 10.0
            else:
                raw_value = struct.unpack(">q", packet[pos:pos + 8])[0]
                value = float(raw_value)

            entries.append(
                {
                    "obis_num": obis_num,
                    "length": obis_length,
                    "raw_value": raw_value,
                    "value": value,
                }
            )

            if obis_num == 0x00010400:
                active_plus_w = value
            elif obis_num == 0x00020400:
                active_minus_w = value

            pos += obis_length

        self._dump_obis_entries(entries)

        grid_power_w = -(active_plus_w + active_minus_w)
        pv_power_w = abs(grid_power_w) if grid_power_w < 0 else None

        self.debug_info["active_plus_w"] = active_plus_w
        self.debug_info["active_minus_w"] = active_minus_w
        self.debug_info["grid_power_w"] = grid_power_w

        return EnergySnapshot(
            grid_power_w=grid_power_w,
            pv_power_w=pv_power_w,
            house_power_w=None,
            updated_at=datetime.now(UTC),
            source="sma_meter_protocol",
            quality="live",
        )