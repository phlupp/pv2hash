from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import grpc
from google.protobuf.json_format import MessageToDict

from pv2hash.logging_ext.setup import get_logger
from pv2hash.miners.base import MinerAdapter
from pv2hash.models.miner import MinerInfo, MinerProfile, MinerProfiles
from pv2hash.vendor.braiins_api_stubs_path import ensure_braiins_stubs_on_path

ensure_braiins_stubs_on_path()

import bos.version_pb2 as version_pb2
import bos.version_pb2_grpc as version_pb2_grpc
import bos.v1.actions_pb2 as actions_pb2
import bos.v1.actions_pb2_grpc as actions_pb2_grpc
import bos.v1.authentication_pb2 as authentication_pb2
import bos.v1.authentication_pb2_grpc as authentication_pb2_grpc
import bos.v1.common_pb2 as common_pb2
import bos.v1.configuration_pb2 as configuration_pb2
import bos.v1.configuration_pb2_grpc as configuration_pb2_grpc
import bos.v1.miner_pb2 as miner_pb2
import bos.v1.miner_pb2_grpc as miner_pb2_grpc
import bos.v1.performance_pb2 as performance_pb2
import bos.v1.performance_pb2_grpc as performance_pb2_grpc
import bos.v1.units_pb2 as units_pb2

logger = get_logger("pv2hash.miners.braiins")


class BraiinsMiner(MinerAdapter):
    """
    Native Braiins implementation via Python gRPC.

    Read:
    - GetApiVersion
    - Login
    - GetConstraints
    - GetMinerDetails
    - GetMinerStatus (first stream message only)
    - GetMinerStats
    - GetErrors
    - GetTunerState

    Write:
    - PauseMining
    - ResumeMining
    - Start
    - SetPowerTarget

    Semantics:
    - profile == "off"           -> PauseMining
    - profile power_w <= 0       -> PauseMining
    - profile power_w > 0        -> Resume/Start if needed, then SetPowerTarget
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
        profiles: dict[str, Any] | None = None,
        username: str = "root",
        password: str = "",
        timeout_s: float = 8.0,
        grpcurl_bin: str | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.username = username or "root"
        self.password = password or ""
        self.timeout_s = float(timeout_s)

        # Nur noch für Alt-Kompatibilität vorhanden.
        self.grpcurl_bin = grpcurl_bin

        self.target_profile = "off"
        self._token: str | None = None
        self._token_expires_monotonic: float = 0.0

        profile_cfg = profiles or {
            "floor": {"power_w": 0},
            "eco": {"power_w": 1200},
            "mid": {"power_w": 2200},
            "high": {"power_w": 3200},
        }

        if "floor" not in profile_cfg and "off" in profile_cfg:
            profile_cfg = dict(profile_cfg)
            profile_cfg["floor"] = profile_cfg["off"]

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
            is_active=enabled,
            priority=priority,
            serial_number=serial_number,
            model=model or "Unknown",
            firmware_version=firmware_version,
            profile="off",
            power_w=0.0,
            profiles=miner_profiles,
        )

        self._set_runtime_defaults()

    async def set_profile(self, profile: str) -> None:
        """
        Apply target profile to the real miner.

        Write errors are kept in last_error/logs, but do not raise into the control loop.
        """
        self.target_profile = profile
        self.info.profile = profile

        desired_w = 0.0 if profile == "off" else self.get_profile_power_w(profile)

        try:
            await asyncio.to_thread(self._apply_profile_sync, profile, desired_w)
            self.info.last_error = None

            # Wichtig:
            # paused/stopped Miner bleiben regelbar und damit aktiv im PV2Hash-Sinn.
            self.info.is_active = bool(self.info.enabled)

            if profile == "off" or desired_w <= 0:
                self.info.power_w = 0.0
                self.info.runtime_state = "paused"
            else:
                self.info.power_w = desired_w
                if self.info.runtime_state in {"paused", "stopped", "unknown"}:
                    self.info.runtime_state = "running"

        except Exception as exc:
            message = f"gRPC write failed: {exc}"
            logger.warning(
                "Braiins write failed for %s (%s:%s): %s",
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
            self._mark_unreachable(f"gRPC read failed: {exc}")

        self.info.last_seen = datetime.now(UTC)
        return self.info

    def _apply_profile_sync(self, profile: str, desired_w: float) -> None:
        target = f"{self.host}:{self.port}"
        channel = grpc.insecure_channel(target)

        try:
            grpc.channel_ready_future(channel).result(timeout=self.timeout_s)

            auth_stub = authentication_pb2_grpc.AuthenticationServiceStub(channel)
            actions_stub = actions_pb2_grpc.ActionsServiceStub(channel)
            perf_stub = performance_pb2_grpc.PerformanceServiceStub(channel)

            token = self._ensure_token_sync(auth_stub)
            metadata = [("authorization", token)]

            if profile == "off" or desired_w <= 0:
                if self.info.runtime_state not in {"paused", "stopped"}:
                    logger.info("PauseMining: miner=%s profile=%s", self.info.name, profile)
                    actions_stub.PauseMining(
                        actions_pb2.PauseMiningRequest(),
                        metadata=metadata,
                        timeout=self.timeout_s,
                    )
                return

            desired_w = self._validate_desired_power_or_raise(desired_w)

            if self.info.runtime_state == "stopped":
                logger.info("Start: miner=%s", self.info.name)
                actions_stub.Start(
                    actions_pb2.StartRequest(),
                    metadata=metadata,
                    timeout=self.timeout_s,
                )

            if self.info.runtime_state in {"paused", "unknown"}:
                logger.info("ResumeMining: miner=%s", self.info.name)
                actions_stub.ResumeMining(
                    actions_pb2.ResumeMiningRequest(),
                    metadata=metadata,
                    timeout=self.timeout_s,
                )

            if self._needs_power_target_update(desired_w):
                logger.info(
                    "SetPowerTarget: miner=%s profile=%s desired_w=%.0f",
                    self.info.name,
                    profile,
                    desired_w,
                )
                perf_stub.SetPowerTarget(
                    performance_pb2.SetPowerTargetRequest(
                        save_action=common_pb2.SAVE_ACTION_SAVE_AND_APPLY,
                        power_target=units_pb2.Power(watt=int(round(desired_w))),
                    ),
                    metadata=metadata,
                    timeout=self.timeout_s,
                )
        finally:
            channel.close()

    def _fetch_bundle_sync(self) -> dict[str, Any]:
        target = f"{self.host}:{self.port}"
        channel = grpc.insecure_channel(target)

        try:
            grpc.channel_ready_future(channel).result(timeout=self.timeout_s)

            api_stub = version_pb2_grpc.ApiVersionServiceStub(channel)
            auth_stub = authentication_pb2_grpc.AuthenticationServiceStub(channel)
            cfg_stub = configuration_pb2_grpc.ConfigurationServiceStub(channel)
            miner_stub = miner_pb2_grpc.MinerServiceStub(channel)
            perf_stub = performance_pb2_grpc.PerformanceServiceStub(channel)

            api_version_msg = api_stub.GetApiVersion(
                version_pb2.ApiVersionRequest(),
                timeout=self.timeout_s,
            )
            api_version = self._msg_to_dict(api_version_msg)

            token = self._ensure_token_sync(auth_stub)
            metadata = [("authorization", token)]

            constraints_msg = cfg_stub.GetConstraints(
                configuration_pb2.GetConstraintsRequest(),
                metadata=metadata,
                timeout=self.timeout_s,
            )
            constraints = self._msg_to_dict(constraints_msg)

            details_msg = miner_stub.GetMinerDetails(
                miner_pb2.GetMinerDetailsRequest(),
                metadata=metadata,
                timeout=self.timeout_s,
            )
            details = self._msg_to_dict(details_msg)

            status_stream = miner_stub.GetMinerStatus(
                miner_pb2.GetMinerStatusRequest(),
                metadata=metadata,
                timeout=self.timeout_s,
            )
            try:
                first_status_msg = next(status_stream)
                status_first = self._msg_to_dict(first_status_msg)
            except StopIteration:
                status_first = {}
            finally:
                try:
                    status_stream.cancel()
                except Exception:
                    pass

            stats_msg = miner_stub.GetMinerStats(
                miner_pb2.GetMinerStatsRequest(),
                metadata=metadata,
                timeout=self.timeout_s,
            )
            stats = self._msg_to_dict(stats_msg)

            errors_msg = miner_stub.GetErrors(
                miner_pb2.GetErrorsRequest(),
                metadata=metadata,
                timeout=self.timeout_s,
            )
            errors = self._msg_to_dict(errors_msg)

            tuner_state: dict[str, Any] = {}
            try:
                tuner_state_msg = perf_stub.GetTunerState(
                    performance_pb2.GetTunerStateRequest(),
                    metadata=metadata,
                    timeout=self.timeout_s,
                )
                tuner_state = self._msg_to_dict(tuner_state_msg)
            except Exception:
                tuner_state = {}

            return {
                "reachable": True,
                "api_version": api_version,
                "constraints": constraints,
                "details": details,
                "status_first": status_first,
                "stats": stats,
                "errors": errors,
                "tuner_state": tuner_state,
            }
        finally:
            channel.close()

    def _ensure_token_sync(
        self,
        auth_stub: authentication_pb2_grpc.AuthenticationServiceStub,
    ) -> str:
        now = time.monotonic()
        if self._token and now < self._token_expires_monotonic:
            return self._token

        login_response = auth_stub.Login(
            authentication_pb2.LoginRequest(
                username=self.username,
                password=self.password,
            ),
            timeout=self.timeout_s,
        )
        login_dict = self._msg_to_dict(login_response)

        token = getattr(login_response, "token", "") or login_dict.get("token", "")
        timeout_s = getattr(login_response, "timeout_s", 0) or login_dict.get("timeout_s", 0)

        if not token:
            raise RuntimeError("Login erfolgreich, aber kein Token erhalten")

        try:
            timeout_value = int(timeout_s)
        except Exception:
            timeout_value = 3600

        self._token = str(token)
        self._token_expires_monotonic = now + max(60, timeout_value - 30)
        return self._token

    def _apply_bundle(self, bundle: dict[str, Any]) -> None:
        self._set_runtime_defaults()

        self.info.last_seen = datetime.now(UTC)
        self.info.reachable = bool(bundle.get("reachable", False))

        api_version = bundle.get("api_version") or {}
        constraints = bundle.get("constraints") or {}
        details = bundle.get("details") or {}
        status_first = bundle.get("status_first") or {}
        errors = bundle.get("errors") or {}
        tuner_state = bundle.get("tuner_state") or {}

        self.info.api_version = self._format_api_version(api_version)

        bos_version = details.get("bos_version") or {}
        current_fw = bos_version.get("current")
        if current_fw:
            self.info.firmware_version = str(current_fw)

        miner_identity = details.get("miner_identity") or {}
        model_name = (
            miner_identity.get("miner_model")
            or miner_identity.get("name")
            or details.get("hostname")
            or self.info.model
        )
        if model_name:
            self.info.model = str(model_name)

        uid = details.get("uid")
        if uid and not self.info.serial_number:
            self.info.serial_number = str(uid)

        tuner_constraints = constraints.get("tuner_constraints") or {}
        power_target = tuner_constraints.get("power_target") or {}
        self.info.power_target_min_w = self._extract_watt_constraint(power_target, "min")
        self.info.power_target_default_w = self._extract_watt_constraint(power_target, "default")
        self.info.power_target_max_w = self._extract_watt_constraint(power_target, "max")

        self.info.control_mode = self._derive_control_mode(tuner_state, tuner_constraints)
        self.info.autotuning_enabled = self._extract_autotuning_enabled(tuner_state)

        current_target_w = self._extract_watt_from_tuner_state(tuner_state)
        if current_target_w is not None:
            self.info.power_w = current_target_w

        runtime_state = self._derive_runtime_state(
            details=details,
            status_first=status_first,
            current_target_w=current_target_w,
        )
        self.info.runtime_state = runtime_state

        # Wichtige Änderung:
        # paused/stopped Miner bleiben für PV2Hash "aktiv", weil sie weiter geregelt werden können.
        self.info.is_active = bool(
            self.info.enabled and self.info.reachable and runtime_state in {"running", "starting", "paused", "stopped"}
        )

        if runtime_state in {"paused", "stopped"}:
            self.info.power_w = 0.0

        inferred_profile = self._infer_profile_from_runtime(
            runtime_state=runtime_state,
            current_target_w=current_target_w,
        )
        if inferred_profile:
            self.info.profile = inferred_profile

        last_error = self._extract_last_error(errors)
        if last_error:
            self.info.last_error = last_error

    def _set_runtime_defaults(self) -> None:
        self.info.reachable = False
        self.info.runtime_state = "unknown"
        self.info.api_version = None
        self.info.control_mode = None
        self.info.autotuning_enabled = None
        self.info.power_target_min_w = None
        self.info.power_target_default_w = None
        self.info.power_target_max_w = None
        self.info.last_error = None

    def _mark_unreachable(self, message: str) -> None:
        self._set_runtime_defaults()
        self.info.reachable = False
        self.info.runtime_state = "unreachable"
        self.info.is_active = False
        self.info.power_w = 0.0
        self.info.last_error = str(message)
        self.info.last_seen = datetime.now(UTC)

    def _validate_desired_power_or_raise(self, desired_w: float) -> float:
        min_w = self.info.power_target_min_w
        max_w = self.info.power_target_max_w

        if min_w is not None and desired_w < float(min_w):
            raise RuntimeError(
                f"Desired power {desired_w:.0f} W liegt unter dem Miner-Minimum {float(min_w):.0f} W"
            )

        if max_w is not None and desired_w > float(max_w):
            raise RuntimeError(
                f"Desired power {desired_w:.0f} W liegt über dem Miner-Maximum {float(max_w):.0f} W"
            )

        return float(desired_w)

    def _needs_power_target_update(self, desired_w: float) -> bool:
        if self.info.runtime_state in {"unknown", "unreachable", "paused", "stopped"}:
            return True

        current_w = self.info.power_w
        if current_w is None:
            return True

        return abs(float(current_w) - float(desired_w)) >= 1.0

    def _infer_profile_from_runtime(
        self,
        *,
        runtime_state: str,
        current_target_w: float | None,
    ) -> str | None:
        if runtime_state in {"paused", "stopped"}:
            return "off"

        if current_target_w is None or self.info.profiles is None:
            return self.target_profile or self.info.profile

        candidates = {
            "floor": getattr(self.info.profiles, "floor", None),
            "eco": getattr(self.info.profiles, "eco", None),
            "mid": getattr(self.info.profiles, "mid", None),
            "high": getattr(self.info.profiles, "high", None),
        }

        best_name: str | None = None
        best_delta: float | None = None

        for name, profile in candidates.items():
            if profile is None:
                continue
            delta = abs(float(profile.power_w) - float(current_target_w))
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_name = name

        if best_name is not None and best_delta is not None and best_delta <= 75.0:
            return best_name

        return self.target_profile or self.info.profile

    @staticmethod
    def _msg_to_dict(message: Any) -> dict[str, Any]:
        return MessageToDict(
            message,
            preserving_proto_field_name=True,
            always_print_fields_with_no_presence=False,
        )

    @staticmethod
    def _format_api_version(api_version: dict[str, Any]) -> str | None:
        major = api_version.get("major")
        minor = api_version.get("minor")
        patch = api_version.get("patch")

        parts = [str(x) for x in (major, minor, patch) if x not in (None, "", 0, "0")]
        if not parts:
            return None
        return ".".join(parts)

    @staticmethod
    def _extract_watt_constraint(data: dict[str, Any], key: str) -> float | None:
        node = data.get(key)
        if not isinstance(node, dict):
            return None
        watt = node.get("watt")
        if watt in (None, ""):
            return None
        try:
            return float(watt)
        except Exception:
            return None

    @staticmethod
    def _extract_autotuning_enabled(tuner_state: dict[str, Any]) -> bool | None:
        value = tuner_state.get("enabled")
        if isinstance(value, bool):
            return value
        return None

    @staticmethod
    def _extract_watt_from_tuner_state(tuner_state: dict[str, Any]) -> float | None:
        power_target_mode_state = tuner_state.get("power_target_mode_state") or {}
        current_target = power_target_mode_state.get("current_target") or {}
        watt = current_target.get("watt")
        if watt in (None, ""):
            return None
        try:
            return float(watt)
        except Exception:
            return None

    @staticmethod
    def _derive_control_mode(
        tuner_state: dict[str, Any],
        tuner_constraints: dict[str, Any],
    ) -> str | None:
        if "power_target_mode_state" in tuner_state:
            return "power_target"
        if "hashrate_target_mode_state" in tuner_state:
            return "hashrate_target"

        default_mode = str(tuner_constraints.get("default_mode") or "")
        if default_mode == "TUNER_MODE_POWER_TARGET":
            return "power_target"
        if default_mode == "TUNER_MODE_HASHRATE_TARGET":
            return "hashrate_target"
        return None

    @staticmethod
    def _derive_runtime_state(
        *,
        details: dict[str, Any],
        status_first: dict[str, Any],
        current_target_w: float | None,
    ) -> str:
        detail_status = str(details.get("status") or "").upper()
        status_text = str(status_first).upper()

        if "PAUSE" in status_text:
            return "paused"
        if "STOP" in status_text:
            return "stopped"
        if "START" in status_text:
            return "starting"
        if "ERROR" in status_text:
            return "fault"

        if detail_status == "MINER_STATUS_NORMAL":
            if current_target_w is not None and current_target_w <= 0:
                return "paused"
            return "running"

        if "STOP" in detail_status:
            return "stopped"
        if "START" in detail_status:
            return "starting"
        if "ERROR" in detail_status:
            return "fault"

        return "unknown"

    @staticmethod
    def _extract_last_error(errors: dict[str, Any]) -> str | None:
        if not errors:
            return None

        if isinstance(errors, dict):
            if "errors" in errors and isinstance(errors["errors"], list) and errors["errors"]:
                first = errors["errors"][0]
                return str(first)
            if "message" in errors:
                return str(errors["message"])
            if errors:
                return str(errors)

        if isinstance(errors, list) and errors:
            return str(errors[0])

        return None