import asyncio
import json
import logging
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any

from pv2hash.miners.base import MinerAdapter
from pv2hash.models.miner import MinerInfo, MinerProfile, MinerProfiles

logger = logging.getLogger(__name__)


class BraiinsMiner(MinerAdapter):
    """
    Read-only Braiins integration via gRPC reflection using ``grpcurl``.

    Warum dieser Zwischenschritt?
    - Braiins Public API ist gRPC-basiert und Reflection-fähig.
    - Damit können wir heute schon echte Status- und Constraint-Daten lesen,
      ohne sofort protobuf-Stubs ins Repo zu vendoren.
    - Schreibende Calls (PauseMining / ResumeMining / SetPowerTarget) folgen im
      nächsten Schritt auf derselben Transportbasis.
    """

    def __init__(
        self,
        miner_id: str,
        name: str,
        host: str,
        port: int = 50051,
        priority: int = 100,
        enabled: bool = True,
        serial_number: str | None = None,
        model: str | None = None,
        firmware_version: str | None = None,
        profiles: dict | None = None,
        username: str | None = None,
        password: str | None = None,
        grpcurl_bin: str = "grpcurl",
    ) -> None:
        self.host = host
        self.port = port
        self.username = username or None
        self.password = password or None
        self.grpcurl_bin = grpcurl_bin or "grpcurl"
        self.target_profile = "off"
        self._auth_token: str | None = None
        self._auth_valid_until: datetime | None = None

        profile_cfg = profiles or {
            "floor": {"power_w": 0},
            "eco": {"power_w": 1200},
            "mid": {"power_w": 2200},
            "high": {"power_w": 3200},
        }

        miner_profiles = MinerProfiles(
            floor=MinerProfile(power_w=float(profile_cfg["floor"]["power_w"])),
            eco=MinerProfile(power_w=float(profile_cfg["eco"]["power_w"])),
            mid=MinerProfile(power_w=float(profile_cfg["mid"]["power_w"])),
            high=MinerProfile(power_w=float(profile_cfg["high"]["power_w"])),
        )

        self.info = MinerInfo(
            id=miner_id,
            name=name,
            host=host,
            driver="braiins",
            enabled=enabled,
            is_active=False,
            priority=priority,
            serial_number=serial_number,
            model=model or "Unknown",
            firmware_version=firmware_version,
            profile="off",
            power_w=0.0,
            profiles=miner_profiles,
            reachable=False,
            runtime_state="unknown",
            control_mode="power_target",
        )

    async def set_profile(self, profile: str) -> None:
        self.target_profile = profile
        self.info.profile = profile

        if profile == "off":
            self.info.power_w = 0.0
            self.info.runtime_state = "paused"
        else:
            desired_w = self.get_profile_power_w(profile)
            if desired_w <= 0:
                self.info.power_w = 0.0
                self.info.runtime_state = "paused"
            else:
                self.info.power_w = desired_w
                self.info.runtime_state = "running"

        self.info.last_error = None
        self.info.last_seen = datetime.now(UTC)

    async def get_status(self) -> MinerInfo:
        self.info.last_seen = datetime.now(UTC)
        self.info.last_error = None
        self.info.reachable = False
        self.info.is_active = False

        grpcurl_path = shutil.which(self.grpcurl_bin)
        if grpcurl_path is None:
            self.info.runtime_state = "unreachable"
            self.info.last_error = (
                f"grpcurl nicht gefunden ({self.grpcurl_bin}). "
                "Bitte auf dem Host installieren."
            )
            return self.info

        api_version = await self._grpc_call(
            "braiins.bos.ApiVersionService/GetApiVersion",
            data={},
            use_auth=False,
        )
        if api_version is None:
            self.info.runtime_state = "unreachable"
            self.info.last_error = "Braiins gRPC API nicht erreichbar"
            return self.info

        self.info.reachable = True
        self.info.api_version = self._format_api_version(api_version)

        if not self.username or not self.password:
            self.info.runtime_state = "unknown"
            self.info.last_error = (
                "Braiins Zugangsdaten fehlen. Bitte username/password im Miner hinterlegen."
            )
            return self.info

        if not await self._ensure_login():
            self.info.runtime_state = "unreachable"
            if not self.info.last_error:
                self.info.last_error = "Braiins Login fehlgeschlagen"
            return self.info

        details = await self._grpc_call(
            "braiins.bos.v1.MinerService/GetMinerDetails",
            data={},
        )
        status = await self._grpc_call(
            "braiins.bos.v1.MinerService/GetMinerStatus",
            data={},
        )
        stats = await self._grpc_call(
            "braiins.bos.v1.MinerService/GetMinerStats",
            data={},
        )
        errors = await self._grpc_call(
            "braiins.bos.v1.MinerService/GetErrors",
            data={},
        )
        constraints = await self._grpc_call(
            "braiins.bos.v1.ConfigurationService/GetConstraints",
            data={},
        )
        tuner_state = await self._grpc_call(
            "braiins.bos.v1.PerformanceService/GetTunerState",
            data={},
        )

        self._apply_details(details)
        self._apply_status(status or details)
        self._apply_stats(stats)
        self._apply_constraints(constraints)
        self._apply_tuner_state(tuner_state)
        self._apply_errors(errors)

        if self.info.runtime_state == "unknown" and self.info.reachable:
            self.info.runtime_state = "running"
            self.info.is_active = True

        return self.info

    async def _ensure_login(self, *, force: bool = False) -> bool:
        now = datetime.now(UTC)
        if (
            not force
            and self._auth_token
            and self._auth_valid_until is not None
            and now < self._auth_valid_until
        ):
            return True

        response = await self._grpc_call(
            "braiins.bos.v1.AuthenticationService/Login",
            data={
                "username": self.username,
                "password": self.password,
            },
            use_auth=False,
        )
        if not isinstance(response, dict):
            self._auth_token = None
            self._auth_valid_until = None
            self.info.last_error = "Braiins Login fehlgeschlagen"
            return False

        token = str(response.get("token") or "").strip()
        if not token:
            self._auth_token = None
            self._auth_valid_until = None
            self.info.last_error = "Braiins Login lieferte kein Token"
            return False

        timeout_s = self._coerce_int(response.get("timeoutS"), default=3600)
        timeout_s = max(timeout_s - 30, 60)

        self._auth_token = token
        self._auth_valid_until = now + timedelta(seconds=timeout_s)
        return True

    async def _grpc_call(
        self,
        method: str,
        *,
        data: dict[str, Any] | None = None,
        use_auth: bool = True,
        retry_auth: bool = True,
    ) -> dict[str, Any] | list[Any] | None:
        cmd = [
            self.grpcurl_bin,
            "-plaintext",
        ]

        if use_auth:
            if not self._auth_token:
                if not await self._ensure_login():
                    return None
            cmd.extend(["-H", f"authorization:{self._auth_token}"])

        cmd.extend(["-d", json.dumps(data or {}, separators=(",", ":"))])
        cmd.extend([f"{self.host}:{self.port}", method])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=6.0)
        except asyncio.TimeoutError:
            self.info.last_error = f"grpcurl Timeout bei {method}"
            return None
        except Exception as exc:
            self.info.last_error = f"grpcurl Fehler bei {method}: {exc}"
            return None

        stdout_text = stdout.decode(errors="ignore").strip()
        stderr_text = stderr.decode(errors="ignore").strip()

        if proc.returncode != 0:
            if use_auth and retry_auth and self._looks_like_auth_error(stderr_text):
                if await self._ensure_login(force=True):
                    return await self._grpc_call(
                        method,
                        data=data,
                        use_auth=use_auth,
                        retry_auth=False,
                    )

            self.info.last_error = stderr_text or stdout_text or f"grpcurl Rückgabecode {proc.returncode}"
            logger.debug("grpcurl call failed: method=%s stderr=%s", method, stderr_text)
            return None

        if not stdout_text:
            return {}

        try:
            return json.loads(stdout_text)
        except json.JSONDecodeError:
            self.info.last_error = f"Ungültige JSON-Antwort von {method}"
            logger.debug("Non-JSON grpcurl stdout for %s: %s", method, stdout_text)
            return None

    def _apply_details(self, details: dict[str, Any] | list[Any] | None) -> None:
        if not isinstance(details, dict):
            return

        identity = details.get("minerIdentity") or {}
        if isinstance(identity, dict):
            model = identity.get("minerModel") or identity.get("miner_model") or identity.get("name")
            if model:
                self.info.model = str(model)

        bos_version = details.get("bosVersion") or {}
        if isinstance(bos_version, dict):
            current = bos_version.get("current")
            if current:
                self.info.firmware_version = str(current)

        if details.get("uid") and not self.info.serial_number:
            self.info.serial_number = str(details.get("uid"))

    def _apply_status(self, status_response: dict[str, Any] | list[Any] | None) -> None:
        if not isinstance(status_response, dict):
            return

        status_value = str(
            status_response.get("status")
            or self._deep_find_value(status_response, {"status"})
            or ""
        ).upper()

        mapping = {
            "MINER_STATUS_NORMAL": ("running", True),
            "MINER_STATUS_RESTRICTED": ("running", True),
            "MINER_STATUS_PAUSED": ("paused", False),
            "MINER_STATUS_NOT_STARTED": ("stopped", False),
            "MINER_STATUS_SUSPENDED": ("fault", False),
        }

        runtime_state, is_active = mapping.get(status_value, ("unknown", False))
        self.info.runtime_state = runtime_state
        self.info.is_active = is_active

    def _apply_stats(self, stats: dict[str, Any] | list[Any] | None) -> None:
        power = self._find_first_number(
            stats,
            {
                "powerConsumption",
                "power_consumption",
                "powerDraw",
                "power_draw",
                "estimatedPowerConsumption",
                "estimated_power_consumption",
                "power",
                "consumption",
            },
        )
        if power is not None:
            self.info.power_w = power

    def _apply_constraints(self, constraints: dict[str, Any] | list[Any] | None) -> None:
        if not isinstance(constraints, dict):
            return

        tuner_constraints = constraints.get("tunerConstraints") or constraints.get("tuner_constraints")
        if not isinstance(tuner_constraints, dict):
            return

        power_target = tuner_constraints.get("powerTarget") or tuner_constraints.get("power_target")
        if not isinstance(power_target, dict):
            return

        self.info.power_target_min_w = self._extract_number(power_target.get("min"))
        self.info.power_target_default_w = self._extract_number(power_target.get("default"))
        self.info.power_target_max_w = self._extract_number(power_target.get("max"))

    def _apply_tuner_state(self, tuner_state: dict[str, Any] | list[Any] | None) -> None:
        if not isinstance(tuner_state, dict):
            return

        overall = str(tuner_state.get("overallTunerState") or "").upper()
        if overall:
            self.info.autotuning_enabled = overall != "TUNER_STATE_DISABLED"

        if "powerTargetModeState" in tuner_state:
            self.info.control_mode = "power_target"
            current_target = self._find_first_number(
                tuner_state["powerTargetModeState"],
                {"currentTarget", "current_target", "target"},
            )
            if self.info.power_w <= 0 and current_target is not None:
                self.info.power_w = current_target
        elif "hashrateTargetModeState" in tuner_state:
            self.info.control_mode = "hashrate_target"

    def _apply_errors(self, errors: dict[str, Any] | list[Any] | None) -> None:
        messages = self._collect_messages(errors)
        if messages:
            self.info.last_error = messages[0]

    def _format_api_version(self, response: dict[str, Any] | list[Any]) -> str | None:
        if not isinstance(response, dict):
            return None
        parts = [
            str(response.get("major") or "").strip(),
            str(response.get("minor") or "").strip(),
            str(response.get("patch") or "").strip(),
        ]
        if not all(parts):
            return None
        version = ".".join(parts)
        pre = str(response.get("pre") or "").strip()
        build = str(response.get("build") or "").strip()
        if pre:
            version = f"{version}-{pre}"
        if build:
            version = f"{version}+{build}"
        return version

    def _collect_messages(self, data: Any) -> list[str]:
        messages: list[str] = []
        if isinstance(data, dict):
            for key, value in data.items():
                if key in {"message", "reason", "hint"} and isinstance(value, str) and value.strip():
                    messages.append(value.strip())
                else:
                    messages.extend(self._collect_messages(value))
        elif isinstance(data, list):
            for item in data:
                messages.extend(self._collect_messages(item))
        return messages

    def _find_first_number(self, data: Any, keys: set[str]) -> float | None:
        if isinstance(data, dict):
            for key, value in data.items():
                if key in keys:
                    number = self._extract_number(value)
                    if number is not None:
                        return number
                number = self._find_first_number(value, keys)
                if number is not None:
                    return number
        elif isinstance(data, list):
            for item in data:
                number = self._find_first_number(item, keys)
                if number is not None:
                    return number
        return None

    def _deep_find_value(self, data: Any, keys: set[str]) -> Any | None:
        if isinstance(data, dict):
            for key, value in data.items():
                if key in keys:
                    return value
                nested = self._deep_find_value(value, keys)
                if nested is not None:
                    return nested
        elif isinstance(data, list):
            for item in data:
                nested = self._deep_find_value(item, keys)
                if nested is not None:
                    return nested
        return None

    def _extract_number(self, value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        if isinstance(value, dict):
            for nested in value.values():
                number = self._extract_number(nested)
                if number is not None:
                    return number
        if isinstance(value, list):
            for nested in value:
                number = self._extract_number(nested)
                if number is not None:
                    return number
        return None

    def _coerce_int(self, value: Any, *, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _looks_like_auth_error(self, text: str) -> bool:
        lowered = text.lower()
        return "unauth" in lowered or "permission denied" in lowered or "token" in lowered
