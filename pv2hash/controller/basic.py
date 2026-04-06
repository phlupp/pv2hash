from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from pv2hash.controller.distribution import (
    PROFILE_ORDER,
    apply_profile_caps,
    get_current_profiles,
    get_step_down_plan,
    get_step_up_plan,
    is_profile_higher,
    max_profile,
)
from pv2hash.logging_ext.setup import get_logger
from pv2hash.models.energy import EnergySnapshot

logger = get_logger("pv2hash.controller.basic")


@dataclass
class ControlDecision:
    profiles: list[str]
    action: str
    summary: str


@dataclass
class ControllerState:
    last_live_profiles: list[str] | None = None
    live_profiles_since_monotonic: float | None = None
    degraded_quality: str | None = None
    degraded_since_monotonic: float | None = None
    import_exceeded_since_monotonic: float | None = None
    last_fallback_log_key: str | None = None
    last_live_hold_log_key: str | None = None
    last_import_log_key: str | None = None
    last_battery_log_key: str | None = None


@dataclass
class MinerBatteryPolicy:
    target_profile: str | None
    max_profile: str
    reason: str | None


@dataclass
class BatteryContext:
    mode: str | None
    soc_pct: float | None
    charge_power_w: float
    discharge_power_w: float
    active: bool
    available_charge_surplus_w: float
    policies: list[MinerBatteryPolicy]


class BasicController:
    def __init__(self, control_config: dict, battery_config: dict | None = None) -> None:
        self.min_switch_interval_seconds = float(
            control_config.get("min_switch_interval_seconds", 0)
        )
        self.switch_hysteresis_w = float(control_config.get("switch_hysteresis_w", 0))
        self.max_import_w = max(0.0, float(control_config.get("max_import_w", 200)))
        self.import_hold_seconds = float(control_config.get("import_hold_seconds", 15))
        self.source_loss = control_config.get("source_loss", {})
        battery_config = battery_config or {}
        self.battery_charge_active_threshold_w = max(
            0.0,
            float(battery_config.get("charge_active_threshold_w", 100.0)),
        )
        self.battery_discharge_active_threshold_w = max(
            0.0,
            float(battery_config.get("discharge_active_threshold_w", 100.0)),
        )
        self.state = ControllerState()

    def decide(
        self,
        *,
        snapshot: EnergySnapshot,
        miners: list,
        distribution_mode: str,
    ) -> ControlDecision:
        quality = self._normalize_quality(snapshot.quality)

        if quality == "live":
            if self.state.degraded_quality is not None:
                logger.info(
                    "Source recovered: %s -> live",
                    self.state.degraded_quality,
                )
            self.state.degraded_quality = None
            self.state.degraded_since_monotonic = None
            self.state.last_fallback_log_key = None

            return self._decide_live(
                snapshot=snapshot,
                miners=miners,
                distribution_mode=distribution_mode,
            )

        return self._decide_degraded(
            quality=quality,
            miners=miners,
        )

    def _decide_live(
        self,
        *,
        snapshot: EnergySnapshot,
        miners: list,
        distribution_mode: str,
    ) -> ControlDecision:
        now_mono = monotonic()
        grid_power_w = float(snapshot.grid_power_w)
        current_profiles = get_current_profiles(miners)
        battery_context = self._build_battery_context(snapshot=snapshot, miners=miners)
        max_profiles = [policy.max_profile for policy in battery_context.policies]
        target_profiles = [
            policy.target_profile or current_profiles[idx]
            for idx, policy in enumerate(battery_context.policies)
        ]

        if self.state.last_live_profiles is None:
            self.state.last_live_profiles = current_profiles.copy()
            logger.info(
                "Initial live state: profiles=%s grid_power_w=%.1f",
                ",".join(current_profiles) if current_profiles else "-",
                grid_power_w,
            )

        candidate_profiles = current_profiles
        action = "hold"
        summary = f"hold ({distribution_mode})"

        capped_current_profiles = apply_profile_caps(current_profiles, max_profiles)
        if capped_current_profiles != current_profiles:
            candidate_profiles = capped_current_profiles
            action = "battery_limit"
            summary = self._build_battery_summary(
                battery_context=battery_context,
                fallback=f"battery_limit ({distribution_mode})",
            )
            self._log_battery_once(
                f"limit:{current_profiles}->{candidate_profiles}",
                "Battery limit active: mode=%s current=%s candidate=%s grid_power_w=%.1f",
                battery_context.mode or "inactive",
                ",".join(current_profiles),
                ",".join(candidate_profiles),
                grid_power_w,
            )
        else:
            has_battery_force_up = any(
                policy.target_profile is not None
                and is_profile_higher(policy.target_profile, current_profiles[idx])
                for idx, policy in enumerate(battery_context.policies)
            )

            if has_battery_force_up and self._can_force_battery_targets(
                battery_context=battery_context,
                current_profiles=current_profiles,
                target_profiles=target_profiles,
                miners=miners,
                grid_power_w=grid_power_w,
            ):
                candidate_profiles = target_profiles
                action = "battery_target"
                summary = self._build_battery_summary(
                    battery_context=battery_context,
                    fallback=f"battery_target ({distribution_mode})",
                )
                self._log_battery_once(
                    f"target:{current_profiles}->{candidate_profiles}",
                    "Battery target active: mode=%s current=%s candidate=%s grid_power_w=%.1f",
                    battery_context.mode or "inactive",
                    ",".join(current_profiles),
                    ",".join(candidate_profiles),
                    grid_power_w,
                )
            else:
                if self._should_step_down(
                    grid_power_w=grid_power_w,
                    now_monotonic=now_mono,
                    current_profiles=current_profiles,
                ):
                    down_plan = get_step_down_plan(distribution_mode, miners)

                    if down_plan.changed:
                        candidate_profiles = down_plan.profiles
                        action = "step_down"
                        summary = (
                            f"step_down ({distribution_mode}, "
                            f"release≈{down_plan.delta_power_w:.0f}W)"
                        )
                else:
                    up_plan = get_step_up_plan(distribution_mode, miners)
                    required_export_w = up_plan.delta_power_w + self.switch_hysteresis_w

                    if (
                        up_plan.changed
                        and up_plan.delta_power_w > 0
                        and grid_power_w < -required_export_w
                    ):
                        candidate_profiles = up_plan.profiles
                        action = "step_up"
                        summary = (
                            f"step_up ({distribution_mode}, "
                            f"need≈{up_plan.delta_power_w:.0f}W)"
                        )

                uncapped_candidate_profiles = candidate_profiles
                candidate_profiles = apply_profile_caps(candidate_profiles, max_profiles)
                if candidate_profiles != uncapped_candidate_profiles:
                    if uncapped_candidate_profiles == current_profiles:
                        action = "hold"
                        summary = self._build_battery_summary(
                            battery_context=battery_context,
                            fallback=f"hold ({distribution_mode})",
                        )
                    else:
                        summary = self._build_battery_summary(
                            battery_context=battery_context,
                            fallback=summary,
                        )

        if candidate_profiles == current_profiles:
            self.state.last_live_profiles = current_profiles.copy()
            self.state.last_live_hold_log_key = None
            return ControlDecision(
                profiles=current_profiles,
                action="hold",
                summary=summary,
            )

        elapsed = (
            now_mono - self.state.live_profiles_since_monotonic
            if self.state.live_profiles_since_monotonic is not None
            else 999999.0
        )

        if (
            self.min_switch_interval_seconds > 0
            and elapsed < self.min_switch_interval_seconds
        ):
            self._log_live_hold_once(
                f"{current_profiles}->{candidate_profiles}",
                (
                    "Live switch suppressed: current=%s candidate=%s "
                    "grid_power_w=%.1f elapsed=%.1fs min_switch_interval=%.1fs"
                ),
                ",".join(current_profiles),
                ",".join(candidate_profiles),
                grid_power_w,
                elapsed,
                self.min_switch_interval_seconds,
            )
            self.state.last_live_profiles = current_profiles.copy()
            return ControlDecision(
                profiles=current_profiles,
                action="hold",
                summary=self._build_battery_summary(
                    battery_context=battery_context,
                    fallback=f"hold ({distribution_mode}, min-switch-interval)",
                ),
            )

        logger.info(
            "Live profile switch: %s -> %s (grid_power_w=%.1f)",
            ",".join(current_profiles),
            ",".join(candidate_profiles),
            grid_power_w,
        )

        self.state.last_live_profiles = candidate_profiles.copy()
        self.state.live_profiles_since_monotonic = now_mono
        self.state.last_live_hold_log_key = None

        if action in {"step_down", "battery_limit"}:
            self._reset_import_tracking()
        else:
            self.state.last_import_log_key = None

        return ControlDecision(
            profiles=candidate_profiles,
            action=action,
            summary=summary,
        )

    def _build_battery_context(
        self,
        *,
        snapshot: EnergySnapshot,
        miners: list,
    ) -> BatteryContext:
        charge_power_w = float(snapshot.battery_charge_power_w or 0.0)
        discharge_power_w = float(snapshot.battery_discharge_power_w or 0.0)

        if snapshot.battery_is_charging is None:
            is_charging = charge_power_w >= self.battery_charge_active_threshold_w
        else:
            is_charging = bool(snapshot.battery_is_charging) or (
                charge_power_w >= self.battery_charge_active_threshold_w
            )

        if snapshot.battery_is_discharging is None:
            is_discharging = discharge_power_w >= self.battery_discharge_active_threshold_w
        else:
            is_discharging = bool(snapshot.battery_is_discharging) or (
                discharge_power_w >= self.battery_discharge_active_threshold_w
            )

        mode: str | None = None
        if is_charging and is_discharging:
            mode = "charging" if charge_power_w >= discharge_power_w else "discharging"
        elif is_charging:
            mode = "charging"
        elif is_discharging:
            mode = "discharging"

        active = mode is not None
        soc_pct = snapshot.battery_soc_pct
        available_charge_surplus_w = max(0.0, -float(snapshot.grid_power_w)) + max(
            0.0,
            charge_power_w,
        ) + self.max_import_w

        policies = [
            self._build_miner_battery_policy(
                miner=miner,
                mode=mode,
                soc_pct=soc_pct,
            )
            for miner in miners
        ]

        return BatteryContext(
            mode=mode,
            soc_pct=soc_pct,
            charge_power_w=charge_power_w,
            discharge_power_w=discharge_power_w,
            active=active,
            available_charge_surplus_w=available_charge_surplus_w,
            policies=policies,
        )

    def _build_miner_battery_policy(
        self,
        *,
        miner,
        mode: str | None,
        soc_pct: float | None,
    ) -> MinerBatteryPolicy:
        min_profile = miner.get_min_regulated_profile()
        unrestricted = MinerBatteryPolicy(
            target_profile=None,
            max_profile="p4",
            reason=None,
        )

        if mode == "discharging":
            if not miner.use_battery_when_discharging():
                return MinerBatteryPolicy(
                    target_profile=min_profile,
                    max_profile=min_profile,
                    reason="battery_discharge_blocked",
                )

            if soc_pct is None:
                return MinerBatteryPolicy(
                    target_profile=min_profile,
                    max_profile=min_profile,
                    reason="battery_soc_missing",
                )

            if soc_pct < miner.get_battery_discharge_soc_min():
                return MinerBatteryPolicy(
                    target_profile=min_profile,
                    max_profile=min_profile,
                    reason="battery_discharge_soc_below_min",
                )

            configured_profile = max_profile(
                miner.get_battery_discharge_profile(),
                min_profile,
            )
            return MinerBatteryPolicy(
                target_profile=configured_profile,
                max_profile=configured_profile,
                reason="battery_discharge_target",
            )

        if mode == "charging":
            if not miner.use_battery_when_charging():
                return unrestricted

            if soc_pct is None or soc_pct < miner.get_battery_charge_soc_min():
                return unrestricted

            configured_profile = max_profile(
                miner.get_battery_charge_profile(),
                min_profile,
            )
            return MinerBatteryPolicy(
                target_profile=configured_profile,
                max_profile=configured_profile,
                reason="battery_charge_target",
            )

        return unrestricted

    def _can_force_battery_targets(
        self,
        *,
        battery_context: BatteryContext,
        current_profiles: list[str],
        target_profiles: list[str],
        miners: list,
        grid_power_w: float,
    ) -> bool:
        total_delta_power_w = 0.0
        for idx, target_profile in enumerate(target_profiles):
            current_power_w = miners[idx].get_profile_power_w(current_profiles[idx])
            target_power_w = miners[idx].get_profile_power_w(target_profile)
            total_delta_power_w += max(0.0, target_power_w - current_power_w)

        if total_delta_power_w <= 0:
            return False

        if battery_context.mode == "discharging":
            return grid_power_w <= (self.max_import_w + self.switch_hysteresis_w)

        if battery_context.mode == "charging":
            return total_delta_power_w <= battery_context.available_charge_surplus_w

        return False

    def _build_battery_summary(
        self,
        *,
        battery_context: BatteryContext,
        fallback: str,
    ) -> str:
        if not battery_context.active:
            return fallback

        if battery_context.mode == "charging":
            return (
                f"{fallback}, battery=charging, soc={self._format_soc(battery_context.soc_pct)}"
            )

        if battery_context.mode == "discharging":
            return (
                f"{fallback}, battery=discharging, soc={self._format_soc(battery_context.soc_pct)}"
            )

        return fallback

    @staticmethod
    def _format_soc(soc_pct: float | None) -> str:
        if soc_pct is None:
            return "?"
        return f"{soc_pct:.1f}%"

    def _should_step_down(
        self,
        *,
        grid_power_w: float,
        now_monotonic: float,
        current_profiles: list[str],
    ) -> bool:
        if not current_profiles or all(profile == "off" for profile in current_profiles):
            self._reset_import_tracking()
            return False

        reset_threshold = self.max_import_w - self.switch_hysteresis_w

        if grid_power_w <= reset_threshold:
            if self.state.import_exceeded_since_monotonic is not None:
                logger.info(
                    "Import condition cleared: grid_power_w=%.1f reset_threshold=%.1f",
                    grid_power_w,
                    reset_threshold,
                )
            self._reset_import_tracking()
            return False

        if grid_power_w <= self.max_import_w:
            return False

        if self.state.import_exceeded_since_monotonic is None:
            self.state.import_exceeded_since_monotonic = now_monotonic
            self._log_import_once(
                "start",
                "Import threshold exceeded: grid_power_w=%.1f max_import_w=%.1f",
                grid_power_w,
                self.max_import_w,
            )
            return False

        elapsed = now_monotonic - self.state.import_exceeded_since_monotonic
        if elapsed >= self.import_hold_seconds:
            self._log_import_once(
                "step_down",
                (
                    "Import hold exceeded: grid_power_w=%.1f max_import_w=%.1f "
                    "elapsed=%.1fs -> step down"
                ),
                grid_power_w,
                self.max_import_w,
                elapsed,
            )
            return True

        self._log_import_once(
            "holding",
            (
                "Import detected: grid_power_w=%.1f max_import_w=%.1f "
                "elapsed=%.1fs hold=%.1fs"
            ),
            grid_power_w,
            self.max_import_w,
            elapsed,
            self.import_hold_seconds,
        )
        return False

    def _decide_degraded(
        self,
        *,
        quality: str,
        miners: list,
    ) -> ControlDecision:
        now_mono = monotonic()
        miner_count = len(miners)
        current_profiles = get_current_profiles(miners)

        if self.state.degraded_quality != quality:
            logger.warning(
                "Source quality changed: %s -> %s",
                self.state.degraded_quality or "live",
                quality,
            )
            self.state.degraded_quality = quality
            self.state.degraded_since_monotonic = now_mono
            self.state.last_fallback_log_key = None
            self._reset_import_tracking()

        behavior = self._get_source_loss_behavior(quality)
        mode = str(behavior.get("mode", "off_all")).strip().lower()
        fallback_profile = str(
            behavior.get("fallback_profile", "p1")
        ).strip().lower()

        if fallback_profile not in PROFILE_ORDER:
            fallback_profile = "p1"

        hold_seconds_raw = behavior.get("hold_seconds", 0)
        hold_seconds = float(hold_seconds_raw or 0)

        def hold_expired() -> bool:
            if hold_seconds <= 0:
                return False
            if self.state.degraded_since_monotonic is None:
                return False
            return (now_mono - self.state.degraded_since_monotonic) >= hold_seconds

        def remaining_seconds() -> float:
            if hold_seconds <= 0 or self.state.degraded_since_monotonic is None:
                return 0.0
            return max(
                0.0,
                hold_seconds - (now_mono - self.state.degraded_since_monotonic),
            )

        if mode == "off_all":
            self._log_fallback_once(
                f"{quality}:off_all",
                "Fallback active: quality=%s mode=off_all",
                quality,
            )
            return ControlDecision(
                profiles=["off"] * miner_count,
                action="fallback_off",
                summary=f"fallback_off ({quality})",
            )

        if mode == "hold_current":
            if hold_expired():
                self._log_fallback_once(
                    f"{quality}:hold_current:expired",
                    (
                        "Fallback expired: quality=%s mode=hold_current "
                        "hold_seconds=%.1f -> off_all"
                    ),
                    quality,
                    hold_seconds,
                )
                return ControlDecision(
                    profiles=["off"] * miner_count,
                    action="fallback_off",
                    summary=f"fallback_off ({quality}, expired)",
                )

            if hold_seconds > 0:
                self._log_fallback_once(
                    f"{quality}:hold_current:timed",
                    "Fallback active: quality=%s mode=hold_current remaining=%.1fs",
                    quality,
                    remaining_seconds(),
                )
            else:
                self._log_fallback_once(
                    f"{quality}:hold_current:infinite",
                    "Fallback active: quality=%s mode=hold_current",
                    quality,
                )
            return ControlDecision(
                profiles=current_profiles,
                action="fallback_hold",
                summary=f"fallback_hold ({quality})",
            )

        if mode == "force_profile":
            if hold_expired():
                self._log_fallback_once(
                    f"{quality}:force_profile:expired",
                    (
                        "Fallback expired: quality=%s mode=force_profile "
                        "profile=%s hold_seconds=%.1f -> off_all"
                    ),
                    quality,
                    fallback_profile,
                    hold_seconds,
                )
                return ControlDecision(
                    profiles=["off"] * miner_count,
                    action="fallback_off",
                    summary=f"fallback_off ({quality}, expired)",
                )

            if hold_seconds > 0:
                self._log_fallback_once(
                    f"{quality}:force_profile:timed:{fallback_profile}",
                    (
                        "Fallback active: quality=%s mode=force_profile profile=%s "
                        "remaining=%.1fs"
                    ),
                    quality,
                    fallback_profile,
                    remaining_seconds(),
                )
            else:
                self._log_fallback_once(
                    f"{quality}:force_profile:infinite:{fallback_profile}",
                    "Fallback active: quality=%s mode=force_profile profile=%s",
                    quality,
                    fallback_profile,
                )
            return ControlDecision(
                profiles=[fallback_profile] * miner_count,
                action="fallback_profile",
                summary=f"fallback_profile ({quality}, {fallback_profile})",
            )

        self._log_fallback_once(
            f"{quality}:unknown:{mode}",
            "Unknown fallback mode: quality=%s mode=%s -> off_all",
            quality,
            mode,
        )
        return ControlDecision(
            profiles=["off"] * miner_count,
            action="fallback_off",
            summary=f"fallback_off ({quality}, unknown={mode})",
        )

    def _get_source_loss_behavior(self, quality: str) -> dict:
        return self.source_loss.get(quality, {}) or {}

    @staticmethod
    def _normalize_quality(value: str | None) -> str:
        normalized = (value or "live").strip().lower()
        if normalized in {"live", "stale", "offline"}:
            return normalized
        return "live"

    def _reset_import_tracking(self) -> None:
        self.state.import_exceeded_since_monotonic = None
        self.state.last_import_log_key = None

    def _log_import_once(self, key: str, message: str, *args) -> None:
        if self.state.last_import_log_key == key:
            return
        logger.info(message, *args)
        self.state.last_import_log_key = key

    def _log_live_hold_once(self, key: str, message: str, *args) -> None:
        if self.state.last_live_hold_log_key == key:
            return
        logger.info(message, *args)
        self.state.last_live_hold_log_key = key

    def _log_fallback_once(self, key: str, message: str, *args) -> None:
        if self.state.last_fallback_log_key == key:
            return
        logger.warning(message, *args)
        self.state.last_fallback_log_key = key

    def _log_battery_once(self, key: str, message: str, *args) -> None:
        if self.state.last_battery_log_key == key:
            return
        logger.info(message, *args)
        self.state.last_battery_log_key = key
