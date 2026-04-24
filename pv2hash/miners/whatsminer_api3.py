
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
import struct
import time
from datetime import UTC, datetime
from typing import Any

from pv2hash.logging_ext.setup import get_logger
from pv2hash.miners.base import DriverAction, DriverField, DriverFieldChoice, MinerAdapter
from pv2hash.models.miner import MinerInfo, MinerProfile, MinerProfiles

logger = get_logger("pv2hash.miners.whatsminer_api3")


class WhatsminerApi3Miner(MinerAdapter):
    DRIVER_LABEL = "WhatsMiner (API 3.x)"

    @classmethod
    def get_config_schema(cls) -> list[DriverField]:
        return [
            DriverField(
                name="host",
                label="Host / IP",
                type="text",
                required=True,
                placeholder="192.168.x.x",
                help="IP-Adresse oder Hostname des WhatsMiner API 3 Geräts.",
                create_phase="basic",
            ),
            DriverField(
                name="settings.port",
                label="API-Port",
                type="number",
                required=True,
                preset=4433,
                default=4433,
                placeholder="4433",
                help="TCP-Port der WhatsMiner API 3.",
                create_phase="basic",
            ),
            DriverField(
                name="settings.account",
                label="Account",
                type="text",
                required=True,
                preset="super",
                default="super",
                placeholder="super",
                help="WhatsMiner API 3 Operator-Account (z. B. super).",
                create_phase="basic",
            ),
            DriverField(
                name="settings.password",
                label="Passwort",
                type="password",
                required=True,
                default="",
                placeholder="Passwort",
                help="Passwort des gewählten API-3-Accounts.",
                create_phase="basic",
            ),
            DriverField(
                name="settings.timeout_s",
                label="Timeout (s)",
                type="number",
                preset=5,
                default=5,
                placeholder="5",
                help="Socket-Timeout für API 3 Requests.",
                advanced=True,
            ),
        ]


    @classmethod
    def get_device_settings_schema(cls) -> list[DriverField]:
        return [
            DriverField(
                name="device_settings.fan_poweroff_cool",
                label="Lüfternachlauf bei Stop",
                type="checkbox",
                default=False,
                help="Wenn aktiviert, kühlt der Miner nach dem Stoppen aktiv nach. Für PV2Hash meist deaktiviert.",
            ),
            DriverField(
                name="device_settings.power_limit_w",
                label="Power Limit (W)",
                type="number",
                default=None,
                min=0,
                max=99999,
                step=1,
                help="Maximale Leistungsaufnahme in Watt. Leer lassen, wenn kein Power-Limit gesetzt werden soll. Der Miner startet zur Übernahme neu.",
            ),
        ]

    @classmethod
    def get_actions_schema(cls) -> list[DriverAction]:
        return [
            DriverAction(
                name="system_reboot",
                label="Miner neu starten",
                description="Startet das WhatsMiner-Gerät sofort per set.system.reboot neu.",
                confirm_text="Miner jetzt wirklich neu starten?",
                dangerous=True,
            ),
        ]

    def __init__(
        self,
        miner_id: str,
        name: str,
        host: str,
        port: int = 4433,
        account: str = "super",
        password: str = "",
        priority: int = 100,
        enabled: bool = True,
        serial_number: str | None = None,
        model: str | None = None,
        firmware_version: str | None = None,
        profiles: dict[str, Any] | None = None,
        min_regulated_profile: str = "off",
        timeout_s: float = 5.0,
        use_battery_when_charging: bool = False,
        battery_charge_soc_min: float = 95.0,
        battery_charge_profile: str = "p1",
        use_battery_when_discharging: bool = False,
        battery_discharge_soc_min: float = 80.0,
        battery_discharge_profile: str = "p1",
    ) -> None:
        self.host = str(host).strip()
        self.port = int(port)
        self.account = str(account or "super").strip() or "super"
        self.password = str(password or "")
        self.timeout_s = float(timeout_s)

        profile_cfg = profiles or {
            "p1": {"power_w": 1000},
            "p2": {"power_w": 1400},
            "p3": {"power_w": 1800},
            "p4": {"power_w": 2200},
        }
        normalized_min_regulated_profile = (
            min_regulated_profile if min_regulated_profile in {"off", "p1", "p2", "p3", "p4"} else "off"
        )

        miner_profiles = MinerProfiles(
            p1=MinerProfile(power_w=float(profile_cfg["p1"]["power_w"])),
            p2=MinerProfile(power_w=float(profile_cfg["p2"]["power_w"])),
            p3=MinerProfile(power_w=float(profile_cfg["p3"]["power_w"])),
            p4=MinerProfile(power_w=float(profile_cfg["p4"]["power_w"])),
        )

        self.info = MinerInfo(
            id=miner_id,
            name=name,
            host=self.host,
            driver="whatsminer_api3",
            enabled=enabled,
            is_active=True,
            priority=priority,
            serial_number=serial_number,
            model=model,
            firmware_version=firmware_version,
            profile="off",
            power_w=0.0,
            profiles=miner_profiles,
            min_regulated_profile=normalized_min_regulated_profile,
            use_battery_when_charging=bool(use_battery_when_charging),
            battery_charge_soc_min=float(battery_charge_soc_min),
            battery_charge_profile=battery_charge_profile,
            use_battery_when_discharging=bool(use_battery_when_discharging),
            battery_discharge_soc_min=float(battery_discharge_soc_min),
            battery_discharge_profile=battery_discharge_profile,
            reachable=False,
            runtime_state="unknown",
            control_mode="power_percent_fast",
            autotuning_enabled=None,
            power_target_min_w=float(profile_cfg["p1"]["power_w"]),
            power_target_default_w=float(profile_cfg["p2"]["power_w"]),
            power_target_max_w=float(profile_cfg["p4"]["power_w"]),
        )

        self._cached_power_limit_w: float | None = None
        self._last_requested_percent: str | None = None
        self._last_sent_profile: str | None = None
        self._desired_profile_after_start: str | None = None
        self._device_info_cache: dict[str, Any] = {}
        self._summary_cache: dict[str, Any] = {}
        self._fan_setting_cache: dict[str, Any] = {}
        self._last_full_refresh_at: datetime | None = None

    def _send_request(self, obj: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(obj, separators=(",", ":")).encode("ascii")
        with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as sock:
            sock.sendall(struct.pack("<I", len(payload)) + payload)
            header = sock.recv(4)
            if len(header) != 4:
                raise RuntimeError(f"Keine gültige 4-Byte-Längenangabe erhalten: {header!r}")
            length = struct.unpack("<I", header)[0]
            data = b""
            while len(data) < length:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode("utf-8", errors="replace"))

    def _make_token(self, cmd: str, salt: str, ts: int) -> str:
        raw = f"{cmd}{self.password}{salt}{ts}".encode("utf-8")
        return base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii")[:8]

    def _get_device_info(self) -> dict[str, Any]:
        return self._send_request({"cmd": "get.device.info"})

    def _get_summary_status(self) -> dict[str, Any]:
        return self._send_request({"cmd": "get.miner.status", "param": "summary"})

    def _get_fan_setting(self) -> dict[str, Any]:
        return self._send_request({"cmd": "get.fan.setting"})

    def _write_command(self, cmd: str, param: Any | None = None, *, include_param: bool = True) -> dict[str, Any]:
        info = self._get_device_info()
        salt = str(info.get("msg", {}).get("salt", ""))
        if not salt:
            raise RuntimeError("WhatsMiner API 3 salt fehlt in get.device.info")
        ts = int(time.time())
        token = self._make_token(cmd, salt, ts)
        req: dict[str, Any] = {
            "cmd": cmd,
            "ts": ts,
            "token": token,
            "account": self.account,
        }
        if include_param:
            req["param"] = param
        return self._send_request(req)

    @staticmethod
    def _safe_float(value: Any, default: float | None = None) -> float | None:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _safe_int(value: Any, default: int | None = None) -> int | None:
        try:
            return int(float(value))
        except Exception:
            return default

    @staticmethod
    def _format_bool_flag(value: Any) -> str:
        if isinstance(value, bool):
            return "Ja" if value else "Nein"
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return "Ja"
        if text in {"0", "false", "no", "off"}:
            return "Nein"
        return str(value) if value not in (None, "") else "—"


    def _set_unreachable(self, exc: Exception) -> None:
        self.info.reachable = False
        self.info.runtime_state = "unreachable"
        self.info.last_error = str(exc)
        self.info.last_seen = datetime.now(UTC)

    def _percent_from_power(self, desired_w: float, power_limit_w: float) -> str:
        if power_limit_w <= 0:
            raise RuntimeError("WhatsMiner API 3 power-limit ist nicht nutzbar")
        percent = max(0, min(100, int(round((float(desired_w) / float(power_limit_w)) * 100.0))))
        return str(percent)

    def _infer_profile_from_runtime(self, power_w: float | None, power_limit_w: float | None) -> str:
        if self.info.runtime_state in {"paused", "stopped"}:
            return "off"
        if self._last_sent_profile in {"p1", "p2", "p3", "p4"} and self.info.runtime_state in {"starting", "running"}:
            return self._last_sent_profile
        if power_w is None or power_limit_w is None or power_limit_w <= 0:
            return self.info.profile
        targets = {
            "p1": float(self.get_profile_power_w("p1")),
            "p2": float(self.get_profile_power_w("p2")),
            "p3": float(self.get_profile_power_w("p3")),
            "p4": float(self.get_profile_power_w("p4")),
        }
        return min(targets.keys(), key=lambda p: abs(targets[p] - power_w))

    async def set_profile(self, profile: str) -> None:
        await asyncio.to_thread(self._set_profile_sync, profile)

    def _set_profile_sync(self, profile: str) -> None:
        try:
            status = self._refresh_status()
            if profile == "off":
                self._desired_profile_after_start = None
                self._last_requested_percent = None
                self._last_sent_profile = "off"
                if status.get("working"):
                    logger.info("WhatsMiner API3 stop service for %s (%s:%s)", self.info.name, self.host, self.port)
                    self._write_command("set.miner.service", "stop")
                self.info.profile = "off"
                self.info.runtime_state = "paused"
                return

            desired_w = float(self.get_profile_power_w(profile))
            if desired_w <= 0:
                self._set_profile_sync("off")
                return

            if not status.get("working"):
                logger.info("WhatsMiner API3 start service for %s (%s:%s)", self.info.name, self.host, self.port)
                self._desired_profile_after_start = profile
                self._last_sent_profile = profile
                self._write_command("set.miner.service", "start")
                self.info.runtime_state = "starting"
                self.info.profile = profile
                return

            if status.get("up_freq_finish") != 1:
                self._desired_profile_after_start = profile
                self._last_sent_profile = profile
                self.info.runtime_state = "starting"
                self.info.profile = profile
                return

            power_limit_w = status.get("power_limit_w") or self._cached_power_limit_w
            if not power_limit_w:
                raise RuntimeError("WhatsMiner API 3 power-limit fehlt für Prozentregelung")

            desired_percent = self._percent_from_power(desired_w, power_limit_w)
            if self._last_requested_percent == desired_percent and self._last_sent_profile == profile:
                self.info.profile = profile
                return

            logger.info(
                "WhatsMiner API3 set power percent for %s (%s:%s): desired_w=%.0f power_limit_w=%.0f percent=%s",
                self.info.name, self.host, self.port, desired_w, power_limit_w, desired_percent
            )
            resp = self._write_command(
                "set.miner.power_percent",
                {"percent": desired_percent, "mode": "fast"},
            )
            if resp.get("code") != 0:
                raise RuntimeError(f"WhatsMiner API 3 set.miner.power_percent failed: {resp}")
            self._last_requested_percent = desired_percent
            self._last_sent_profile = profile
            self._desired_profile_after_start = None
            self.info.profile = profile
            self.info.runtime_state = "running"
        except Exception as exc:
            self._set_unreachable(exc)
            logger.warning(
                "WhatsMiner API3 control action failed for %s (%s:%s): %s",
                self.info.name, self.host, self.port, exc,
            )

    async def get_status(self) -> MinerInfo:
        return await asyncio.to_thread(self._get_status_sync)

    def _refresh_status(self) -> dict[str, Any]:
        device = self._get_device_info()
        status = self._get_summary_status()
        try:
            fan_setting = self._get_fan_setting()
        except Exception as exc:
            fan_setting = {}
            logger.debug(
                "WhatsMiner API3 get.fan.setting failed for %s (%s:%s): %s",
                self.info.name, self.host, self.port, exc,
            )
        self._device_info_cache = device.get("msg", {}) if isinstance(device.get("msg"), dict) else {}
        self._summary_cache = status.get("msg", {}).get("summary", {}) if isinstance(status.get("msg"), dict) else {}
        self._fan_setting_cache = fan_setting.get("msg", {}) if isinstance(fan_setting.get("msg"), dict) else {}
        self._last_full_refresh_at = datetime.now(UTC)

        miner = self._device_info_cache.get("miner", {}) if isinstance(self._device_info_cache.get("miner"), dict) else {}
        system = self._device_info_cache.get("system", {}) if isinstance(self._device_info_cache.get("system"), dict) else {}

        power_rt = self._safe_float(self._summary_cache.get("power-realtime"), None)
        power_limit_w = self._safe_float(self._summary_cache.get("power-limit"), None)
        if power_limit_w and power_limit_w > 0:
            self._cached_power_limit_w = power_limit_w
        else:
            power_limit_w = self._cached_power_limit_w

        hash_realtime_ths = self._safe_float(self._summary_cache.get("hash-realtime"), None)
        working = str(miner.get("working", "false")).strip().lower() == "true"
        up_freq_finish = self._safe_int(self._summary_cache.get("up-freq-finish"), 0) or 0

        self.info.reachable = True
        self.info.last_error = None
        self.info.last_seen = datetime.now(UTC)
        self.info.power_w = float(power_rt or 0.0)
        self.info.current_hashrate_ghs = hash_realtime_ths * 1000.0 if hash_realtime_ths is not None else None
        self.info.model = self.info.model or miner.get("type") or self.info.model
        self.info.firmware_version = system.get("fwversion") or self.info.firmware_version
        self.info.api_version = system.get("api") or self.info.api_version
        self.info.power_target_max_w = float(power_limit_w) if power_limit_w is not None else self.info.power_target_max_w
        self.info.power_target_default_w = float(power_limit_w) if power_limit_w is not None else self.info.power_target_default_w
        self.info.power_target_min_w = float(self.get_profile_power_w("p1")) if self.get_profile_power_w("p1") > 0 else self.info.power_target_min_w

        if not self.info.enabled:
            self.info.is_active = False
            self.info.runtime_state = "disabled"
        else:
            self.info.is_active = True
            if not working:
                self.info.runtime_state = "paused"
                self.info.profile = "off"
            elif up_freq_finish != 1:
                self.info.runtime_state = "starting"
                self.info.profile = self._desired_profile_after_start or self._last_sent_profile or self.info.profile
            else:
                self.info.runtime_state = "running"
                self.info.profile = self._infer_profile_from_runtime(power_rt, power_limit_w)

        return {
            "working": working,
            "up_freq_finish": up_freq_finish,
            "power_limit_w": power_limit_w,
            "power_rt": power_rt,
        }

    def _get_status_sync(self) -> MinerInfo:
        try:
            self._refresh_status()
        except Exception as exc:
            self._set_unreachable(exc)
            logger.warning("WhatsMiner API3 status failed for %s (%s:%s): %s", self.info.name, self.host, self.port, exc)
        return self.info


    def get_device_settings_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}

        try:
            fan_setting = self._get_fan_setting()
            fan_msg = fan_setting.get("msg", {}) if isinstance(fan_setting.get("msg"), dict) else {}
            if "fan-poweroff-cool" in fan_msg:
                values["device_settings.fan_poweroff_cool"] = bool(self._safe_int(fan_msg.get("fan-poweroff-cool"), 0))
            if "fan-zero-speed" in fan_msg:
                # Firmware note: on tested M31S+ H6OS 20250214.16.1.AMS,
                # set.fan.zero_speed returns OK but toggles fan-poweroff-cool
                # instead of fan-zero-speed. Keep readback/details, but do not
                # expose it as a writable device setting.
                values["device_settings.fan_zero_speed"] = bool(self._safe_int(fan_msg.get("fan-zero-speed"), 0))
            self._fan_setting_cache = fan_msg
        except Exception as exc:
            logger.debug(
                "WhatsMiner API3 device fan setting readback failed for %s (%s:%s): %s",
                self.info.name, self.host, self.port, exc,
            )

        power_limit_w = self._cached_power_limit_w
        if power_limit_w is None:
            try:
                status = self._get_summary_status()
                summary = status.get("msg", {}).get("summary", {}) if isinstance(status.get("msg"), dict) else {}
                power_limit_w = self._safe_float(summary.get("power-limit"), None)
                if power_limit_w is not None and power_limit_w > 0:
                    self._cached_power_limit_w = power_limit_w
            except Exception as exc:
                logger.debug(
                    "WhatsMiner API3 device power-limit readback failed for %s (%s:%s): %s",
                    self.info.name, self.host, self.port, exc,
                )

        if power_limit_w is not None:
            values["device_settings.power_limit_w"] = int(round(float(power_limit_w)))

        return values

    def apply_device_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        current_values = self.get_device_settings_values()
        commands: list[tuple[str, str, Any]] = []

        if "device_settings.fan_poweroff_cool" in values:
            fan_poweroff_cool = bool(values.get("device_settings.fan_poweroff_cool", False))
            if current_values.get("device_settings.fan_poweroff_cool") != fan_poweroff_cool:
                commands.append(("fan_poweroff_cool", "set.fan.poweroff_cool", 1 if fan_poweroff_cool else 0))


        power_limit_w = values.get("device_settings.power_limit_w", None)
        power_limit_int: int | None = None
        if power_limit_w is not None:
            try:
                power_limit_int = int(float(power_limit_w))
            except Exception:
                return {"ok": False, "message": "Power Limit muss eine Zahl zwischen 0 und 99999 W sein."}

            if power_limit_int < 0 or power_limit_int > 99999:
                return {"ok": False, "message": "Power Limit muss zwischen 0 und 99999 W liegen."}

            current_power_limit = current_values.get("device_settings.power_limit_w")
            if current_power_limit is None or int(float(current_power_limit)) != power_limit_int:
                commands.append(("power_limit_w", "set.miner.power_limit", power_limit_int))

        if not commands:
            logger.info(
                "WhatsMiner API3 device settings unchanged for %s (%s:%s)",
                self.info.name, self.host, self.port,
            )
            return {"ok": True, "message": "Keine Geräte-Einstellung geändert"}

        logger.info(
            "WhatsMiner API3 apply device settings for %s (%s:%s): %s",
            self.info.name,
            self.host,
            self.port,
            ", ".join(f"{setting_name}={param!r}" for setting_name, _cmd, param in commands),
        )

        for setting_name, cmd, param in commands:
            try:
                resp = self._write_command(cmd, param)
            except Exception as exc:
                self._set_unreachable(exc)
                logger.warning(
                    "WhatsMiner API3 apply %s failed for %s (%s:%s): %s",
                    setting_name, self.info.name, self.host, self.port, exc
                )
                return {"ok": False, "message": f"API-Verbindungsfehler bei {setting_name}: {exc}"}

            logger.info(
                "WhatsMiner API3 apply %s response for %s (%s:%s): %s",
                setting_name, self.info.name, self.host, self.port, resp,
            )

            if int(resp.get("code", -1)) != 0:
                logger.warning(
                    "WhatsMiner API3 apply %s API error for %s (%s:%s): %s",
                    setting_name, self.info.name, self.host, self.port, resp
                )
                return {"ok": False, "message": f"API-Fehler bei {setting_name}: {resp}"}

        if power_limit_int is not None:
            self._cached_power_limit_w = float(power_limit_int)

        fan_expected: dict[str, bool] = {}
        if "device_settings.fan_poweroff_cool" in values:
            fan_expected["device_settings.fan_poweroff_cool"] = bool(values.get("device_settings.fan_poweroff_cool", False))

        if fan_expected:
            readback_values = self.get_device_settings_values()
            mismatches: list[str] = []
            for key, expected in fan_expected.items():
                if key not in readback_values:
                    mismatches.append(f"{key}: Readback fehlt")
                    continue
                actual = bool(readback_values.get(key))
                if actual != expected:
                    mismatches.append(f"{key}: erwartet={expected} gelesen={actual}")

            if mismatches:
                logger.warning(
                    "WhatsMiner API3 device settings readback mismatch for %s (%s:%s): %s",
                    self.info.name, self.host, self.port, "; ".join(mismatches),
                )
                return {
                    "ok": False,
                    "message": "Geräte-Einstellung wurde gesendet, aber der Miner meldet danach weiterhin abweichende Werte: " + "; ".join(mismatches),
                }

            logger.info(
                "WhatsMiner API3 device settings readback verified for %s (%s:%s)",
                self.info.name, self.host, self.port,
            )

        return {"ok": True, "message": "Geräte-Einstellungen übernommen"}

    def apply_action(self, action_name: str) -> dict[str, Any]:
        if action_name != "system_reboot":
            return {"ok": False, "message": f"Unbekannte Aktion: {action_name}"}

        try:
            resp = self._write_command("set.system.reboot", include_param=False)
        except Exception as exc:
            self._set_unreachable(exc)
            logger.warning(
                "WhatsMiner API3 action %s failed for %s (%s:%s): %s",
                action_name, self.info.name, self.host, self.port, exc
            )
            return {"ok": False, "message": f"API-Verbindungsfehler bei {action_name}: {exc}"}

        if int(resp.get("code", -1)) != 0:
            logger.warning(
                "WhatsMiner API3 action %s API error for %s (%s:%s): %s",
                action_name, self.info.name, self.host, self.port, resp
            )
            return {"ok": False, "message": f"API-Fehler bei {action_name}: {resp}"}

        self.info.runtime_state = "rebooting"
        return {"ok": True, "message": "Miner-Neustart ausgelöst"}

    def get_details(self) -> dict:
        if not self._device_info_cache or not self._summary_cache:
            try:
                self._refresh_status()
            except Exception as exc:
                logger.debug(
                    "WhatsMiner API3 details refresh failed for %s (%s:%s): %s",
                    self.info.name, self.host, self.port, exc,
                )

        miner = self._device_info_cache.get("miner", {}) if isinstance(self._device_info_cache.get("miner"), dict) else {}
        power = self._device_info_cache.get("power", {}) if isinstance(self._device_info_cache.get("power"), dict) else {}
        system = self._device_info_cache.get("system", {}) if isinstance(self._device_info_cache.get("system"), dict) else {}
        summary = self._summary_cache if isinstance(self._summary_cache, dict) else {}
        boards = summary.get("board-temperature") if isinstance(summary.get("board-temperature"), list) else []

        sections = [
            {
                "id": "overview",
                "title": "Übersicht",
                "items": [
                    {"label": "Runtime", "value": str(self.info.runtime_state)},
                    {"label": "API", "value": str(system.get("api", "—"))},
                    {"label": "Firmware", "value": str(system.get("fwversion", "—"))},
                    {"label": "Power realtime", "value": f"{self.info.power_w:.0f} W"},
                    {"label": "Power limit", "value": f"{(self._cached_power_limit_w or 0):.0f} W"},
                    {"label": "Working", "value": str(miner.get("working", "—"))},
                    {"label": "Fast Boot", "value": str(miner.get("fast-boot", "—"))},
                    {"label": "up-freq-finish", "value": str(summary.get("up-freq-finish", "—"))},
                ],
            },
            {
                "id": "thermals",
                "title": "Thermik",
                "items": [
                    {"label": "Umgebung", "value": f"{summary.get('environment-temperature', '—')} °C"},
                    {"label": "Chip Min", "value": f"{summary.get('chip-temp-min', '—')} °C"},
                    {"label": "Chip Avg", "value": f"{summary.get('chip-temp-avg', '—')} °C"},
                    {"label": "Chip Max", "value": f"{summary.get('chip-temp-max', '—')} °C"},
                    {"label": "Lüfter In", "value": f"{summary.get('fan-speed-in', '—')} rpm"},
                    {"label": "Lüfter Out", "value": f"{summary.get('fan-speed-out', '—')} rpm"},
                    {"label": "Lüfternachlauf", "value": self._format_bool_flag(self._fan_setting_cache.get("fan-poweroff-cool"))},
                    {"label": "Zero Fan Speed", "value": self._format_bool_flag(self._fan_setting_cache.get("fan-zero-speed"))},
                    {"label": "Fan Temp Offset", "value": f"{self._fan_setting_cache.get('fan-temp-offset', '—')} °C"},
                ] + [
                    {"label": f"Board {idx+1}", "value": f"{temp} °C"} for idx, temp in enumerate(boards)
                ],
            },
            {
                "id": "power",
                "title": "PSU",
                "items": [
                    {"label": "VIN", "value": f"{power.get('vin', '—')} V"},
                    {"label": "IIN", "value": f"{power.get('iin', '—')} A"},
                    {"label": "VOUT", "value": f"{power.get('vout', '—')} V"},
                    {"label": "PIN", "value": f"{power.get('pin', '—')} W"},
                    {"label": "PSU Temp", "value": f"{power.get('temp0', '—')} °C"},
                ],
            },
        ]
        return {"sections": sections}
