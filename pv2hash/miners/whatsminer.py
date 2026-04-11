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

    Read path:
    - summary
    - status
    - get_version
    - devdetails

    Write path:
    - power_off
    - power_on
    - power value only (no percent fallback)

    Notes:
    - Readable API is sent as plaintext JSON over TCP/4028.
    - Writable API uses the documented get_token + encrypted payload flow.
    - The exact power-value command name differs by firmware/API generation,
      therefore a small list of power-value command candidates is tried.
    """

    POWER_VALUE_COMMAND_CANDIDATES = (
        "set_power_value",
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
                self.info.power_w = desired_w
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
            self.info.power_w = power_limit_w
        elif actual_power_w is not None:
            self.info.power_w = actual_power_w

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
            inferred_profile = self._infer_profile_from_power(power_limit_w if power_limit_w is not None else actual_power_w)
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

        key = self._openssl_md5_crypt_fragment(salt=salt, value=self.password)
        sign = self._openssl_md5_crypt_fragment(salt=newsalt, value=f"{key}{token_time[-4:]}")
        aes_key_hex = hashlib.sha256(key.encode("utf-8")).hexdigest()

        api_parts = [cmd]
        if params:
            api_parts.extend(str(param) for param in params if param is not None)
        api_str = "|".join(api_parts)
        plaintext = f"{token_time},{sign}|{api_str}"
        encrypted = self._openssl_aes256_ecb_encrypt_base64(aes_key_hex, plaintext)

        response = self._send_tcp_json_sync({"enc": 1, "data": encrypted})
        if "enc" in response and "data" in response:
            decrypted = self._openssl_aes256_ecb_decrypt_base64(aes_key_hex, str(response["data"]))
            return self._parse_json_payload(decrypted)
        return response

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

    def _openssl_md5_crypt_fragment(self, *, salt: str, value: str) -> str:
        result = subprocess.run(
            ["openssl", "passwd", "-1", "-salt", salt, value],
            capture_output=True,
            text=True,
            check=True,
        )
        output = result.stdout.strip()
        if not output:
            raise RuntimeError("openssl passwd -1 lieferte keine Ausgabe")
        return output.split("$")[-1]

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
