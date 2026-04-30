from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import grpc
from google.protobuf.json_format import MessageToDict

from pv2hash.logging_ext.setup import get_logger
from pv2hash.miners.base import DriverAction, DriverField, MinerAdapter
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
    DRIVER_LABEL = "Braiins OS+"

    @classmethod
    def get_config_schema(cls) -> list[DriverField]:
        return [
            DriverField(name="host", label="Host / IP", type="text", required=True, placeholder="192.168.x.x", help="IP-Adresse oder Hostname des Braiins OS+ Miners.", create_phase="basic", layout={"width": "half"}),
            DriverField(name="settings.port", label="gRPC-Port", type="number", required=True, preset=50051, default=50051, placeholder="50051", help="gRPC-Port der Braiins OS+ API.", create_phase="basic", layout={"width": "quarter"}),
            DriverField(name="settings.timeout_s", label="Timeout", type="number", unit="s", preset=2, default=2, placeholder="2", help="gRPC-Timeout für Braiins API-Requests.", advanced=True, layout={"width": "quarter"}),
            DriverField(name="settings.username", label="Benutzer", type="text", required=True, preset="root", default="root", placeholder="root", help="Braiins OS+ API-Benutzer.", create_phase="basic", layout={"width": "half"}),
            DriverField(name="settings.password", label="Passwort", type="password", required=True, default="", placeholder="Passwort", help="Passwort des Braiins OS+ API-Benutzers.", create_phase="basic", layout={"width": "half"}),
        ]

    @classmethod
    def get_actions_schema(cls) -> list[DriverAction]:
        return [
            DriverAction(name="pause_mining", label="Mining pausieren", description="Pausiert das Mining per Braiins PauseMining.", confirm_text="Mining auf diesem Miner wirklich pausieren?", disabled_when_control_enabled=True),
            DriverAction(name="resume_mining", label="Mining fortsetzen", description="Setzt das Mining per Braiins ResumeMining fort.", confirm_text="Mining auf diesem Miner wirklich fortsetzen?", disabled_when_control_enabled=True),
            DriverAction(name="start_miner", label="Miner starten", description="Startet den Miner per Braiins Start.", confirm_text="Miner wirklich starten?"),
            DriverAction(name="reboot_system", label="Miner neu starten", description="Startet das Braiins OS+ Gerät per Reboot neu.", confirm_text="Miner jetzt wirklich neu starten?", dangerous=True),
        ]

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
    - profile == "off"        -> PauseMining
    - profile power_w <= 0    -> PauseMining
    - profile p1/p2/p3/p4 > 0 -> Resume/Start if needed, then SetPowerTarget
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
        min_regulated_profile: str = "off",
        username: str = "root",
        password: str = "",
        timeout_s: float = 8.0,
        grpcurl_bin: str | None = None,
        power_limit_w: float = 0.0,
        use_battery_when_charging: bool = False,
        battery_charge_soc_min: float = 95.0,
        battery_charge_profile: str = "p1",
        use_battery_when_discharging: bool = False,
        battery_discharge_soc_min: float = 80.0,
        battery_discharge_profile: str = "p1",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.username = username or "root"
        self.password = password or ""
        self.timeout_s = float(timeout_s)

        # Nur noch für Alt-Kompatibilität vorhanden.
        self.grpcurl_bin = grpcurl_bin
        self.configured_power_limit_w = float(power_limit_w or 0.0)

        self.target_profile = "off"
        self._token: str | None = None
        self._token_expires_monotonic: float = 0.0
        self._last_bundle: dict[str, Any] = {}
        self._last_bundle_at: datetime | None = None
        self._last_power_target_w: float | None = None
        self._last_actual_power_w: float | None = None

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
            min_regulated_profile=normalized_min_regulated_profile,
            use_battery_when_charging=bool(use_battery_when_charging),
            battery_charge_soc_min=float(battery_charge_soc_min),
            battery_charge_profile=battery_charge_profile,
            use_battery_when_discharging=bool(use_battery_when_discharging),
            battery_discharge_soc_min=float(battery_discharge_soc_min),
            battery_discharge_profile=battery_discharge_profile,
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

    def apply_action(self, action_name: str) -> dict[str, Any]:
        command_map = {
            "pause_mining": ("PauseMining", actions_pb2.PauseMiningRequest, "Mining pausiert"),
            "resume_mining": ("ResumeMining", actions_pb2.ResumeMiningRequest, "Mining fortgesetzt"),
            "start_miner": ("Start", actions_pb2.StartRequest, "Miner-Start ausgelöst"),
            "reboot_system": ("Reboot", actions_pb2.RebootRequest, "Miner-Neustart ausgelöst"),
        }
        if action_name not in command_map:
            return {"ok": False, "message": f"Unbekannte Aktion: {action_name}"}

        method_name, request_cls, success_message = command_map[action_name]

        try:
            self._run_action_sync(method_name, request_cls)
        except Exception as exc:
            logger.warning(
                "Braiins action %s failed for %s (%s:%s): %s",
                action_name, self.info.name, self.host, self.port, exc,
            )
            self.info.last_error = f"gRPC action failed: {exc}"
            return {"ok": False, "message": f"Braiins-Aktion fehlgeschlagen: {exc}"}

        if action_name == "pause_mining":
            self.info.runtime_state = "paused"
            self.info.power_w = 0.0
        elif action_name in {"resume_mining", "start_miner"}:
            self.info.runtime_state = "starting"
        elif action_name == "reboot_system":
            self.info.runtime_state = "rebooting"

        self.info.last_error = None
        self.info.last_seen = datetime.now(UTC)
        return {"ok": True, "message": success_message}

    def _run_action_sync(self, method_name: str, request_cls: Any) -> None:
        target = f"{self.host}:{self.port}"
        channel = grpc.insecure_channel(target)

        try:
            grpc.channel_ready_future(channel).result(timeout=self.timeout_s)
            auth_stub = authentication_pb2_grpc.AuthenticationServiceStub(channel)
            actions_stub = actions_pb2_grpc.ActionsServiceStub(channel)
            token = self._ensure_token_sync(auth_stub)
            metadata = [("authorization", token)]
            method = getattr(actions_stub, method_name)
            logger.info("Braiins action %s: miner=%s", method_name, self.info.name)
            method(request_cls(), metadata=metadata, timeout=self.timeout_s)
        finally:
            channel.close()

    def get_details(self) -> dict:
        bundle = self._last_bundle or {}
        if not bundle:
            try:
                bundle = self._fetch_bundle_sync()
                self._apply_bundle(bundle)
            except Exception as exc:
                logger.debug("Braiins details refresh failed for %s (%s:%s): %s", self.info.name, self.host, self.port, exc)
                bundle = {}

        api_version = bundle.get("api_version") or {}
        constraints = bundle.get("constraints") or {}
        details = bundle.get("details") or {}
        status_first = bundle.get("status_first") or {}
        stats = bundle.get("stats") or {}
        errors = bundle.get("errors") or {}
        tuner_state = bundle.get("tuner_state") or {}

        miner_stats = stats.get("miner_stats") if isinstance(stats.get("miner_stats"), dict) else {}
        real_hashrate = miner_stats.get("real_hashrate") if isinstance(miner_stats.get("real_hashrate"), dict) else {}
        tuner_constraints = constraints.get("tuner_constraints") if isinstance(constraints.get("tuner_constraints"), dict) else {}
        power_target = tuner_constraints.get("power_target") if isinstance(tuner_constraints.get("power_target"), dict) else {}
        current_target_w = self._extract_watt_from_tuner_state(tuner_state)
        actual_power_w = self._extract_actual_power_w(stats)
        efficiency_j_th = self._extract_efficiency_j_th(stats)

        return {
            "sections": [
                {"id": "overview", "title": "Übersicht", "items": [
                    {"label": "Runtime", "value": str(self.info.runtime_state)},
                    {"label": "Modell", "value": str(self.info.model or "—")},
                    {"label": "Serial / UID", "value": str(self.info.serial_number or details.get("uid") or "—")},
                    {"label": "Hostname", "value": str(details.get("hostname", "—"))},
                    {"label": "API", "value": self._format_api_version(api_version) or "—"},
                    {"label": "Firmware", "value": str(self.info.firmware_version or "—")},
                ]},
                {"id": "performance", "title": "Leistung / Hashrate", "items": [
                    {"label": "Aktuelle Leistung", "value": self._format_watt(actual_power_w)},
                    {"label": "Power Target aktuell", "value": self._format_watt(current_target_w)},
                    {"label": "Power Target min", "value": self._format_watt(self._extract_watt_constraint(power_target, "min"))},
                    {"label": "Power Target default", "value": self._format_watt(self._extract_watt_constraint(power_target, "default"))},
                    {"label": "Power Target max", "value": self._format_watt(self._extract_watt_constraint(power_target, "max"))},
                    {"label": "Effizienz", "value": self._format_efficiency(efficiency_j_th)},
                    {"label": "Nominale Hashrate", "value": self._format_hashrate_node(miner_stats.get("nominal_hashrate"))},
                    {"label": "Hashrate 5s", "value": self._format_hashrate_node(real_hashrate.get("last_5s"))},
                    {"label": "Hashrate 1m", "value": self._format_hashrate_node(real_hashrate.get("last_1m"))},
                    {"label": "Hashrate 5m", "value": self._format_hashrate_node(real_hashrate.get("last_5m"))},
                ]},
                {"id": "tuner", "title": "Tuner", "items": [
                    {"label": "Control Mode", "value": str(self.info.control_mode or "—")},
                    {"label": "Autotuning", "value": self._format_bool(self.info.autotuning_enabled)},
                    {"label": "Default Mode", "value": str(tuner_constraints.get("default_mode", "—"))},
                ]},
                {"id": "status", "title": "Status-Rohdaten", "items": [
                    {"label": "Miner Status", "value": str(details.get("status", "—"))},
                    {"label": "Status Stream", "value": self._short_dict_value(status_first)},
                ]},
                {"id": "errors", "title": "Fehler", "items": [{
                    "label": "Letzte Fehler",
                    "kind": "table",
                    "columns": [
                        {"key": "source", "label": "Quelle"},
                        {"key": "severity", "label": "Level"},
                        {"key": "message", "label": "Meldung"},
                    ],
                    "rows": self._error_rows(errors, limit=5),
                    "empty": "Keine Fehler gemeldet",
                }]},
            ]
        }

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
        self._last_bundle = dict(bundle or {})
        self._last_bundle_at = datetime.now(UTC)
        self._set_runtime_defaults()

        self.info.last_seen = datetime.now(UTC)
        self.info.reachable = bool(bundle.get("reachable", False))

        api_version = bundle.get("api_version") or {}
        constraints = bundle.get("constraints") or {}
        details = bundle.get("details") or {}
        status_first = bundle.get("status_first") or {}
        stats = bundle.get("stats") or {}
        errors = bundle.get("errors") or {}
        tuner_state = bundle.get("tuner_state") or {}

        self.info.api_version = self._format_api_version(api_version)
        self.info.current_hashrate_ghs = self._extract_hashrate_ghs(stats)

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
        actual_power_w = self._extract_actual_power_w(stats)
        self._last_power_target_w = current_target_w
        self._last_actual_power_w = actual_power_w

        if actual_power_w is not None:
            self.info.power_w = actual_power_w
        elif current_target_w is not None:
            # Fallback for API versions that do not expose live consumption.
            self.info.power_w = current_target_w

        runtime_state = self._derive_runtime_state(
            details=details,
            status_first=status_first,
            current_target_w=current_target_w,
        )
        self.info.runtime_state = runtime_state

        self.info.is_active = bool(
            self.info.enabled
            and self.info.reachable
            and runtime_state in {"running", "starting", "paused", "stopped"}
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
        self.info.current_hashrate_ghs = None
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

        # Compare SetPowerTarget writes against the current target, not live
        # consumption. Live consumption fluctuates around the target and would
        # otherwise trigger repeated unnecessary writes.
        current_w = self._last_power_target_w
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
            "p1": getattr(self.info.profiles, "p1", None),
            "p2": getattr(self.info.profiles, "p2", None),
            "p3": getattr(self.info.profiles, "p3", None),
            "p4": getattr(self.info.profiles, "p4", None),
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
    def _format_watt(value: Any) -> str:
        try:
            if value is None:
                return "—"
            return f"{float(value):.0f} W"
        except Exception:
            return "—"

    @staticmethod
    def _format_efficiency(value: Any) -> str:
        try:
            if value is None:
                return "—"
            return f"{float(value):.1f} J/TH"
        except Exception:
            return "—"

    @staticmethod
    def _format_bool(value: Any) -> str:
        if value is True:
            return "Ja"
        if value is False:
            return "Nein"
        return "—"

    @staticmethod
    def _format_hashrate_node(node: Any) -> str:
        value = BraiinsMiner._extract_hashrate_value_ghs(node)
        if value is None:
            return "—"
        if value >= 1000.0:
            return f"{value / 1000.0:.3f} TH/s"
        return f"{value:.0f} GH/s"

    @staticmethod
    def _short_dict_value(value: Any) -> str:
        if value in (None, "", {}, []):
            return "—"
        text = str(value)
        return text[:177] + "…" if len(text) > 180 else text

    @staticmethod
    def _error_rows(errors: Any, limit: int = 5) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []

        def add_row(source: str, severity: str, message: Any) -> None:
            if message in (None, "", {}, []):
                return
            rows.append({"source": str(source or "—"), "severity": str(severity or "—"), "message": BraiinsMiner._short_dict_value(message)})

        if isinstance(errors, dict):
            raw_errors = errors.get("errors")
            if isinstance(raw_errors, list):
                for entry in raw_errors[-limit:]:
                    if isinstance(entry, dict):
                        add_row(entry.get("source") or entry.get("component") or "Braiins", entry.get("severity") or entry.get("level") or entry.get("type") or "—", entry.get("message") or entry.get("reason") or entry.get("description") or entry)
                    else:
                        add_row("Braiins", "—", entry)
            elif raw_errors:
                add_row("Braiins", "—", raw_errors)
            for key in ("message", "reason", "description"):
                if key in errors:
                    add_row("Braiins", "—", errors.get(key))
            if not rows and errors:
                add_row("Braiins", "—", errors)
        elif isinstance(errors, list):
            for entry in errors[-limit:]:
                add_row("Braiins", "—", entry)
        elif errors:
            add_row("Braiins", "—", errors)

        return rows[-limit:]

    @staticmethod
    def _msg_to_dict(message: Any) -> dict[str, Any]:
        return MessageToDict(
            message,
            preserving_proto_field_name=True,
            always_print_fields_with_no_presence=False,
        )

    @staticmethod
    def _extract_hashrate_ghs(stats: dict[str, Any]) -> float | None:
        if not isinstance(stats, dict):
            return None

        miner_stats = stats.get("miner_stats") or {}
        if not isinstance(miner_stats, dict):
            return None

        real_hashrate = miner_stats.get("real_hashrate") or {}
        candidates = []
        if isinstance(real_hashrate, dict):
            candidates.extend(
                [
                    real_hashrate.get("last_1m"),
                    real_hashrate.get("last_5m"),
                    real_hashrate.get("last_5s"),
                    real_hashrate.get("since_restart"),
                ]
            )
        candidates.append(miner_stats.get("nominal_hashrate"))

        for candidate in candidates:
            value = BraiinsMiner._extract_hashrate_value_ghs(candidate)
            if value is not None and value >= 0:
                return value

        return None

    @staticmethod
    def _extract_hashrate_value_ghs(node: Any) -> float | None:
        if not isinstance(node, dict):
            return None

        gigahash = node.get("gigahash_per_second")
        if gigahash not in (None, ""):
            try:
                return float(gigahash)
            except Exception:
                return None

        terahash = node.get("terahash_per_second")
        if terahash not in (None, ""):
            try:
                return float(terahash) * 1000.0
            except Exception:
                return None

        megahash = node.get("megahash_per_second")
        if megahash not in (None, ""):
            try:
                return float(megahash) / 1000.0
            except Exception:
                return None

        return None

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
    def _extract_actual_power_w(stats: dict[str, Any]) -> float | None:
        if not isinstance(stats, dict):
            return None
        power_stats = stats.get("power_stats") or {}
        if not isinstance(power_stats, dict):
            return None
        consumption = power_stats.get("approximated_consumption") or {}
        if not isinstance(consumption, dict):
            return None
        watt = consumption.get("watt")
        if watt in (None, ""):
            return None
        try:
            return float(watt)
        except Exception:
            return None

    @staticmethod
    def _extract_efficiency_j_th(stats: dict[str, Any]) -> float | None:
        if not isinstance(stats, dict):
            return None
        power_stats = stats.get("power_stats") or {}
        if not isinstance(power_stats, dict):
            return None
        efficiency = power_stats.get("efficiency") or {}
        if not isinstance(efficiency, dict):
            return None
        value = efficiency.get("joule_per_terahash")
        if value in (None, ""):
            return None
        try:
            return float(value)
        except Exception:
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