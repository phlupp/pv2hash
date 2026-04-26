import asyncio
import errno
import socket
import struct
import threading
import time
from datetime import UTC, datetime

from pv2hash.logging_ext.setup import get_logger
from pv2hash.models.energy import EnergySnapshot
from pv2hash.netutils import get_local_ipv4_addresses
from pv2hash.sources.base import EnergySource


logger = get_logger("pv2hash.source.sma_meter_protocol")


class IncompleteSmaPacketError(ValueError):
    pass


class SmaMeterProtocolSource(EnergySource):
    driver_id = "sma_meter_protocol"
    driver_label = "SMA Energy Meter"

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
    FREQUENCY_INDEXES = {14}
    COSPHI_INDEXES = {13}
    KNOWN_SUSY_DEVICE_NAMES = {
        270: "SMA Energy Meter",
        349: "SMA Energy Meter 2.0",
        372: "SMA Home Manager 2.0",
        501: "SMA Home Manager 2.0",
        502: "SMA Energy Meter 2.0",
    }

    def __init__(
        self,
        multicast_ip: str = "239.12.255.254",
        bind_port: int = 9522,
        interface_ip: str = "0.0.0.0",
        packet_timeout_seconds: float = 1.0,
        stale_after_seconds: float = 8.0,
        offline_after_seconds: float = 30.0,
        device_serial_number: str | int | None = "",
        debug_dump_obis: bool = False,
    ) -> None:
        self.multicast_ip = multicast_ip
        self.bind_port = bind_port
        self.interface_ip = interface_ip.strip() or "0.0.0.0"
        self.packet_timeout_seconds = packet_timeout_seconds
        self.stale_after_seconds = stale_after_seconds
        self.offline_after_seconds = offline_after_seconds
        self.device_serial_number = self._normalize_serial_number(device_serial_number)
        self._seen_devices: dict[str, dict] = {}

        self.last_snapshot: EnergySnapshot | None = None
        self.last_live_packet_at: datetime | None = None

        self.debug_dump_obis = bool(debug_dump_obis)
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
            "device_serial_number_filter": self.device_serial_number or None,
            "seen_devices": [],
            "selected_device_name": None,
            "selected_device_susy_id": None,
            "multicast_ip": self.multicast_ip,
            "bind_port": self.bind_port,
            "interface_ip": self.interface_ip,
            "effective_interface_ip": None,
            "interface_fallback_active": False,
            "multicast_joined": False,
            "active_plus_w": None,
            "active_minus_w": None,
            "grid_power_w": None,
            "frequency_hz": None,
            "phase_values": {
                "L1": {"voltage_v": None, "current_a": None, "power_w": None},
                "L2": {"voltage_v": None, "current_a": None, "power_w": None},
                "L3": {"voltage_v": None, "current_a": None, "power_w": None},
            },
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
            "last_packet_device_name": None,
            "last_packet_entry_count": 0,
            "last_packet_channels": [],
            "last_packet_obis_ids": [],
            "last_packet_manufacturer_specific_count": 0,
        }

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self.sock = self._create_socket()
        self._receiver_thread = threading.Thread(
            target=self._receiver_loop,
            name="pv2hash-sma-receiver",
            daemon=True,
        )
        self._receiver_thread.start()

    @classmethod
    def config_fields_from_settings(cls, *, settings: dict | None = None, debug_info: dict | None = None, defaults: dict | None = None) -> list[dict]:
        settings = settings or {}
        debug_info = debug_info or {}
        defaults = defaults or {}
        device_options = []
        for item in debug_info.get("seen_devices", []) or []:
            serial = cls._normalize_serial_number(item.get("serial_number"))
            if not serial:
                continue
            label = f"{item.get('device_name') or 'SMA Gerät'} – S/N {serial}"
            sender_ip = str(item.get("sender_ip") or "").strip()
            if sender_ip:
                label += f" – {sender_ip}"
            device_options.append({"value": serial, "label": label})

        interface_value = settings.get("interface_ip", defaults.get("interface_ip", "0.0.0.0"))
        interface_options = [
            {"value": item.get("address", ""), "label": item.get("label", item.get("address", ""))}
            for item in get_local_ipv4_addresses()
        ]
        if interface_value and all(str(opt.get("value")) != str(interface_value) for opt in interface_options):
            interface_options.append({"value": str(interface_value), "label": f"Aktuell konfiguriert — {interface_value}"})

        serial_help = None
        if not device_options:
            serial_help = "Bitte Geräte-Suche starten."

        return [
            {"name": "multicast_ip", "label": "Multicast-IP", "type": "text", "value": settings.get("multicast_ip", defaults.get("multicast_ip", "239.12.255.254")), "layout": {"width": "half"}},
            {"name": "bind_port", "label": "Bind-Port", "type": "number", "value": settings.get("bind_port", defaults.get("bind_port", 9522)), "step": 1, "layout": {"width": "quarter"}},
            {"name": "interface_ip", "label": "Lokale Interface-IP / Automatisch", "type": "select", "value": interface_value or "0.0.0.0", "options": interface_options, "layout": {"width": "half"}},
            {"name": "device_serial_number", "label": "SMA-Gerät / Seriennummer", "type": "select", "value": settings.get("device_serial_number", defaults.get("device_serial_number", "")), "options": device_options, "required": True, "help": serial_help, "layout": {"width": "half"}},
            {"name": "packet_timeout_seconds", "label": "Paket-Timeout", "type": "number", "value": settings.get("packet_timeout_seconds", defaults.get("packet_timeout_seconds", 1.0)), "unit": "s", "step": 0.1, "layout": {"width": "third"}},
            {"name": "stale_after_seconds", "label": "Veraltet nach", "type": "number", "value": settings.get("stale_after_seconds", defaults.get("stale_after_seconds", 8.0)), "unit": "s", "step": 0.1, "layout": {"width": "third"}},
            {"name": "offline_after_seconds", "label": "Offline nach", "type": "number", "value": settings.get("offline_after_seconds", defaults.get("offline_after_seconds", 30.0)), "unit": "s", "step": 0.1, "layout": {"width": "third"}},
        ]

    def get_config_fields(self, *, config: dict | None = None) -> list[dict]:
        settings = (config or {}).get("settings", {}) or {}
        defaults = {
            "multicast_ip": self.multicast_ip,
            "bind_port": self.bind_port,
            "interface_ip": self.interface_ip,
            "device_serial_number": self.device_serial_number,
            "packet_timeout_seconds": self.packet_timeout_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            "offline_after_seconds": self.offline_after_seconds,
        }
        return self.config_fields_from_settings(settings=settings, debug_info=self.debug_info, defaults=defaults)


    def get_actions(self, *, config: dict | None = None) -> list[dict]:
        return [
            {
                "id": "sma_device_search",
                "label": "Geräte-Suche",
                "style": "secondary",
                "help": "Sammelt kurz SMA-Telegramme und aktualisiert die Geräteauswahl.",
            }
        ]

    async def discover_devices(self, *, seconds: float = 4.0) -> list[dict]:
        deadline = time.monotonic() + max(0.5, float(seconds))
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            if self._seen_devices:
                # Keep listening for the full short window so multiple SMA senders can appear.
                continue
        with self._lock:
            return list(self.debug_info.get("seen_devices", []) or [])

    async def run_action(self, action: str, *, config: dict | None = None) -> dict:
        if action != "sma_device_search":
            return await super().run_action(action, config=config)
        devices = await self.discover_devices(seconds=4.0)
        return {
            "status": "ok",
            "message": f"Geräte-Suche abgeschlossen: {len(devices)} Gerät(e) gefunden.",
            "debug_info": {"seen_devices": devices},
        }




    def get_header_fields(self, *, snapshot=None, debug_info: dict | None = None, status: dict | None = None, detail_groups=None) -> list[dict]:
        debug_info = debug_info or self.debug_info
        fields = super().get_header_fields(snapshot=snapshot, debug_info=debug_info, status=status, detail_groups=detail_groups)
        grid_power_w = getattr(snapshot, "grid_power_w", None) if snapshot is not None else debug_info.get("grid_power_w")
        fields.append({"label": "Gerät", "value": debug_info.get("last_packet_device_name") or self.driver_label})
        fields.append({"label": "Leistung", "value": grid_power_w, "unit": "W", "precision": 0})
        return fields

    def get_detail_groups(self, *, snapshot=None, debug_info: dict | None = None) -> list[dict]:
        debug_info = debug_info or self.debug_info
        phase_values = debug_info.get("phase_values") or {}

        fields = [
            {"label": "Frequenz", "value": debug_info.get("frequency_hz"), "unit": "Hz", "precision": 2},
            {"label": "L1 Spannung", "value": (phase_values.get("L1") or {}).get("voltage_v"), "unit": "V", "precision": 1},
            {"label": "L1 Strom", "value": (phase_values.get("L1") or {}).get("current_a"), "unit": "A", "precision": 2},
            {"label": "L1 Leistung", "value": (phase_values.get("L1") or {}).get("power_w"), "unit": "W", "precision": 0},
            {"label": "L2 Spannung", "value": (phase_values.get("L2") or {}).get("voltage_v"), "unit": "V", "precision": 1},
            {"label": "L2 Strom", "value": (phase_values.get("L2") or {}).get("current_a"), "unit": "A", "precision": 2},
            {"label": "L2 Leistung", "value": (phase_values.get("L2") or {}).get("power_w"), "unit": "W", "precision": 0},
            {"label": "L3 Spannung", "value": (phase_values.get("L3") or {}).get("voltage_v"), "unit": "V", "precision": 1},
            {"label": "L3 Strom", "value": (phase_values.get("L3") or {}).get("current_a"), "unit": "A", "precision": 2},
            {"label": "L3 Leistung", "value": (phase_values.get("L3") or {}).get("power_w"), "unit": "W", "precision": 0},
            {"label": "Seriennummer", "value": debug_info.get("last_packet_serial_number")},
            {"label": "SUSy-ID", "value": debug_info.get("last_packet_susy_id")},
        ]
        return [{"title": "Details", "fields": fields}] if any(field.get("value") not in (None, "") for field in fields) else []

    def _join_multicast(self, sock: socket.socket, interface_ip: str) -> str:
        requested_interface_ip = interface_ip.strip() or "0.0.0.0"

        try:
            membership = socket.inet_aton(self.multicast_ip) + socket.inet_aton(requested_interface_ip)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            return requested_interface_ip
        except OSError as exc:
            if requested_interface_ip == "0.0.0.0":
                raise

            logger.warning(
                "Configured SMA interface IP %s is not usable for multicast join (%s). Falling back to 0.0.0.0.",
                requested_interface_ip,
                exc,
            )
            membership = socket.inet_aton(self.multicast_ip) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            self.debug_info["interface_fallback_active"] = True
            return "0.0.0.0"

    def _create_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind(("", self.bind_port))
        except OSError:
            sock.bind(("0.0.0.0", self.bind_port))

        effective_interface_ip = self._join_multicast(sock, self.interface_ip)
        sock.settimeout(self.packet_timeout_seconds)

        self.debug_info["multicast_joined"] = True
        self.debug_info["effective_interface_ip"] = effective_interface_ip

        logger.info(
            "SMA meter protocol source initialized: multicast=%s port=%s interface=%s effective_interface=%s serial_filter=%s",
            self.multicast_ip,
            self.bind_port,
            self.interface_ip,
            effective_interface_ip,
            self.device_serial_number or "-",
        )

        return sock

    def _recv_packet_batch(self) -> list[tuple[bytes, tuple]]:
        """Receive one blocking packet, then drain already queued UDP packets.

        SMA telegrams are sent continuously via UDP multicast. If the control loop
        is slower than the telegram rate, the kernel socket buffer can contain old
        packets. Draining the buffer avoids accumulating latency over time.
        """
        if self._stop_event.is_set():
            return []

        packets = [self.sock.recvfrom(4096)]
        old_timeout = self.sock.gettimeout()

        try:
            self.sock.settimeout(0.0)
            while len(packets) < 512 and not self._stop_event.is_set():
                try:
                    packets.append(self.sock.recvfrom(4096))
                except (BlockingIOError, InterruptedError, socket.timeout):
                    break
        finally:
            try:
                self.sock.settimeout(old_timeout)
            except OSError:
                if not self._stop_event.is_set():
                    raise

        return packets

    def _prepare_packet_debug(self, data: bytes, addr: tuple) -> None:
        sender_ip, sender_port = addr[0], addr[1]
        self.debug_info["last_sender_ip"] = sender_ip
        self.debug_info["last_sender_port"] = sender_port
        self.debug_info["last_packet_len"] = len(data)
        self.debug_info["last_packet_decision"] = None
        self.debug_info["last_packet_rejected_reason"] = None

    async def read(self) -> EnergySnapshot:
        return self._fallback_snapshot()

    def _receiver_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._receive_once()
            except Exception:
                logger.exception("Unhandled error in SMA receiver loop")
                time.sleep(0.5)

    def _receive_once(self) -> None:
        try:
            packets = self._recv_packet_batch()
            if not packets:
                return
            with self._lock:
                self.debug_info["received_packets"] += len(packets)
                if len(packets) > 1:
                    self.debug_info["ignored_packets"] += len(packets) - 1

            for data, addr in reversed(packets):
                with self._lock:
                    self._prepare_packet_debug(data, addr)
                sender_ip = addr[0]

                proto_index = data.find(self.EMETER_PROTOCOL_ID)
                if proto_index < 0:
                    with self._lock:
                        other_index = data.find(b"\x60\x65")
                        self.debug_info["last_protocol"] = "0x6065" if other_index >= 0 else "unknown"
                        self.debug_info["last_packet_decision"] = "ignored"
                        self.debug_info["last_packet_rejected_reason"] = "protocol_not_0x6069"
                    continue

                packet_meta = self._parse_packet_meta(data, proto_index=proto_index)
                with self._lock:
                    self.debug_info["last_protocol"] = "0x6069"
                    self._store_seen_device(packet_meta, sender_ip)

                packet_serial = self._normalize_serial_number(packet_meta["serial_number"])
                if self.device_serial_number and packet_serial != self.device_serial_number:
                    with self._lock:
                        self.debug_info["last_packet_decision"] = "filtered"
                        self.debug_info["last_packet_rejected_reason"] = "serial_number_mismatch"
                    continue

                snapshot = self._parse_emeter_packet(data, proto_index=proto_index, packet_meta=packet_meta)
                with self._lock:
                    self.last_snapshot = snapshot
                    self.last_live_packet_at = snapshot.updated_at
                    self.debug_info["parsed_packets"] += 1
                    self.debug_info["last_error"] = None
                    self.debug_info["last_live_packet_at"] = snapshot.updated_at.isoformat()
                    self.debug_info["last_packet_decision"] = "accepted"
                    self._set_quality("live")
                return

        except IncompleteSmaPacketError as exc:
            with self._lock:
                self.debug_info["incomplete_packets"] += 1
                self.debug_info["last_packet_decision"] = "rejected"
                self.debug_info["last_packet_rejected_reason"] = str(exc)
                self.debug_info["last_error"] = None
            logger.debug("Rejected SMA packet: %s", exc)
        except (TimeoutError, socket.timeout):
            with self._lock:
                self.debug_info["timeouts"] += 1
            self._fallback_snapshot()
        except OSError as exc:
            if self._stop_event.is_set() or getattr(exc, "errno", None) in (errno.EBADF, errno.ENOTSOCK):
                return
            with self._lock:
                self.debug_info["parse_errors"] += 1
                self.debug_info["last_error"] = str(exc)
                self.debug_info["last_packet_decision"] = "error"
            logger.exception("Error while reading SMA meter protocol packet")
        except Exception as exc:
            with self._lock:
                self.debug_info["parse_errors"] += 1
                self.debug_info["last_error"] = str(exc)
                self.debug_info["last_packet_decision"] = "error"
            logger.exception("Error while reading SMA meter protocol packet")

    def close(self) -> None:
        self._stop_event.set()
        try:
            self.sock.close()
        except Exception:
            pass
        thread = getattr(self, "_receiver_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _set_quality(self, quality: str) -> None:
        self.debug_info["current_quality"] = quality
        if quality != self._last_logged_quality:
            logger.info("SMA source quality changed to %s", quality)
            self._last_logged_quality = quality

    def _fallback_snapshot(self) -> EnergySnapshot:
        now = datetime.now(UTC)

        with self._lock:
            last_snapshot = self.last_snapshot
            last_live_packet_at = self.last_live_packet_at

        if last_snapshot is not None and last_live_packet_at is not None:
            age = (now - last_live_packet_at).total_seconds()

            if age < self.stale_after_seconds:
                quality = "live"
            elif age < self.offline_after_seconds:
                quality = "stale"
            else:
                quality = "offline"

            self._set_quality(quality)

            return EnergySnapshot(
                grid_power_w=last_snapshot.grid_power_w,
                pv_power_w=last_snapshot.pv_power_w,
                house_power_w=last_snapshot.house_power_w,
                battery_charge_power_w=last_snapshot.battery_charge_power_w,
                battery_discharge_power_w=last_snapshot.battery_discharge_power_w,
                battery_soc_pct=last_snapshot.battery_soc_pct,
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

    @staticmethod
    def _normalize_serial_number(value) -> str:
        text = str(value or "").strip()
        if text == "":
            return ""
        try:
            return str(int(float(text)))
        except Exception:
            return text

    def _apply_packet_meta(self, packet_meta: dict) -> None:
        susy_id = packet_meta["susy_id"]
        serial_number = packet_meta["serial_number"]
        self.debug_info["last_packet_device_address_hex"] = packet_meta["device_address_hex"]
        self.debug_info["last_packet_susy_id"] = susy_id
        self.debug_info["last_packet_serial_number"] = serial_number
        self.debug_info["last_packet_measuring_time_ms"] = packet_meta["measuring_time_ms"]
        self.debug_info["last_packet_device_name"] = packet_meta["device_name"]
        self.debug_info["last_protocol_offset"] = packet_meta["proto_index"]

    def _store_seen_device(self, packet_meta: dict, sender_ip: str) -> None:
        serial_key = self._normalize_serial_number(packet_meta["serial_number"])
        if serial_key == "":
            return

        current = self._seen_devices.get(serial_key, {})
        packet_count = int(current.get("packet_count", 0)) + 1
        stored = {
            "serial_number": serial_key,
            "susy_id": packet_meta["susy_id"],
            "device_name": packet_meta["device_name"],
            "sender_ip": sender_ip,
            "last_seen_at": datetime.now(UTC).isoformat(),
            "packet_count": packet_count,
        }
        self._seen_devices[serial_key] = stored
        self.debug_info["seen_devices"] = sorted(
            self._seen_devices.values(),
            key=lambda item: (
                str(item.get("device_name") or "").lower(),
                self._normalize_serial_number(item.get("serial_number")),
            ),
        )

        if self.device_serial_number and serial_key == self.device_serial_number:
            self.debug_info["selected_device_name"] = packet_meta["device_name"]
            self.debug_info["selected_device_susy_id"] = packet_meta["susy_id"]

    def _parse_packet_meta(self, packet: bytes, *, proto_index: int) -> dict:
        if proto_index < 0:
            raise ValueError("Invalid protocol offset")

        min_len = proto_index + 12
        if len(packet) < min_len:
            raise ValueError(
                f"Packet too short for protocol block at offset {proto_index}: {len(packet)}"
            )

        protocol_id = struct.unpack(">H", packet[proto_index:proto_index + 2])[0]
        if protocol_id != 0x6069:
            raise ValueError(f"Unexpected protocol id: 0x{protocol_id:04x}")

        susy_id = struct.unpack(">H", packet[proto_index + 2:proto_index + 4])[0]
        serial_number = struct.unpack(">I", packet[proto_index + 4:proto_index + 8])[0]
        measuring_time_ms = struct.unpack(">I", packet[proto_index + 8:proto_index + 12])[0]
        device_address_hex = packet[proto_index + 2:proto_index + 8].hex()

        return {
            "proto_index": proto_index,
            "device_address_hex": device_address_hex,
            "susy_id": susy_id,
            "serial_number": serial_number,
            "measuring_time_ms": measuring_time_ms,
            "device_name": self._resolve_device_name(susy_id),
        }

    def _format_obis_num(self, obis_num: int) -> str:
        channel, index, measurement_type, tariff = self._extract_obis_parts(obis_num)
        return (
            f"{obis_num:08x} / channel={channel} / "
            f"1-{channel}:{index}.{measurement_type}.{tariff}"
        )

    def _resolve_device_name(self, susy_id: int) -> str:
        return self.KNOWN_SUSY_DEVICE_NAMES.get(susy_id, f"SMA Gerät (SUSy-ID {susy_id})")

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
        if index in self.FREQUENCY_INDEXES:
            return raw_value / 1000.0, "Hz", "current_average"
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
            self.device_serial_number or "-",
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

    def _parse_emeter_packet(self, packet: bytes, *, proto_index: int, packet_meta: dict | None = None) -> EnergySnapshot:
        packet_meta = packet_meta or self._parse_packet_meta(packet, proto_index=proto_index)
        self._apply_packet_meta(packet_meta)

        susy_id = packet_meta["susy_id"]
        serial_number = packet_meta["serial_number"]
        measuring_time_ms = packet_meta["measuring_time_ms"]
        device_address_hex = packet_meta["device_address_hex"]

        pos = proto_index + 12
        active_plus_w: float | None = None
        active_minus_w: float | None = None
        frequency_hz: float | None = None
        phase_active_plus_w: dict[str, float | None] = {"L1": None, "L2": None, "L3": None}
        phase_active_minus_w: dict[str, float | None] = {"L1": None, "L2": None, "L3": None}
        phase_values: dict[str, dict[str, float | None]] = {
            "L1": {"voltage_v": None, "current_a": None, "power_w": None},
            "L2": {"voltage_v": None, "current_a": None, "power_w": None},
            "L3": {"voltage_v": None, "current_a": None, "power_w": None},
        }
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
            elif index in self.FREQUENCY_INDEXES and decoded_unit == "Hz":
                frequency_hz = float(decoded_value)
            elif index in self.CURRENT_INDEXES and decoded_unit == "A":
                phase = {31: "L1", 51: "L2", 71: "L3"}.get(index)
                if phase:
                    phase_values[phase]["current_a"] = float(decoded_value)
            elif index in self.VOLTAGE_INDEXES and decoded_unit == "V":
                phase = {32: "L1", 52: "L2", 72: "L3"}.get(index)
                if phase:
                    phase_values[phase]["voltage_v"] = float(decoded_value)
            elif index in self.POWER_INDEXES and decoded_unit == "W":
                phase = {21: "L1", 41: "L2", 61: "L3"}.get(index)
                if phase:
                    phase_active_plus_w[phase] = float(decoded_value)
                phase = {22: "L1", 42: "L2", 62: "L3"}.get(index)
                if phase:
                    phase_active_minus_w[phase] = float(decoded_value)

            pos += obis_length

        packet_meta = {
            "device_address_hex": device_address_hex,
            "susy_id": susy_id,
            "serial_number": serial_number,
            "measuring_time_ms": measuring_time_ms,
            "device_name": self._resolve_device_name(susy_id),
            "proto_index": proto_index,
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
        for phase in ("L1", "L2", "L3"):
            plus_w = phase_active_plus_w.get(phase)
            minus_w = phase_active_minus_w.get(phase)
            if plus_w is not None and minus_w is not None:
                phase_values[phase]["power_w"] = plus_w - minus_w

        self.debug_info["active_plus_w"] = active_plus_w
        self.debug_info["active_minus_w"] = active_minus_w
        self.debug_info["grid_power_w"] = grid_power_w
        self.debug_info["frequency_hz"] = frequency_hz
        self.debug_info["phase_values"] = phase_values

        return EnergySnapshot(
            grid_power_w=grid_power_w,
            pv_power_w=None,
            house_power_w=None,
            updated_at=datetime.now(UTC),
            source="sma_meter_protocol",
            quality="live",
        )
