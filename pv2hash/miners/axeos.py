from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pv2hash.logging_ext.setup import get_logger
from pv2hash.miners.base import DriverAction, DriverDetailColumn, DriverField, MinerAdapter
from pv2hash.models.miner import MinerInfo, MinerProfile, MinerProfiles

logger = get_logger("pv2hash.miners.axeos")


class AxeOsMiner(MinerAdapter):
    DRIVER_LABEL = "axeOS / ESP-Miner"
    FIXED_PROFILE_POWER_W = 200.0
    OFF_POWER_THRESHOLD_W = 5.0
    DEFAULT_STARTUP_GRACE_S = 30.0

    @classmethod
    def has_fixed_power_profiles(cls) -> bool:
        return True

    @classmethod
    def get_config_schema(cls) -> list[DriverField]:
        return [
            DriverField(name="host", label="Host / IP", type="text", required=True, preset="192.168.x.x", default="", placeholder="192.168.x.x", help="IP-Adresse oder Hostname des axeOS / ESP-Miner Geräts.", create_phase="basic", layout={"width": "half"}),
            DriverField(name="settings.port", label="HTTP-Port", type="number", required=True, preset=80, default=80, placeholder="80", help="HTTP-Port der axeOS API.", create_phase="basic", layout={"width": "quarter"}),
            DriverField(name="settings.timeout_s", label="Timeout", type="number", unit="s", preset=3, default=3, min=1, max=30, step=1, help="HTTP-Timeout für API-Requests.", advanced=True, layout={"width": "quarter"}),
            DriverField(name="settings.startup_grace_s", label="Start-Ramp-up", type="number", unit="s", preset=30, default=30, min=0, max=300, step=5, help="Zeit nach POST /api/system/restart, in der geringe Leistung noch nicht erneut als off gewertet wird. Verhindert Restart-Schleifen beim Hochlaufen.", advanced=True, layout={"width": "quarter"}),
        ]

    @classmethod
    def get_actions_schema(cls) -> list[DriverAction]:
        return [
            DriverAction(name="pause_mining", label="Mining stoppen", description="Sendet POST /api/system/shutdown. PV2Hash wertet den Miner danach bei Leistung < 5 W als off.", confirm_text="Mining auf diesem axeOS-Miner wirklich per Shutdown stoppen?", disabled_when_control_enabled=True),
            DriverAction(name="resume_mining", label="Mining starten", description="Sendet POST /api/system/restart. Wird für axeOS zum Starten nach Shutdown verwendet.", confirm_text="Mining auf diesem axeOS-Miner wirklich per Restart starten?", disabled_when_control_enabled=True),
            DriverAction(name="identify", label="Miner identifizieren", description="Sendet POST /api/system/identify."),
            DriverAction(name="restart_system", label="Miner neu starten", description="Sendet POST /api/system/restart.", confirm_text="axeOS-Miner jetzt wirklich neu starten?", dangerous=True),
        ]

    def __init__(self, miner_id: str, name: str, host: str, port: int = 80, priority: int = 100, enabled: bool = True, serial_number: str | None = None, model: str | None = None, firmware_version: str | None = None, profiles: dict[str, Any] | None = None, min_regulated_profile: str = "off", timeout_s: float = 3.0, use_battery_when_charging: bool = False, battery_charge_soc_min: float = 95.0, battery_charge_profile: str = "p1", use_battery_when_discharging: bool = False, battery_discharge_soc_min: float = 80.0, battery_discharge_profile: str = "p1", startup_grace_s: float = DEFAULT_STARTUP_GRACE_S) -> None:
        self.host = str(host).strip()
        self.port = int(port or 80)
        self.timeout_s = float(timeout_s or 3.0)
        self.startup_grace_s = max(0.0, float(startup_grace_s if startup_grace_s is not None else self.DEFAULT_STARTUP_GRACE_S))
        self.target_profile = "off"
        self._startup_grace_until: datetime | None = None
        self._last_system_info: dict[str, Any] = {}
        self._last_asic_info: dict[str, Any] = {}
        self._last_details_at: datetime | None = None

        profile_cfg = profiles or {name: {"power_w": self.FIXED_PROFILE_POWER_W} for name in ("p1", "p2", "p3", "p4")}
        def profile_power(profile: str) -> float:
            try:
                return float(profile_cfg.get(profile, {}).get("power_w", self.FIXED_PROFILE_POWER_W))
            except Exception:
                return self.FIXED_PROFILE_POWER_W

        miner_profiles = MinerProfiles(
            p1=MinerProfile(power_w=profile_power("p1")),
            p2=MinerProfile(power_w=profile_power("p2")),
            p3=MinerProfile(power_w=profile_power("p3")),
            p4=MinerProfile(power_w=profile_power("p4")),
        )

        self.info = MinerInfo(
            id=miner_id,
            name=name,
            host=self.host,
            driver="axeos",
            enabled=enabled,
            is_active=enabled,
            priority=priority,
            serial_number=serial_number,
            model=model or "axeOS",
            firmware_version=firmware_version,
            profile="off",
            power_w=0.0,
            profiles=miner_profiles,
            min_regulated_profile=min_regulated_profile if min_regulated_profile in {"off", "p1", "p2", "p3", "p4"} else "off",
            use_battery_when_charging=bool(use_battery_when_charging),
            battery_charge_soc_min=float(battery_charge_soc_min),
            battery_charge_profile=battery_charge_profile,
            use_battery_when_discharging=bool(use_battery_when_discharging),
            battery_discharge_soc_min=float(battery_discharge_soc_min),
            battery_discharge_profile=battery_discharge_profile,
            control_mode="start_stop_only",
            power_target_min_w=self.FIXED_PROFILE_POWER_W,
            power_target_default_w=self.FIXED_PROFILE_POWER_W,
            power_target_max_w=self.FIXED_PROFILE_POWER_W,
        )

    def _base_url(self) -> str:
        host = self.host.rstrip("/")
        if host.startswith("http://") or host.startswith("https://"):
            base = host
        else:
            base = f"http://{host}"
        if self.port and self.port != 80:
            after_scheme = base.split("://", 1)[1]
            if ":" not in after_scheme:
                base = f"{base}:{self.port}"
        return base

    def _request_text(self, method: str, path: str, accept: str = "application/json, text/plain, */*") -> str:
        url = f"{self._base_url()}{path}"
        request = Request(url, method=method.upper(), headers={"Accept": accept})
        if method.upper() == "POST":
            request.add_header("Content-Length", "0")
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} for {path}: {exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"HTTP connection failed for {path}: {exc.reason}") from exc

    def _request_json(self, method: str, path: str) -> dict[str, Any]:
        body = self._request_text(method, path, accept="application/json")
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from {path}: {body[:200]!r}") from exc

    def _post_system_command(self, path: str, expected_text: str | None = None) -> dict[str, Any]:
        body = self._request_text("POST", path)
        text = body.strip()
        if expected_text and expected_text.lower() not in text.lower():
            logger.debug("axeOS command %s for %s (%s) returned unexpected text: %r", path, self.info.name, self.host, text[:200])
        return {"text": text}

    def _startup_grace_active(self, now: datetime | None = None) -> bool:
        if self._startup_grace_until is None:
            return False
        now = now or datetime.now(UTC)
        if now >= self._startup_grace_until:
            self._startup_grace_until = None
            return False
        return True

    def _begin_startup_grace(self) -> None:
        if self.startup_grace_s <= 0:
            self._startup_grace_until = None
            return
        self._startup_grace_until = datetime.now(UTC) + timedelta(seconds=self.startup_grace_s)

    def _clear_startup_grace(self) -> None:
        self._startup_grace_until = None

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            if value in (None, ""):
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _text(value: Any, empty: str = "—") -> str:
        if value in (None, ""):
            return empty
        return str(value)

    @staticmethod
    def _yes_no(value: Any) -> str:
        return "Ja" if bool(value) else "Nein"

    @staticmethod
    def _format_seconds(value: Any) -> str:
        try:
            seconds = int(float(value))
        except Exception:
            return "—"
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        if days:
            return f"{days}d {hours}h {minutes}m"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def _refresh_sync(self) -> dict[str, Any]:
        info = self._request_json("GET", "/api/system/info")
        try:
            asic = self._request_json("GET", "/api/system/asic")
        except Exception as exc:
            logger.debug("axeOS ASIC info read failed for %s (%s): %s", self.info.name, self.host, exc)
            asic = {}
        self._last_system_info = info or {}
        self._last_asic_info = asic or {}
        self._last_details_at = datetime.now(UTC)
        return self._last_system_info

    def _apply_system_info(self, payload: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        raw_power = payload.get("power")
        live_power = self._num(raw_power, 0.0)
        off_by_power = raw_power is not None and live_power < self.OFF_POWER_THRESHOLD_W
        desired_active = self.target_profile in {"p1", "p2", "p3", "p4"}
        startup_grace_active = desired_active and self._startup_grace_active(now)
        paused = (bool(payload.get("miningPaused", False)) or off_by_power) and not startup_grace_active
        hashrate_ghs = 0.0 if paused else self._num(payload.get("hashRate"), 0.0)
        self.info.reachable = True
        self.info.last_error = None
        self.info.last_seen = now
        self.info.model = self._text(payload.get("deviceModel") or payload.get("boardVersion") or payload.get("ASICModel"), "axeOS")
        self.info.firmware_version = self._text(payload.get("axeOSVersion") or payload.get("version"), "") or None
        self.info.serial_number = self._text(payload.get("macAddr"), "") or self.info.serial_number
        self.info.api_version = self._text(payload.get("axeOSVersion") or payload.get("version"), "") or None
        self.info.current_hashrate_ghs = hashrate_ghs
        self.info.temp_c = self._num(payload.get("temp"), None)
        self.info.temp_asic_min_c = self._num(payload.get("temp2"), None) or self.info.temp_c
        self.info.temp_asic_max_c = max([v for v in (self.info.temp_c, self.info.temp_asic_min_c) if v is not None], default=None)
        if paused:
            self.info.power_w = live_power if raw_power is not None else 0.0
            self.info.runtime_state = "paused"
            self.info.profile = "off"
        else:
            self.info.power_w = live_power if raw_power is not None else self.get_profile_power_w("p1")
            self.info.runtime_state = "starting" if startup_grace_active and off_by_power else "running"
            self.info.profile = self.target_profile if self.target_profile in {"p1", "p2", "p3", "p4"} else "p1"
            if raw_power is not None and live_power >= self.OFF_POWER_THRESHOLD_W:
                self._clear_startup_grace()
        self.info.is_active = bool(self.info.enabled)

    async def set_profile(self, profile: str) -> None:
        self.target_profile = profile
        self.info.profile = profile
        desired_w = 0.0 if profile == "off" else self.get_profile_power_w(profile)
        try:
            if profile == "off" or desired_w <= 0:
                self._clear_startup_grace()
                response = await asyncio.to_thread(self._post_system_command, "/api/system/shutdown", "shutdown")
                logger.info("axeOS shutdown requested for %s (%s): %s", self.info.name, self.host, response)
                self.info.power_w = 0.0
                self.info.runtime_state = "paused"
            else:
                startup_grace_active = self._startup_grace_active()
                is_currently_off = (self.info.runtime_state in {"paused", "stopped"} or self.info.power_w < self.OFF_POWER_THRESHOLD_W) and not startup_grace_active
                if is_currently_off:
                    response = await asyncio.to_thread(self._post_system_command, "/api/system/restart", "restart")
                    self._begin_startup_grace()
                    logger.info("axeOS restart requested for %s (%s): %s", self.info.name, self.host, response)
                else:
                    logger.debug("axeOS %s (%s) already running or starting; keeping active profile %s without restart", self.info.name, self.host, profile)
                self.info.power_w = max(float(desired_w), self.info.power_w)
                self.info.runtime_state = "starting" if self._startup_grace_active() else "running"
            self.info.reachable = True
            self.info.last_error = None
            self.info.is_active = bool(self.info.enabled)
        except Exception as exc:
            logger.warning("axeOS write failed for %s (%s): %s", self.info.name, self.host, exc)
            self.info.last_error = f"HTTP write failed: {exc}"
            self.info.reachable = False
            self.info.runtime_state = "unreachable"
        self.info.last_seen = datetime.now(UTC)

    async def get_status(self) -> MinerInfo:
        try:
            payload = await asyncio.to_thread(self._refresh_sync)
            self._apply_system_info(payload)
        except Exception as exc:
            self.info.reachable = False
            self.info.runtime_state = "unreachable"
            self.info.last_error = f"HTTP read failed: {exc}"
            self.info.last_seen = datetime.now(UTC)
        return self.info

    def apply_action(self, action_name: str) -> dict[str, Any]:
        path_map = {
            "pause_mining": ("/api/system/shutdown", "Mining per Shutdown gestoppt", "shutdown"),
            "resume_mining": ("/api/system/restart", "Mining per Restart gestartet", "restart"),
            "identify": ("/api/system/identify", "Identify ausgelöst", None),
            "restart_system": ("/api/system/restart", "Neustart ausgelöst", "restart"),
        }
        item = path_map.get(action_name)
        if item is None:
            return {"ok": False, "message": f"Unbekannte axeOS-Aktion: {action_name}"}
        path, message, expected_text = item
        try:
            response = self._post_system_command(path, expected_text)
            if action_name == "resume_mining":
                self.target_profile = self.target_profile if self.target_profile in {"p1", "p2", "p3", "p4"} else "p1"
                self._begin_startup_grace()
            elif action_name == "pause_mining":
                self.target_profile = "off"
                self._clear_startup_grace()
            logger.info("axeOS action %s for %s (%s): %s", action_name, self.info.name, self.host, response)
            return {"ok": True, "message": message, "response": response}
        except Exception as exc:
            logger.warning("axeOS action %s failed for %s (%s): %s", action_name, self.info.name, self.host, exc)
            return {"ok": False, "message": f"API-Verbindungsfehler bei {action_name}: {exc}"}

    def _ensure_cached_details(self) -> None:
        if self._last_system_info:
            return
        try:
            payload = self._refresh_sync()
            self._apply_system_info(payload)
        except Exception:
            pass

    def get_details(self) -> dict[str, Any]:
        self._ensure_cached_details()
        info = self._last_system_info or {}
        asic = self._last_asic_info or {}
        rejected = info.get("sharesRejectedReasons") or []
        if not isinstance(rejected, list):
            rejected = []
        rejected_rows = []
        for item in rejected[:5]:
            if isinstance(item, dict):
                rejected_rows.append({"reason": self._text(item.get("message")), "count": self._text(item.get("count"))})

        monitor = info.get("hashrateMonitor") or {}
        asics = monitor.get("asics") if isinstance(monitor, dict) else []
        if not isinstance(asics, list):
            asics = []
        asic_rows = []
        for idx, item in enumerate(asics[:8], start=1):
            if not isinstance(item, dict):
                continue
            domains = item.get("domains") or []
            domains_text = ", ".join(str(round(self._num(v), 2)) for v in domains) if isinstance(domains, list) else "—"
            asic_rows.append({"asic": str(idx), "hashrate": f"{self._num(item.get('total')):.2f} GH/s", "errors": f"{self._num(item.get('errorCount')):.0f}", "domains": domains_text})

        return {"sections": [
            {"id": "overview", "title": "Übersicht", "items": [
                {"label": "Hostname", "value": self._text(info.get("hostname"))},
                {"label": "Modell", "value": self._text(info.get("deviceModel") or asic.get("deviceModel") or info.get("boardVersion"))},
                {"label": "ASIC", "value": self._text(info.get("ASICModel") or asic.get("ASICModel"))},
                {"label": "ASIC-Anzahl", "value": self._text(info.get("smallCoreCount") or asic.get("asicCount"))},
                {"label": "axeOS", "value": self._text(info.get("axeOSVersion"))},
                {"label": "Firmware", "value": self._text(info.get("version"))},
                {"label": "Mining pausiert", "value": self._yes_no(info.get("miningPaused"))},
                {"label": "PV2Hash Off-Erkennung", "value": self._yes_no(info.get("power") is not None and self._num(info.get("power"), 0.0) < self.OFF_POWER_THRESHOLD_W)},
                {"label": "Uptime", "value": self._format_seconds(info.get("uptimeSeconds"))},
            ]},
            {"id": "performance", "title": "Leistung / Hashrate", "items": [
                {"label": "Leistung", "value": f"{self._num(info.get('power')):.1f} W"},
                {"label": "Max Power", "value": f"{self._num(info.get('maxPower')):.0f} W" if info.get("maxPower") is not None else "—"},
                {"label": "Profil-Leistung", "value": "p1-p4 fest 200 W (Start/Stop-only)"},
                {"label": "Off-Schwelle", "value": f"< {self.OFF_POWER_THRESHOLD_W:.0f} W"},
                {"label": "Start-Ramp-up", "value": f"{self.startup_grace_s:.0f} s"},
                {"label": "Hashrate", "value": f"{self._num(info.get('hashRate')):.2f} GH/s"},
                {"label": "Hashrate 1m", "value": f"{self._num(info.get('hashRate_1m')):.2f} GH/s"},
                {"label": "Hashrate 10m", "value": f"{self._num(info.get('hashRate_10m')):.2f} GH/s"},
                {"label": "Hashrate 1h", "value": f"{self._num(info.get('hashRate_1h')):.2f} GH/s"},
                {"label": "Fehlerquote", "value": f"{self._num(info.get('errorPercentage')):.2f} %"},
            ]},
            {"id": "thermals", "title": "Thermik / Lüfter", "items": [
                {"label": "Temp", "value": f"{self._num(info.get('temp')):.1f} °C"},
                {"label": "Temp 2", "value": f"{self._num(info.get('temp2')):.1f} °C"},
                {"label": "VR Temp", "value": f"{self._num(info.get('vrTemp')):.1f} °C"},
                {"label": "Zieltemp", "value": f"{self._num(info.get('temptarget')):.1f} °C"},
                {"label": "Lüfter", "value": f"{self._num(info.get('fanrpm')):.0f} rpm"},
                {"label": "Lüfter 2", "value": f"{self._num(info.get('fan2rpm')):.0f} rpm"},
                {"label": "Fan Speed", "value": f"{self._num(info.get('fanspeed')):.0f} %"},
                {"label": "Auto Fan", "value": self._yes_no(info.get("autofanspeed"))},
            ]},
            {"id": "network", "title": "Netzwerk / Pool", "items": [
                {"label": "IPv4", "value": self._text(info.get("ipv4"))},
                {"label": "IPv6", "value": self._text(info.get("ipv6"))},
                {"label": "MAC", "value": self._text(info.get("macAddr"))},
                {"label": "WLAN", "value": self._text(info.get("ssid"))},
                {"label": "RSSI", "value": f"{self._num(info.get('wifiRSSI')):.0f} dBm" if info.get("wifiRSSI") is not None else "—"},
                {"label": "Pool", "value": self._text(info.get("stratumURL"))},
                {"label": "Port", "value": self._text(info.get("stratumPort"))},
                {"label": "User", "value": self._text(info.get("stratumUser"))},
            ]},
            {"id": "shares", "title": "Shares / ASICs", "items": [
                {"label": "Accepted", "value": self._text(info.get("sharesAccepted"))},
                {"label": "Rejected", "value": self._text(info.get("sharesRejected"))},
                {"label": "Rejected Reasons", "kind": "table", "columns": (DriverDetailColumn(key="reason", label="Grund"), DriverDetailColumn(key="count", label="Anzahl")), "rows": tuple(rejected_rows), "empty": "Keine Rejected-Reasons gemeldet."},
                {"label": "ASIC Hashrate Monitor", "kind": "table", "columns": (DriverDetailColumn(key="asic", label="ASIC"), DriverDetailColumn(key="hashrate", label="Hashrate"), DriverDetailColumn(key="errors", label="Errors"), DriverDetailColumn(key="domains", label="Domains")), "rows": tuple(asic_rows), "empty": "Keine ASIC-Hashrate-Daten gemeldet."},
            ]},
        ]}
