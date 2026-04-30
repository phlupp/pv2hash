from __future__ import annotations

import json
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from ipaddress import ip_network
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pv2hash.sockets.base import SocketAdapter, SocketInfo


class TasmotaHttpSocket(SocketAdapter):
    driver_id = "tasmota_http"
    driver_label = "Tasmota HTTP"

    def __init__(self, info: SocketInfo, settings: dict[str, Any] | None = None) -> None:
        super().__init__(info, settings)
        self.port = int(self.settings.get("port", 80) or 80)
        self.relay = int(self.settings.get("relay", 1) or 1)
        self.username = str(self.settings.get("username", "") or "")
        self.password = str(self.settings.get("password", "") or "")
        self.timeout_s = float(self.settings.get("timeout_s", 2.0) or 2.0)
        self.use_energy = bool(self.settings.get("use_energy", True))
        self.last_details: dict[str, Any] = {}

    def _power_command(self) -> str:
        return "Power" if self.relay <= 1 else f"Power{self.relay}"

    def _request_command(self, command: str, *, timeout_s: float | None = None) -> dict[str, Any]:
        host = str(self.info.host or "").strip()
        if not host:
            raise RuntimeError("Tasmota Host/IP fehlt.")

        query: dict[str, str] = {"cmnd": command}
        if self.username:
            query["user"] = self.username
        if self.password:
            query["password"] = self.password

        url = f"http://{host}:{self.port}/cm?{urlencode(query)}"
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "PV2Hash"})
        try:
            with urlopen(request, timeout=float(timeout_s or self.timeout_s)) as response:
                raw = response.read(256 * 1024).decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise RuntimeError(f"Tasmota HTTP Fehler {exc.code}") from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise RuntimeError(f"Tasmota nicht erreichbar: {exc}") from exc

        try:
            parsed = json.loads(raw)
        except Exception as exc:
            raise RuntimeError("Tasmota Antwort ist kein gültiges JSON.") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Tasmota Antwort hat ein unerwartetes Format.")
        return parsed

    @staticmethod
    def _nested_get(data: dict[str, Any], path: list[str], default: Any = None) -> Any:
        current: Any = data
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current

    @staticmethod
    def _find_first_key(data: Any, names: set[str]) -> Any:
        if isinstance(data, dict):
            for key, value in data.items():
                if str(key).upper() in names:
                    return value
            for value in data.values():
                found = TasmotaHttpSocket._find_first_key(value, names)
                if found is not None:
                    return found
        elif isinstance(data, list):
            for value in data:
                found = TasmotaHttpSocket._find_first_key(value, names)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _as_on(value: Any) -> bool | None:
        if value is None:
            return None
        text = str(value).strip().upper()
        if text in {"ON", "1", "TRUE"}:
            return True
        if text in {"OFF", "0", "FALSE"}:
            return False
        return None

    def _parse_state(self, power_payload: dict[str, Any], status_payload: dict[str, Any] | None = None) -> bool | None:
        command = self._power_command().upper()
        candidates = {command, "POWER", "POWER1"}
        state = self._as_on(self._find_first_key(power_payload, candidates))
        if state is not None:
            return state
        if status_payload:
            return self._as_on(self._find_first_key(status_payload, candidates))
        return None

    def _parse_power_w(self, sensor_payload: dict[str, Any] | None) -> float | None:
        if not sensor_payload:
            return None
        energy = self._find_first_key(sensor_payload, {"ENERGY"})
        if isinstance(energy, dict):
            power = self._as_float(energy.get("Power"))
            if power is not None:
                return power
        return self._as_float(self._find_first_key(sensor_payload, {"POWER"}))

    def _parse_details(self, status_payload: dict[str, Any] | None, sensor_payload: dict[str, Any] | None) -> dict[str, Any]:
        details: dict[str, Any] = {}
        status_payload = status_payload or {}
        sensor_payload = sensor_payload or {}
        status = status_payload.get("Status") if isinstance(status_payload.get("Status"), dict) else {}
        status_fwr = status_payload.get("StatusFWR") if isinstance(status_payload.get("StatusFWR"), dict) else {}
        status_prm = status_payload.get("StatusPRM") if isinstance(status_payload.get("StatusPRM"), dict) else {}
        status_sts = status_payload.get("StatusSTS") if isinstance(status_payload.get("StatusSTS"), dict) else {}
        status_net = status_payload.get("StatusNET") if isinstance(status_payload.get("StatusNET"), dict) else {}

        device_name = status.get("DeviceName") or status_prm.get("DeviceName")
        friendly = status.get("FriendlyName") or status_prm.get("FriendlyName")
        if isinstance(friendly, list):
            friendly_name = friendly[0] if friendly else None
        else:
            friendly_name = friendly

        energy = self._find_first_key(sensor_payload, {"ENERGY"})
        if not isinstance(energy, dict):
            energy = {}

        wifi = status_sts.get("Wifi") if isinstance(status_sts.get("Wifi"), dict) else {}

        mapping = {
            "device_name": device_name,
            "friendly_name": friendly_name,
            "topic": status.get("Topic") or status_prm.get("Topic"),
            "firmware": status_fwr.get("Version"),
            "hardware": status_fwr.get("Hardware"),
            "uptime": status_sts.get("Uptime"),
            "ip_address": status_net.get("IPAddress") or status_sts.get("IPAddress"),
            "wifi_rssi": wifi.get("RSSI"),
            "wifi_signal": wifi.get("Signal"),
            "voltage_v": self._as_float(energy.get("Voltage")),
            "current_a": self._as_float(energy.get("Current")),
            "energy_today_kwh": self._as_float(energy.get("Today")),
            "energy_yesterday_kwh": self._as_float(energy.get("Yesterday")),
            "energy_total_kwh": self._as_float(energy.get("Total")),
            "esp_temperature_c": self._as_float(
                self._find_first_key(sensor_payload, {"ESP32TEMPERATURE", "ESPTEMPERATURE", "TEMPERATURE"})
            ),
        }
        for key, value in mapping.items():
            if value is not None and value != "":
                details[key] = value
        return details

    def _set_offline(self, message: str) -> SocketInfo:
        self.info.reachable = False
        self.info.quality = "offline" if self.info.last_seen else "no_data"
        self.info.runtime_state = "unreachable"
        self.info.is_on = None
        self.info.power_w = None
        self.info.last_error = message
        self.info.details = dict(self.last_details)
        return self.info

    def get_status(self) -> SocketInfo:
        if not self.info.enabled or not self.info.monitor_enabled:
            self.info.reachable = False
            self.info.quality = "no_data"
            self.info.runtime_state = "disabled"
            self.info.is_on = None
            self.info.last_error = None
            return self.info

        try:
            status_payload = self._request_command("Status 0")
            sensor_payload = status_payload
            if self.use_energy and not isinstance(self._find_first_key(sensor_payload, {"ENERGY"}), dict):
                sensor_payload = self._request_command("Status 8")
            state = self._parse_state(status_payload, status_payload)
            power_w = self._parse_power_w(sensor_payload) if self.use_energy else None
            details = self._parse_details(status_payload, sensor_payload)
            self.last_details.update(details)

            self.info.reachable = True
            self.info.quality = "live"
            self.info.is_on = state
            self.info.power_w = power_w
            self.info.runtime_state = "on" if state is True else "off" if state is False else "unknown"
            self.info.last_seen = datetime.now(UTC)
            self.info.last_error = None
            self.info.details = dict(self.last_details)
            return self.info
        except Exception as exc:
            return self._set_offline(str(exc))

    def get_details(self) -> dict[str, Any]:
        try:
            status_payload = self._request_command("Status 0")
            sensor_payload = self._request_command("Status 8") if self.use_energy else {}
            details = self._parse_details(status_payload, sensor_payload)
            self.last_details.update(details)
            self.info.details = dict(self.last_details)
            return dict(self.last_details)
        except Exception:
            return dict(self.last_details)

    def _set_power(self, value: str) -> dict[str, Any]:
        if not self.info.enabled or not self.info.monitor_enabled:
            return {"ok": False, "message": "Socket ist deaktiviert."}
        try:
            payload = self._request_command(f"{self._power_command()} {value}")
            self.get_status()
            return {"ok": True, "message": f"Tasmota Socket {value.lower()} geschaltet.", "response": payload}
        except Exception as exc:
            self._set_offline(str(exc))
            return {"ok": False, "message": str(exc)}

    def switch_on(self) -> dict[str, Any]:
        return self._set_power("ON")

    def switch_off(self) -> dict[str, Any]:
        return self._set_power("OFF")

    def reboot(self) -> dict[str, Any]:
        if not self.info.enabled or not self.info.monitor_enabled:
            return {"ok": False, "message": "Socket ist deaktiviert."}
        try:
            payload = self._request_command("Restart 1")
            self.info.last_error = None
            return {"ok": True, "message": "Tasmota Neustart ausgelöst.", "response": payload}
        except Exception as exc:
            self._set_offline(str(exc))
            return {"ok": False, "message": str(exc)}

    @classmethod
    def probe(cls, host: str, *, port: int = 80, timeout_s: float = 0.5) -> dict[str, Any] | None:
        info = SocketInfo(id="probe", uuid="", name="Tasmota", driver=cls.driver_id, host=host)
        adapter = cls(info=info, settings={"port": port, "timeout_s": timeout_s, "use_energy": True})
        try:
            status_payload = adapter._request_command("Status 0", timeout_s=timeout_s)
            sensor_payload = adapter._request_command("Status 8", timeout_s=timeout_s)
            details = adapter._parse_details(status_payload, sensor_payload)
            power_payload = adapter._request_command("Power", timeout_s=timeout_s)
            is_on = adapter._parse_state(power_payload, status_payload)
            power_w = adapter._parse_power_w(sensor_payload)
            name = str(details.get("device_name") or details.get("friendly_name") or f"Tasmota {host}")
            return {
                "host": host,
                "port": port,
                "name": name,
                "device_name": details.get("device_name"),
                "friendly_name": details.get("friendly_name"),
                "firmware": details.get("firmware"),
                "is_on": is_on,
                "power_w": power_w,
                "details": details,
            }
        except Exception:
            return None


def discover_tasmota_http(cidr: str, *, port: int = 80, timeout_s: float = 0.45, max_workers: int = 32, max_hosts: int = 512) -> list[dict[str, Any]]:
    network = ip_network(cidr, strict=False)
    hosts = [str(ip) for ip in network.hosts()]
    if len(hosts) > max_hosts:
        hosts = hosts[:max_hosts]

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 64))) as executor:
        futures = {executor.submit(TasmotaHttpSocket.probe, host, port=port, timeout_s=timeout_s): host for host in hosts}
        for future in as_completed(futures):
            item = future.result()
            if item:
                results.append(item)
    results.sort(key=lambda item: tuple(int(part) if part.isdigit() else 0 for part in str(item.get("host", "0.0.0.0")).split(".")))
    return results
