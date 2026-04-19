from __future__ import annotations

import asyncio
import hashlib
import json
import re
import socket
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pv2hash.logging_ext.setup import get_logger
from pv2hash.miners.base import MinerAdapter
from pv2hash.models.miner import MinerInfo, MinerProfile, MinerProfiles

logger = get_logger("pv2hash.miners.whatsminer")


class WhatsminerMiner(MinerAdapter):
    """
    WhatsMiner adapter for the documented TCP API on port 4028.

    API 2.x strategy in PV2Hash:
    - start/stop via power_on / power_off
    - watt-based profiles remain visible in PV2Hash
    - Regelung erfolgt über set_power_pct_v2 auf Basis von power_limit

    Notes:
    - Readable API is sent as plaintext JSON over TCP/4028.
    - Writable API uses the documented get_token + encrypted payload flow.
    - PV2Hash verwendet für die Ist-Leistung ausschließlich SUMMARY.PowerRT.
    - PV2Hash verwendet für das Basis-Power-Limit ausschließlich STATUS.Msg.power_limit.
    """

    POWER_ON_VARIANT_LABEL = "json_token/scheme=md5crypt,pwd=fullpwd,time=full,key=fragment"
    POWER_ON_MODE_MARKER = "single_variant_v2_json_token"
    POWER_OFF_VARIANT_LABEL = "json_token/scheme=md5crypt,pwd=fullpwd,time=last4,key=fragment"
    POWER_LIMIT_VARIANT_LABEL = "json_token/scheme=md5crypt,pwd=fullpwd,time=full,key=fragment"
    POWER_PERCENT_VARIANT_LABEL = "json_token/scheme=md5crypt,pwd=fullpwd,time=full,key=fragment"
    POWER_PERCENT_MODE_MARKER = "single_variant_v1_json_token"

    def __init__(
        self,
        miner_id: str,
        name: str,
        host: str,
        port: int = 4028,
        priority: int = 100,
        enabled: bool = True,
        serial_number: str | None = None,
        model: str | None = None,
        firmware_version: str | None = None,
        profiles: dict[str, Any] | None = None,
        min_regulated_profile: str = "off",
        password: str = "",
        timeout_s: float = 2.0,
        power_limit_w: float = 0.0,
        use_battery_when_charging: bool = False,
        battery_charge_soc_min: float = 95.0,
        battery_charge_profile: str = "p1",
        use_battery_when_discharging: bool = False,
        battery_discharge_soc_min: float = 80.0,
        battery_discharge_profile: str = "p1",
    ) -> None:
        self.host = str(host).strip()
        self.port = int(port)
        self.password = str(password or "")
        self.timeout_s = float(timeout_s)
        self.configured_power_limit_w = float(power_limit_w or 0.0)
        self.reported_power_limit_w: float | None = None
        self.reported_hash_percent: float | None = None

        profile_cfg = profiles or {
            "p1": {"power_w": 1200},
            "p2": {"power_w": 2200},
            "p3": {"power_w": 3200},
            "p4": {"power_w": 4200},
        }

        miner_profiles = MinerProfiles(
            p1=MinerProfile(power_w=float(profile_cfg["p1"]["power_w"])),
            p2=MinerProfile(power_w=float(profile_cfg["p2"]["power_w"])),
            p3=MinerProfile(power_w=float(profile_cfg["p3"]["power_w"])),
            p4=MinerProfile(power_w=float(profile_cfg["p4"]["power_w"])),
        )

        normalized_min_regulated_profile = (
            min_regulated_profile
            if min_regulated_profile in {"off", "p1", "p2", "p3", "p4"}
            else "off"
        )

        self.info = MinerInfo(
            id=miner_id,
            name=name,
            host=self.host,
            driver="whatsminer_api2",
            enabled=enabled,
            is_active=enabled,
            priority=priority,
            serial_number=serial_number,
            model=model or "WhatsMiner",
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
            control_mode="power_state_only",
        )

        self._set_runtime_defaults()
        self.target_profile = "off"
        self._last_applied_power_pct: int | None = None
        self._pending_power_pct: int | None = None
        self._pending_power_pct_started_at: float = 0.0

    async def set_profile(self, profile: str) -> None:
        self.target_profile = profile
        self.info.profile = profile
        desired_w = 0.0 if profile == "off" else self.get_profile_power_w(profile)

        try:
            await asyncio.to_thread(self._apply_profile_sync, profile, desired_w)
            self.info.last_error = None
            self.info.is_active = bool(self.info.enabled)

            if profile == "off" or desired_w <= 0:
                self.info.power_w = 0.0
                self.info.runtime_state = "paused"
            else:
                self.info.runtime_state = "running"
        except Exception as exc:
            message = f"WhatsMiner write failed: {exc}"
            logger.warning(
                "WhatsMiner write failed for %s (%s:%s): %s",
                self.info.name,
                self.host,
                self.port,
                exc,
            )
            self.info.last_error = message

        self.info.last_seen = datetime.now(UTC)

    async def get_status(self) -> MinerInfo:
        try:
            bundle = await asyncio.to_thread(self._fetch_bundle_sync)
            self._apply_bundle(bundle)
        except Exception as exc:
            self._mark_unreachable(f"WhatsMiner read failed: {exc}")

        self.info.last_seen = datetime.now(UTC)
        return self.info

    def _set_runtime_defaults(self) -> None:
        self.info.reachable = False
        self.info.runtime_state = "unknown"
        self.info.current_hashrate_ghs = None
        self.info.api_version = None
        self.info.control_mode = self.info.control_mode or "power_state_only"
        self.info.autotuning_enabled = None
        self.info.power_target_min_w = None
        self.info.power_target_default_w = None
        self.info.power_target_max_w = None

    def _mark_unreachable(self, message: str) -> None:
        self._set_runtime_defaults()
        self.info.reachable = False
        self.info.is_active = False
        self.info.runtime_state = "unknown"
        self.info.last_error = message

    def _fetch_bundle_sync(self) -> dict[str, Any]:
        return {
            "summary": self._read_command_sync("summary"),
            "status": self._read_command_sync("status"),
            "version": self._read_command_sync("get_version"),
            "devdetails": self._read_command_sync("devdetails"),
        }

    def _apply_bundle(self, bundle: dict[str, Any]) -> None:
        self._set_runtime_defaults()
        self.info.last_seen = datetime.now(UTC)
        self.info.reachable = True
        self.info.is_active = bool(self.info.enabled)

        summary = bundle.get("summary") or {}
        status = bundle.get("status") or {}
        version = bundle.get("version") or {}
        devdetails = bundle.get("devdetails") or {}

        summary_row = self._first_list_item(summary.get("SUMMARY"))
        version_msg = version.get("Msg") or {}
        details_rows = devdetails.get("DEVDETAILS") or []

        for item in details_rows:
            if isinstance(item, dict) and item.get("Model"):
                self.info.model = str(item.get("Model"))
                break

        api_ver = version_msg.get("api_ver")
        if api_ver is not None:
            self.info.api_version = str(api_ver)

        fw_ver = version_msg.get("fw_ver") or status.get("Firmware Version")
        if fw_ver:
            self.info.firmware_version = str(fw_ver)

        hashrate_mhs = self._safe_float(
            self._dict_get_canonical(summary_row, "HS RT", "MHS av"),
            None,
        )
        if hashrate_mhs is not None:
            self.info.current_hashrate_ghs = hashrate_mhs / 1000.0

        actual_power_w = self._safe_metric_float(
            self._dict_get_canonical(summary_row, "PowerRT"),
            None,
        )

        status_msg = status.get("Msg") if isinstance(status.get("Msg"), dict) else {}
        reported_power_limit_w = self._safe_metric_float(
            self._dict_get_canonical(status_msg, "power_limit"),
            None,
        )
        self.reported_power_limit_w = reported_power_limit_w
        effective_power_limit_w = self._effective_power_limit_w()
        if effective_power_limit_w is not None:
            self.info.power_target_default_w = effective_power_limit_w
            self.info.power_target_max_w = effective_power_limit_w

        hash_percent_text = str(self._dict_get_canonical(status_msg, "hash_percent") or "").strip().rstrip("%")
        self.reported_hash_percent = self._safe_float(hash_percent_text, None)

        if actual_power_w is not None:
            self.info.power_w = actual_power_w

        power_mode = self._dict_get_canonical(status_msg, "power_mode") or self._dict_get_canonical(summary_row, "Power Mode")
        if power_mode:
            self.info.control_mode = f"power_pct_v2:{power_mode}"

        mineroff = self._status_mineroff(status)
        if mineroff:
            if self.target_profile and self.target_profile != "off":
                self.info.runtime_state = "starting"
                self.info.profile = self.target_profile
            else:
                self.info.runtime_state = "paused"
                self.info.profile = "off"
        else:
            self.info.runtime_state = "running"
            inferred_from_w = actual_power_w
            if inferred_from_w is None and effective_power_limit_w is not None and self.reported_hash_percent is not None:
                inferred_from_w = effective_power_limit_w * (self.reported_hash_percent / 100.0)
            inferred_profile = self._infer_profile_from_power(inferred_from_w)
            if inferred_profile and inferred_profile != "off":
                self.info.profile = inferred_profile
            elif self.target_profile and self.target_profile != "off":
                self.info.profile = self.target_profile
                if (actual_power_w is None or actual_power_w <= 0) and (
                    self.reported_hash_percent is None or self.reported_hash_percent <= 0
                ):
                    self.info.runtime_state = "starting"
            elif inferred_profile:
                self.info.profile = inferred_profile

        self.info.last_error = None

    def _apply_profile_sync(self, profile: str, desired_w: float) -> None:
        miner_is_off = self._is_miner_off(timeout_s=0.8)

        if profile == "off" or desired_w <= 0:
            self._last_applied_power_pct = None
            self._pending_power_pct = None
            self._pending_power_pct_started_at = 0.0
            if miner_is_off:
                return
            self._write_power_state(
                "power_off",
                verifier=lambda: self._verify_power_state(expected_off=True, timeout_s=6.0),
            )
            return

        if miner_is_off:
            self._write_power_state(
                "power_on",
                verifier=lambda: self._verify_power_state(expected_off=False, timeout_s=6.0),
            )
            return

        power_limit_w = self._effective_power_limit_w()
        if power_limit_w is None or power_limit_w <= 0:
            message = "WhatsMiner API 2.x: kein Basis-Power-Limit verfügbar; Prozentregelung nicht möglich."
            logger.warning(
                "WhatsMiner percent control skipped for %s (%s:%s): no effective power_limit available",
                self.info.name,
                self.host,
                self.port,
            )
            self.info.last_error = message
            return

        percent = self._desired_power_percent(desired_w, power_limit_w)
        if not self._needs_power_percent_update(percent, power_limit_w=power_limit_w):
            return

        self._write_power_percent(
            percent,
            desired_w=desired_w,
            power_limit_w=power_limit_w,
        )

    def _write_power_state(
        self,
        cmd: str,
        *,
        verifier: Callable[[], bool] | None = None,
    ) -> dict[str, Any] | None:
        response = self._write_command_sync(cmd, ["true"] if cmd == "power_off" else None, verifier=verifier)
        if cmd == "power_off":
            self._last_applied_power_pct = None
            self._pending_power_pct = None
            self._pending_power_pct_started_at = 0.0
        return response

    def _needs_power_percent_update(self, desired_percent: int, *, power_limit_w: float) -> bool:
        if self.info.runtime_state in {"paused", "stopped", "unknown", "unreachable"}:
            return False

        if self._power_percent_matches_runtime(desired_percent, power_limit_w=power_limit_w):
            self._last_applied_power_pct = desired_percent
            self._pending_power_pct = None
            self._pending_power_pct_started_at = 0.0
            return False

        if self._last_applied_power_pct is not None and self._last_applied_power_pct == desired_percent:
            return False

        if self._pending_power_pct is not None and self._pending_power_pct == desired_percent:
            age_s = time.monotonic() - self._pending_power_pct_started_at
            if age_s < 15.0:
                return False

        return True

    def _write_power_percent(
        self,
        percent: int,
        *,
        desired_w: float,
        power_limit_w: float,
    ) -> dict[str, Any] | None:
        logger.info(
            "WhatsMiner set_power_pct_v2 request for %s (%s:%s): desired_w=%.0f power_limit_w=%.0f percent=%s",
            self.info.name,
            self.host,
            self.port,
            desired_w,
            power_limit_w,
            percent,
        )
        self._pending_power_pct = percent
        self._pending_power_pct_started_at = time.monotonic()
        response = self._write_command_sync(
            "set_power_pct_v2",
            [str(percent)],
            verifier=lambda: self._verify_power_percent(expected_percent=percent, power_limit_w=power_limit_w, timeout_s=8.0),
            allow_encrypted_ack=True,
        )
        self._last_applied_power_pct = percent
        return response

    def _effective_power_limit_w(self) -> float | None:
        if self.reported_power_limit_w and self.reported_power_limit_w > 0:
            return float(self.reported_power_limit_w)
        if self.configured_power_limit_w and self.configured_power_limit_w > 0:
            return float(self.configured_power_limit_w)
        return None

    def _desired_power_percent(self, desired_w: float, power_limit_w: float) -> int:
        if power_limit_w <= 0:
            raise RuntimeError("Ungültiges Basis-Power-Limit für WhatsMiner API 2.x")
        if desired_w > power_limit_w + 1e-6:
            raise RuntimeError(
                f"Gewünschte Leistung {desired_w:.0f} W liegt über dem Basis-Power-Limit {power_limit_w:.0f} W."
            )
        percent = int(round((float(desired_w) / float(power_limit_w)) * 100.0))
        return max(1, min(100, percent))

    def _runtime_power_percent(self, *, power_limit_w: float) -> float | None:
        if power_limit_w <= 0:
            return None
        if self.reported_hash_percent is not None:
            return float(self.reported_hash_percent)
        actual_power_w = self.info.power_w if self.info.power_w and self.info.power_w > 0 else None
        if actual_power_w is None:
            return None
        return max(0.0, min(100.0, (float(actual_power_w) / float(power_limit_w)) * 100.0))

    def _power_percent_matches_runtime(self, percent: int, *, power_limit_w: float, tolerance: float = 8.0) -> bool:
        current_percent = self._runtime_power_percent(power_limit_w=power_limit_w)
        if current_percent is None:
            return False
        return abs(current_percent - float(percent)) <= tolerance

    def apply_base_power_limit(self, target_w: int) -> dict[str, Any]:
        if int(target_w) <= 0:
            raise RuntimeError("Ungültiges Basis-Power-Limit für WhatsMiner API 2.x")

        response = self._write_command_sync(
            "adjust_power_limit",
            [str(int(target_w))],
            verifier=lambda: self._verify_power_limit(expected_w=int(target_w), timeout_s=20.0),
        )
        self.configured_power_limit_w = float(target_w)
        self.reported_power_limit_w = float(target_w)
        self.info.power_target_default_w = float(target_w)
        self.info.power_target_max_w = float(target_w)
        return response

    def _infer_profile_from_power(self, power_w: float | None) -> str | None:
        if power_w is None or power_w <= 0:
            return "off"

        candidates = []
        for profile_name in ("p1", "p2", "p3", "p4"):
            candidate_w = self.get_profile_power_w(profile_name)
            if candidate_w > 0:
                candidates.append((abs(candidate_w - power_w), profile_name))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _read_command_sync(self, cmd: str, *, timeout_s: float | None = None) -> dict[str, Any]:
        return self._send_tcp_json_sync({"cmd": cmd}, timeout_s=timeout_s)

    def _write_command_sync(
        self,
        cmd: str,
        params: list[str] | None = None,
        verifier: Callable[[], bool] | None = None,
        allow_encrypted_ack: bool = False,
    ) -> dict[str, Any]:
        if not self.password:
            raise RuntimeError(
                "WhatsMiner Passwort fehlt. Für Write-API muss das Admin-Passwort hinterlegt sein."
            )

        started = time.monotonic()
        token_started = time.monotonic()
        token_reply = self._read_command_sync("get_token", timeout_s=min(self.timeout_s, 1.0))
        token_elapsed_ms = (time.monotonic() - token_started) * 1000.0
        token_time, salt, newsalt = self._extract_token_fields(token_reply)
        if not token_time or not salt or not newsalt:
            raise RuntimeError(
                f"WhatsMiner get_token lieferte unvollständige Token-Daten: {token_reply!r}"
            )

        is_power_state_cmd = cmd in {"power_off", "power_on"}
        token_materials = self._derive_token_materials(
            token_time=token_time,
            salt=salt,
            newsalt=newsalt,
            wide=is_power_state_cmd,
            power_on_single=(cmd == "power_on"),
            power_limit_single=(cmd == "adjust_power_limit"),
            power_percent_single=(cmd == "set_power_pct_v2"),
        )
        command_payloads = self._build_command_payload_candidates(cmd=cmd, params=params)
        if not command_payloads:
            raise RuntimeError(f"WhatsMiner command wird aktuell nicht unterstützt: {cmd}")

        errors: list[str] = []
        for payload_label, payload in command_payloads:
            variants = self._build_encrypted_payload_variants(
                token_time=token_time,
                payload=payload,
                token_materials=token_materials,
                wide=is_power_state_cmd,
            )
            if cmd == "power_on":
                logger.info(
                    "WhatsMiner power_on mode for %s (%s:%s): %s",
                    self.info.name,
                    self.host,
                    self.port,
                    self.POWER_ON_MODE_MARKER,
                )
                preferred = [v for v in variants if v.get("label") == self.POWER_ON_VARIANT_LABEL]
                variants = preferred[:1] if preferred else variants[:1]
            elif cmd == "power_off":
                logger.info(
                    "WhatsMiner power_off mode for %s (%s:%s): single_variant_v1_json_token",
                    self.info.name,
                    self.host,
                    self.port,
                )
                preferred = [v for v in variants if v.get("label") == self.POWER_OFF_VARIANT_LABEL]
                variants = preferred[:1] if preferred else variants[:1]
            elif cmd == "adjust_power_limit":
                logger.info(
                    "WhatsMiner adjust_power_limit mode for %s (%s:%s): single_variant_v1_json_token",
                    self.info.name,
                    self.host,
                    self.port,
                )
                preferred = [v for v in variants if v.get("label") == self.POWER_LIMIT_VARIANT_LABEL]
                variants = preferred[:1] if preferred else variants[:1]
            elif cmd == "set_power_pct_v2":
                logger.info(
                    "WhatsMiner power percent mode for %s (%s:%s): %s",
                    self.info.name,
                    self.host,
                    self.port,
                    self.POWER_PERCENT_MODE_MARKER,
                )
                preferred = [v for v in variants if v.get("label") == self.POWER_PERCENT_VARIANT_LABEL]
                variants = preferred[:1] if preferred else variants[:1]
            for variant in variants:
                attempt_started = time.monotonic()
                try:
                    response = self._send_tcp_json_sync(
                        variant["outer_payload"],
                        timeout_s=min(self.timeout_s, 1.0),
                    )
                    if "enc" in response and "data" in response:
                        response = self._decrypt_response(
                            aes_key_hex=variant["aes_key_hex"],
                            encoded=str(response["data"]),
                        )
                    verify_elapsed_ms = 0.0
                    try:
                        self._validate_command_ok(response)
                    except Exception as validation_exc:
                        if verifier is not None:
                            verify_started = time.monotonic()
                            verified = verifier()
                            verify_elapsed_ms = (time.monotonic() - verify_started) * 1000.0
                            if verified:
                                total_elapsed_ms = (time.monotonic() - started) * 1000.0
                                logger.info(
                                    "WhatsMiner write verified by follow-up check for %s (%s:%s): cmd=%s payload=%s variant=%s token_ms=%.0f verify_ms=%.0f total_ms=%.0f response=%r",
                                    self.info.name,
                                    self.host,
                                    self.port,
                                    cmd,
                                    payload_label,
                                    variant["label"],
                                    token_elapsed_ms,
                                    verify_elapsed_ms,
                                    total_elapsed_ms,
                                    response,
                                )
                                return response
                        if allow_encrypted_ack and self._looks_like_encrypted_ack(response):
                            total_elapsed_ms = (time.monotonic() - started) * 1000.0
                            logger.info(
                                "WhatsMiner write accepted by encrypted ack for %s (%s:%s): cmd=%s payload=%s variant=%s token_ms=%.0f verify_ms=%.0f total_ms=%.0f response=%r",
                                self.info.name,
                                self.host,
                                self.port,
                                cmd,
                                payload_label,
                                variant["label"],
                                token_elapsed_ms,
                                verify_elapsed_ms,
                                total_elapsed_ms,
                                response,
                            )
                            return response
                        raise validation_exc
                    total_elapsed_ms = (time.monotonic() - started) * 1000.0
                    logger.info(
                        "WhatsMiner write succeeded for %s (%s:%s): cmd=%s payload=%s variant=%s token_ms=%.0f total_ms=%.0f",
                        self.info.name,
                        self.host,
                        self.port,
                        cmd,
                        payload_label,
                        variant["label"],
                        token_elapsed_ms,
                        total_elapsed_ms,
                    )
                    return response
                except Exception as exc:
                    attempt_elapsed_ms = (time.monotonic() - attempt_started) * 1000.0
                    errors.append(
                        f"payload={payload_label} variant={variant['label']} ms={attempt_elapsed_ms:.0f} error={exc}"
                    )
                    logger.debug(
                        "WhatsMiner encrypted write variant failed for %s (%s:%s): cmd=%s payload=%s variant=%s token_ms=%.0f attempt_ms=%.0f error=%s",
                        self.info.name,
                        self.host,
                        self.port,
                        cmd,
                        payload_label,
                        variant["label"],
                        token_elapsed_ms,
                        attempt_elapsed_ms,
                        exc,
                    )

        total_elapsed_ms = (time.monotonic() - started) * 1000.0
        joined_errors = "; ".join(errors[-4:]) if errors else "unbekannter Fehler"
        raise RuntimeError(f"total_ms={total_elapsed_ms:.0f}; token_ms={token_elapsed_ms:.0f}; {joined_errors}")

    def _build_command_payload_candidates(
        self,
        *,
        cmd: str,
        params: list[str] | None,
    ) -> list[tuple[str, dict[str, str]]]:
        normalized_params = [str(param) for param in (params or []) if param is not None]

        if cmd == "power_on":
            return [("cmd_only", {"cmd": "power_on"})]

        if cmd == "power_off":
            respbefore = normalized_params[0] if normalized_params else "true"
            return [
                ("respbefore", {"cmd": "power_off", "respbefore": respbefore}),
            ]

        value = normalized_params[0] if normalized_params else ""
        if not value:
            return []

        if cmd == "set_power_value":
            return [
                ("power_value", {"cmd": cmd, "power_value": value}),
                ("value", {"cmd": cmd, "value": value}),
            ]

        if cmd == "adjust_power_limit":
            return [
                ("power_limit", {"cmd": cmd, "power_limit": value}),
            ]

        if cmd == "set_power_pct_v2":
            return [
                ("percent", {"cmd": cmd, "percent": value}),
            ]

        return [("generic", {"cmd": cmd, "param": value})]

    def _derive_token_materials(
        self,
        *,
        token_time: str,
        salt: str,
        newsalt: str,
        wide: bool = False,
        power_on_single: bool = False,
        power_limit_single: bool = False,
        power_percent_single: bool = False,
    ) -> list[dict[str, str]]:
        time_last4 = token_time[-4:]
        passwords: list[tuple[str, str]] = [("fullpwd", self.password)]
        password_bytes = self.password.encode("utf-8", errors="ignore")
        if len(password_bytes) > 8:
            passwords.append(("pwd8", password_bytes[:8].decode("utf-8", errors="ignore")))

        materials: list[dict[str, str]] = []

        for password_mode, password_value in passwords:
            if (power_on_single or power_limit_single or power_percent_single) and password_mode != "fullpwd":
                continue
            full_pwd_output = self._openssl_md5_crypt_output(salt=salt, value=password_value)
            pwd_fragment = self._md5_crypt_fragment(full_pwd_output)

            if wide and not power_on_single and not power_limit_single and not power_percent_single:
                for time_mode, time_for_sign in (("last4", time_last4), ("full", token_time)):
                    sign_output = self._openssl_md5_crypt_output(
                        salt=newsalt,
                        value=f"{pwd_fragment}{time_for_sign}",
                    )
                    sign_fragment = self._md5_crypt_fragment(sign_output)
                    for key_mode, key_source in (("full", full_pwd_output), ("fragment", pwd_fragment)):
                        aes_key_hex = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
                        materials.append(
                            {
                                "scheme": "md5crypt",
                                "password_mode": password_mode,
                                "time_mode": time_mode,
                                "key_mode": key_mode,
                                "sign": sign_fragment,
                                "aes_key_hex": aes_key_hex,
                            }
                        )
            else:
                single_fragment_mode = power_on_single or power_limit_single or power_percent_single
                time_for_sign = token_time if single_fragment_mode else time_last4
                sign_output = self._openssl_md5_crypt_output(
                    salt=newsalt,
                    value=f"{pwd_fragment}{time_for_sign}",
                )
                sign_fragment = self._md5_crypt_fragment(sign_output)
                key_source = pwd_fragment if single_fragment_mode else full_pwd_output
                key_mode = "fragment" if single_fragment_mode else "full"
                time_mode = "full" if single_fragment_mode else "last4"
                aes_key_hex = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
                materials.append(
                    {
                        "scheme": "md5crypt",
                        "password_mode": password_mode,
                        "time_mode": time_mode,
                        "key_mode": key_mode,
                        "sign": sign_fragment,
                        "aes_key_hex": aes_key_hex,
                    }
                )

            if not (power_on_single or power_limit_single or power_percent_single):
                simple_key = hashlib.md5(f"{salt}{password_value}".encode("utf-8")).hexdigest()
                simple_sign = hashlib.md5(f"{newsalt}{simple_key}{time_last4}".encode("utf-8")).hexdigest()
                simple_aes_key_hex = hashlib.sha256(simple_key.encode("utf-8")).hexdigest()
                materials.append(
                    {
                        "scheme": "simplemd5",
                        "password_mode": password_mode,
                        "time_mode": "last4",
                        "key_mode": "md5hex",
                        "sign": simple_sign,
                        "aes_key_hex": simple_aes_key_hex,
                    }
                )

        return materials

    def _build_encrypted_payload_variants(
        self,
        *,
        token_time: str,
        payload: dict[str, str],
        token_materials: list[dict[str, str]],
        wide: bool = False,
    ) -> list[dict[str, str | dict[str, Any]]]:
        variants: list[dict[str, str | dict[str, Any]]] = []

        pipe_parts = [payload.get("cmd", "")]
        for key, value in payload.items():
            if key == "cmd" or value in (None, ""):
                continue
            pipe_parts.append(str(value))
        pipe_plain = "|".join(pipe_parts)

        payload_json = json.dumps(payload, separators=(",", ":"))
        payload_with_token = {"token": token_time, **payload}
        payload_json_with_token = json.dumps(payload_with_token, separators=(",", ":"))
        is_power_state_cmd = str(payload.get("cmd", "")).lower() in {"power_on", "power_off"}

        seen: set[tuple[str, str]] = set()
        for material in token_materials:
            sign = str(material["sign"])
            aes_key_hex = str(material["aes_key_hex"])
            variant_id = (
                f"scheme={material['scheme']},pwd={material['password_mode']},"
                f"time={material['time_mode']},key={material['key_mode']}"
            )
            plaintext_candidates = [
                (f"json_token/{variant_id}", payload_json_with_token),
            ]
            if wide and is_power_state_cmd:
                plaintext_candidates.extend([
                    (f"json_prefixed/{variant_id}", f"{token_time},{sign}|{payload_json}"),
                    (f"pipe_prefixed/{variant_id}", f"{token_time},{sign}|{pipe_plain}"),
                ])
            for label, plaintext in plaintext_candidates:
                dedupe_key = (label, aes_key_hex)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                encrypted = self._openssl_aes256_ecb_encrypt_base64(aes_key_hex, plaintext)
                variants.append(
                    {
                        "label": label,
                        "aes_key_hex": aes_key_hex,
                        "outer_payload": {"enc": 1, "data": encrypted},
                    }
                )

        return variants

    def _looks_like_encrypted_ack(self, response: dict[str, Any]) -> bool:
        if not isinstance(response, dict):
            return False
        if isinstance(response.get("enc"), str) and response.get("enc"):
            return True
        if response.get("enc") == 1 and isinstance(response.get("data"), str) and response.get("data"):
            return True
        return False

    def _decrypt_response(self, *, aes_key_hex: str, encoded: str) -> dict[str, Any]:
        try:
            decrypted = self._openssl_aes256_ecb_decrypt_base64(aes_key_hex, encoded)
        except Exception as exc:
            return {"enc": 1, "data": encoded, "decrypt_error": str(exc)}

        try:
            return self._parse_json_payload(decrypted)
        except Exception as exc:
            return {"enc": 1, "data": encoded, "raw": decrypted, "parse_error": str(exc)}

    def _send_tcp_json_sync(
        self,
        payload: dict[str, Any],
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        effective_timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        with socket.create_connection((self.host, self.port), timeout=effective_timeout) as sock:
            sock.settimeout(effective_timeout)
            sock.sendall(data)
            raw = self._recv_all(sock)
        return self._parse_json_payload(raw)

    def _recv_all(self, sock: socket.socket) -> str:
        chunks: list[bytes] = []
        while True:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk)
            raw = b"".join(chunks).decode("utf-8", errors="replace")
            cleaned = raw.replace("\x00", "").strip()
            if not cleaned:
                continue
            if cleaned.startswith("enc|"):
                return raw
            try:
                json.loads(cleaned)
                return raw
            except Exception:
                start = cleaned.find("{")
                end = cleaned.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        json.loads(cleaned[start:end + 1])
                        return raw
                    except Exception:
                        pass
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _parse_json_payload(self, raw: str) -> dict[str, Any]:
        cleaned = raw.replace("\x00", "").strip()
        if not cleaned:
            raise RuntimeError("Leere Antwort vom WhatsMiner API-Port")
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            if cleaned.startswith("enc|"):
                return {"enc": 1, "data": cleaned.split("|", 1)[1]}
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise RuntimeError(f"Ungültige WhatsMiner-Antwort: {cleaned!r}") from None
            parsed = json.loads(cleaned[start:end + 1])
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Unerwartetes WhatsMiner-Antwortformat: {parsed!r}")
        return parsed

    def _extract_token_fields(self, token_reply: dict[str, Any]) -> tuple[str, str, str]:
        sources: list[Any] = [token_reply]

        msg = token_reply.get("Msg")
        if msg not in (None, ""):
            sources.append(msg)

        for source in sources:
            token_time, salt, newsalt = self._extract_token_fields_from_value(source)
            if token_time and salt and newsalt:
                return token_time, salt, newsalt

        return "", "", ""

    def _extract_token_fields_from_value(self, value: Any) -> tuple[str, str, str]:
        if isinstance(value, dict):
            token_time = str(value.get("time", "")).strip()
            salt = str(value.get("salt", "")).strip()
            newsalt = str(value.get("newsalt", "")).strip()
            return token_time, salt, newsalt

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return "", "", ""

            try:
                parsed = json.loads(stripped)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                return self._extract_token_fields_from_value(parsed)

            parts = stripped.replace("|", " ").split()
            if len(parts) >= 3:
                return parts[0].strip(), parts[1].strip(), parts[2].strip()

        return "", "", ""

    def _validate_command_ok(self, response: dict[str, Any]) -> None:
        code = self._safe_int(response.get("Code"), None)
        if code in {131, 134}:
            return

        status = response.get("STATUS")
        if isinstance(status, list):
            if all(isinstance(item, dict) and item.get("STATUS") == "S" for item in status):
                return
        elif status == "S":
            return

        msg = response.get("Msg")
        description = response.get("Description")
        raise RuntimeError(
            f"WhatsMiner API Fehler (Code={code}, STATUS={status}, Msg={msg}, Description={description})"
        )

    def _verify_power_limit(self, expected_w: int, timeout_s: float = 20.0) -> bool:
        deadline = time.monotonic() + max(0.1, timeout_s)
        while time.monotonic() < deadline:
            try:
                remaining = max(0.3, min(1.0, deadline - time.monotonic()))
                status = self._read_command_sync("status", timeout_s=remaining)
                status_msg = self._status_msg(status)
                power_limit_w = self._safe_metric_float(self._dict_get_canonical(status_msg, "power_limit"), None)
                if power_limit_w is not None and abs(power_limit_w - float(expected_w)) <= 1.0:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _verify_power_state(self, *, expected_off: bool, timeout_s: float = 1.6) -> bool:
        deadline = time.monotonic() + max(0.1, timeout_s)
        while time.monotonic() < deadline:
            try:
                remaining = max(0.2, min(0.6, deadline - time.monotonic()))
                status = self._read_command_sync("status", timeout_s=remaining)
                status_msg = status.get("Msg") if isinstance(status.get("Msg"), dict) else {}
                mineroff = str(self._dict_get_canonical(status_msg, "mineroff") or "").strip().lower() == "true"
                if mineroff == expected_off:
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def _is_miner_off(self, *, timeout_s: float = 0.8) -> bool:
        try:
            status = self._read_command_sync("status", timeout_s=timeout_s)
            return self._status_mineroff(status)
        except Exception:
            return self.info.runtime_state in {"paused", "stopped", "unknown"}

    def _status_msg(self, status: dict[str, Any]) -> dict[str, Any]:
        return status.get("Msg") if isinstance(status.get("Msg"), dict) else {}

    def _status_mineroff(self, status: dict[str, Any]) -> bool:
        status_msg = self._status_msg(status)
        return str(self._dict_get_canonical(status_msg, "mineroff") or "").strip().lower() == "true"

    def _status_hash_percent(self, status: dict[str, Any]) -> float | None:
        status_msg = self._status_msg(status)
        value = str(self._dict_get_canonical(status_msg, "hash_percent") or "").strip().rstrip("%")
        return self._safe_float(value, None)

    def _verify_power_percent(self, *, expected_percent: int, power_limit_w: float, timeout_s: float = 8.0) -> bool:
        deadline = time.monotonic() + max(0.1, timeout_s)
        while time.monotonic() < deadline:
            try:
                remaining = max(0.2, min(0.8, deadline - time.monotonic()))
                status = self._read_command_sync("status", timeout_s=remaining)
                current_percent = self._status_hash_percent(status)
                if current_percent is not None and abs(current_percent - float(expected_percent)) <= 1.0:
                    return True

                summary = self._read_command_sync("summary", timeout_s=remaining)
                summary_row = self._first_list_item(summary.get("SUMMARY"))
                actual_power_w = self._safe_metric_float(
                    self._dict_get_canonical(summary_row, "PowerRT"),
                    None,
                )
                effective_limit = self._safe_metric_float(
                    self._dict_get_canonical(self._status_msg(status), "power_limit"),
                    None,
                )
                if actual_power_w is not None and effective_limit and effective_limit > 0:
                    current_percent = max(0.0, min(100.0, (float(actual_power_w) / float(effective_limit)) * 100.0))
                    if abs(current_percent - float(expected_percent)) <= 8.0:
                        return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _openssl_md5_crypt_output(self, *, salt: str, value: str) -> str:
        result = subprocess.run(
            ["openssl", "passwd", "-1", "-salt", salt, value],
            capture_output=True,
            text=True,
            check=True,
        )
        output = result.stdout.strip()
        if not output:
            raise RuntimeError("openssl passwd -1 lieferte keine Ausgabe")
        return output

    def _md5_crypt_fragment(self, md5_crypt_output: str) -> str:
        parts = str(md5_crypt_output).strip().split("$")
        fragment = parts[-1] if parts else ""
        if not fragment:
            raise RuntimeError("Ungültige openssl passwd -1 Ausgabe")
        return fragment

    def _openssl_aes256_ecb_encrypt_base64(self, aes_key_hex: str, plaintext: str) -> str:
        result = subprocess.run(
            ["openssl", "enc", "-aes-256-ecb", "-nosalt", "-K", aes_key_hex, "-base64", "-A"],
            input=plaintext.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8").strip()

    def _openssl_aes256_ecb_decrypt_base64(self, aes_key_hex: str, encoded: str) -> str:
        result = subprocess.run(
            ["openssl", "enc", "-d", "-aes-256-ecb", "-nosalt", "-K", aes_key_hex, "-base64", "-A"],
            input=encoded.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8", errors="replace")

    def _first_list_item(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                return first
        return {}

    def _normalize_metric_key(self, key: Any) -> str:
        text = str(key or "").strip().lower()
        return "".join(ch for ch in text if ch.isalnum())

    def _dict_get_canonical(self, mapping: Any, *candidate_keys: str) -> Any:
        if not isinstance(mapping, dict):
            return None

        for key in candidate_keys:
            if key in mapping:
                return mapping.get(key)

        normalized_candidates = {self._normalize_metric_key(key) for key in candidate_keys if key}
        if not normalized_candidates:
            return None

        for key, value in mapping.items():
            if self._normalize_metric_key(key) in normalized_candidates:
                return value
        return None

    def _safe_metric_float(self, value: Any, default: float | None) -> float | None:
        try:
            if value in (None, ""):
                return default
            if isinstance(value, (int, float)):
                return float(value)
            text = str(value).strip()
            if not text:
                return default
            text = text.replace(",", ".")
            match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
            if not match:
                return default
            return float(match.group(0))
        except Exception:
            return default

    def _safe_float(self, value: Any, default: float | None) -> float | None:
        try:
            if value in (None, ""):
                return default
            return float(value)
        except Exception:
            return default

    def _safe_int(self, value: Any, default: int | None) -> int | None:
        try:
            if value in (None, ""):
                return default
            return int(value)
        except Exception:
            return default
