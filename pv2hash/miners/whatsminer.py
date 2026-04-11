from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import subprocess
from datetime import UTC, datetime
from typing import Any

from pv2hash.logging_ext.setup import get_logger
from pv2hash.miners.base import MinerAdapter
from pv2hash.models.miner import MinerInfo, MinerProfile, MinerProfiles

logger = get_logger("pv2hash.miners.whatsminer")


class WhatsminerMiner(MinerAdapter):
    """
    WhatsMiner adapter for the documented TCP API on port 4028.

    Current focus:
    - API 2.x read support
    - API 2.x write support with watt-based commands only

    Read path:
    - summary
    - status
    - get_version
    - devdetails

    Write path:
    - power_off
    - power_on
    - power value / power limit commands only (no percent fallback)

    Notes:
    - Readable API is sent as plaintext JSON over TCP/4028.
    - Writable API uses the documented get_token + encrypted payload flow.
    - WhatsMiner API 2.x documentation around encrypted payload formatting is
      inconsistent, so the adapter tries a small number of API-2.x-compatible
      envelope variants while staying strictly on watt-based commands.
    """

    POWER_VALUE_COMMAND_CANDIDATES = (
        "set_power_value",
        "adjust_power_limit",
        "set_miner_power",
    )

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
        timeout_s: float = 8.0,
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
            control_mode="power_value",
        )

        self._set_runtime_defaults()

    async def set_profile(self, profile: str) -> None:
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
        self.info.control_mode = self.info.control_mode or "power_value"
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

        hashrate_mhs = self._safe_float(summary_row.get("HS RT", summary_row.get("MHS av")), None)
        if hashrate_mhs is not None:
            self.info.current_hashrate_ghs = hashrate_mhs / 1000.0

        actual_power_w = self._safe_float(summary_row.get("Power"), None)
        power_limit_w = self._safe_float(summary_row.get("Power Limit"), None)
        if power_limit_w is not None:
            self.info.power_target_default_w = power_limit_w
        if actual_power_w is not None:
            self.info.power_w = actual_power_w
        elif power_limit_w is not None:
            self.info.power_w = power_limit_w

        power_mode = summary_row.get("Power Mode")
        if power_mode:
            self.info.control_mode = f"power_mode:{power_mode}"

        btmineroff = str(status.get("btmineroff", "false")).strip().lower() == "true"
        if btmineroff:
            self.info.runtime_state = "paused"
            self.info.power_w = 0.0
            self.info.profile = "off"
        else:
            self.info.runtime_state = "running"
            inferred_profile = self._infer_profile_from_power(
                power_limit_w if power_limit_w is not None else actual_power_w
            )
            if inferred_profile:
                self.info.profile = inferred_profile

        self.info.last_error = None

    def _apply_profile_sync(self, profile: str, desired_w: float) -> None:
        if profile == "off" or desired_w <= 0:
            self._write_command_sync("power_off", ["true"])
            return

        if self.info.runtime_state in {"paused", "stopped", "unknown"}:
            self._write_command_sync("power_on")

        desired_w_int = max(1, int(round(desired_w)))
        last_error: Exception | None = None
        for cmd in self.POWER_VALUE_COMMAND_CANDIDATES:
            try:
                response = self._write_command_sync(cmd, [str(desired_w_int)])
                self._validate_command_ok(response)
                self.info.control_mode = "power_value"
                return
            except Exception as exc:
                last_error = exc
                logger.info(
                    "WhatsMiner power-value command candidate failed for %s (%s:%s): cmd=%s value=%s error=%s",
                    self.info.name,
                    self.host,
                    self.port,
                    cmd,
                    desired_w_int,
                    exc,
                )

        if last_error is None:
            raise RuntimeError("No WhatsMiner power-value command candidate available")
        raise last_error

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

    def _read_command_sync(self, cmd: str) -> dict[str, Any]:
        return self._send_tcp_json_sync({"cmd": cmd})

    def _write_command_sync(self, cmd: str, params: list[str] | None = None) -> dict[str, Any]:
        if not self.password:
            raise RuntimeError(
                "WhatsMiner Passwort fehlt. Für Write-API muss das Admin-Passwort hinterlegt sein."
            )

        token_reply = self._read_command_sync("get_token")
        token_time, salt, newsalt = self._extract_token_fields(token_reply)
        if not token_time or not salt or not newsalt:
            raise RuntimeError(
                f"WhatsMiner get_token lieferte unvollständige Token-Daten: {token_reply!r}"
            )

        token_materials = self._derive_token_materials(
            token_time=token_time,
            salt=salt,
            newsalt=newsalt,
        )
        command_payloads = self._build_command_payload_candidates(cmd=cmd, params=params)
        if not command_payloads:
            raise RuntimeError(f"WhatsMiner command wird aktuell nicht unterstützt: {cmd}")

        errors: list[str] = []
        for payload_label, payload in command_payloads:
            for variant in self._build_encrypted_payload_variants(
                token_time=token_time,
                payload=payload,
                token_materials=token_materials,
            ):
                try:
                    response = self._send_tcp_json_sync(variant["outer_payload"])
                    if "enc" in response and "data" in response:
                        response = self._decrypt_response(
                            aes_key_hex=variant["aes_key_hex"],
                            encoded=str(response["data"]),
                        )
                    self._validate_command_ok(response)
                    logger.info(
                        "WhatsMiner write succeeded for %s (%s:%s): cmd=%s payload=%s variant=%s",
                        self.info.name,
                        self.host,
                        self.port,
                        cmd,
                        payload_label,
                        variant["label"],
                    )
                    return response
                except Exception as exc:
                    errors.append(f"payload={payload_label} variant={variant['label']} error={exc}")
                    logger.debug(
                        "WhatsMiner encrypted write variant failed for %s (%s:%s): cmd=%s payload=%s variant=%s error=%s",
                        self.info.name,
                        self.host,
                        self.port,
                        cmd,
                        payload_label,
                        variant["label"],
                        exc,
                    )

        joined_errors = "; ".join(errors[-6:]) if errors else "unbekannter Fehler"
        raise RuntimeError(joined_errors)

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
                ("cmd_only", {"cmd": "power_off"}),
            ]

        value = normalized_params[0] if normalized_params else ""
        if not value:
            return []

        if cmd == "set_power_value":
            return [
                ("power_value", {"cmd": cmd, "power_value": value}),
                ("value", {"cmd": cmd, "value": value}),
                ("power", {"cmd": cmd, "power": value}),
                ("param", {"cmd": cmd, "param": value}),
            ]

        if cmd == "adjust_power_limit":
            return [
                ("power_limit", {"cmd": cmd, "power_limit": value}),
                ("value", {"cmd": cmd, "value": value}),
            ]

        if cmd == "set_miner_power":
            return [
                ("power_value", {"cmd": cmd, "power_value": value}),
                ("power", {"cmd": cmd, "power": value}),
                ("value", {"cmd": cmd, "value": value}),
                ("param", {"cmd": cmd, "param": value}),
            ]

        return [("generic", {"cmd": cmd, "param": value})]

    def _derive_token_materials(
        self,
        *,
        token_time: str,
        salt: str,
        newsalt: str,
    ) -> list[dict[str, str]]:
        full_pwd_output = self._openssl_md5_crypt_output(salt=salt, value=self.password)
        pwd_fragment = self._md5_crypt_fragment(full_pwd_output)
        time_last4 = token_time[-4:]

        materials: list[dict[str, str]] = []
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
                        "time_mode": time_mode,
                        "key_mode": key_mode,
                        "sign": sign_fragment,
                        "aes_key_hex": aes_key_hex,
                    }
                )
        return materials

    def _build_encrypted_payload_variants(
        self,
        *,
        token_time: str,
        payload: dict[str, str],
        token_materials: list[dict[str, str]],
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

        seen: set[tuple[str, str]] = set()
        for material in token_materials:
            sign = str(material["sign"])
            aes_key_hex = str(material["aes_key_hex"])
            variant_id = f"time={material['time_mode']},key={material['key_mode']}"
            plaintext_candidates = [
                (f"json_token/{variant_id}", payload_json_with_token),
                (f"json_prefixed/{variant_id}", f"{token_time},{sign}|{payload_json}"),
                (f"pipe_prefixed/{variant_id}", f"{token_time},{sign}|{pipe_plain}"),
            ]
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

    def _decrypt_response(self, *, aes_key_hex: str, encoded: str) -> dict[str, Any]:
        try:
            decrypted = self._openssl_aes256_ecb_decrypt_base64(aes_key_hex, encoded)
            return self._parse_json_payload(decrypted)
        except Exception:
            return {"enc": 1, "data": encoded}

    def _send_tcp_json_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as sock:
            sock.settimeout(self.timeout_s)
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
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                return first
        return {}

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
