from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from pv2hash.controller.distribution import (
    PROFILE_ORDER,
    apply_profile_caps,
    get_current_profiles,
    get_step_down_plan,
    get_step_down_plan_to_profiles,
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
    reason_code: str | None = None
    flags: list[str] | None = None
    distribution_reason: str | None = None
    decision_context: dict | None = None
    debug_event_type: str | None = None
    debug_requested_profiles: list[str] | None = None


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
    step_down_floor_profile: str | None
    reason: str | None


@dataclass
class BatteryContext:
    mode: str | None
    soc_pct: float | None
    charge_power_w: float
    discharge_power_w: float
    active: bool
    available_charge_surplus_w: float
    charging_export_unlocked: bool
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
        max_profiles = self._get_effective_battery_max_profiles(
            battery_context=battery_context,
        )
        target_profiles = self._build_battery_target_profiles(
            current_profiles=current_profiles,
            battery_context=battery_context,
            miners=miners,
        )

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
        distribution_reason: str | None = None

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
                miner.is_active_for_distribution()
                and policy.target_profile is not None
                and is_profile_higher(policy.target_profile, current_profiles[idx])
                for idx, (miner, policy) in enumerate(zip(miners, battery_context.policies))
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
                if self._should_step_down_for_battery_charge_guard(
                    battery_context=battery_context,
                    current_profiles=current_profiles,
                ):
                    floor_profiles = [
                        policy.step_down_floor_profile or current_profiles[idx]
                        for idx, policy in enumerate(battery_context.policies)
                    ]
                    down_plan = get_step_down_plan_to_profiles(
                        distribution_mode,
                        miners,
                        floor_profiles,
                    )

                    if down_plan.changed:
                        candidate_profiles = down_plan.profiles
                        action = "battery_charge_step_down"
                        distribution_reason = down_plan.reason
                        summary = self._build_battery_summary(
                            battery_context=battery_context,
                            fallback=(
                                f"battery_charge_step_down ({distribution_mode}, "
                                f"release≈{down_plan.delta_power_w:.0f}W)"
                            ),
                        )
                elif self._should_step_down_for_battery_discharge(
                    battery_context=battery_context,
                    current_profiles=current_profiles,
                ):
                    floor_profiles = [
                        policy.step_down_floor_profile or current_profiles[idx]
                        for idx, policy in enumerate(battery_context.policies)
                    ]
                    down_plan = get_step_down_plan_to_profiles(
                        distribution_mode,
                        miners,
                        floor_profiles,
                    )

                    if down_plan.changed:
                        candidate_profiles = down_plan.profiles
                        action = "battery_step_down"
                        distribution_reason = down_plan.reason
                        summary = self._build_battery_summary(
                            battery_context=battery_context,
                            fallback=(
                                f"battery_step_down ({distribution_mode}, "
                                f"release≈{down_plan.delta_power_w:.0f}W)"
                            ),
                        )
                elif self._should_step_down(
                    grid_power_w=grid_power_w,
                    now_monotonic=now_mono,
                    current_profiles=current_profiles,
                ):
                    down_plan = get_step_down_plan(distribution_mode, miners)

                    if down_plan.changed:
                        candidate_profiles = down_plan.profiles
                        action = "step_down"
                        distribution_reason = down_plan.reason
                        summary = self._build_battery_summary(
                            battery_context=battery_context,
                            fallback=(
                                f"step_down ({distribution_mode}, "
                                f"release≈{down_plan.delta_power_w:.0f}W)"
                            ),
                        )
                elif not self._should_hold_normal_step_up_for_battery_discharge(
                    battery_context=battery_context,
                ):
                    up_plan = get_step_up_plan(distribution_mode, miners)
                    required_export_w = up_plan.delta_power_w + self.switch_hysteresis_w

                    if (
                        up_plan.changed
                        and up_plan.delta_power_w > 0
                        and grid_power_w < -required_export_w
                    ):
                        candidate_profiles = up_plan.profiles
                        action = "step_up"
                        distribution_reason = up_plan.reason
                        summary = self._build_battery_summary(
                            battery_context=battery_context,
                            fallback=(
                                f"step_up ({distribution_mode}, "
                                f"need≈{up_plan.delta_power_w:.0f}W)"
                            ),
                        )
                    elif up_plan.changed and up_plan.delta_power_w > 0:
                        distribution_reason = up_plan.reason
                        summary = self._build_battery_summary(
                            battery_context=battery_context,
                            fallback=(
                                f"hold ({distribution_mode}, "
                                f"need≈{up_plan.delta_power_w:.0f}W, "
                                f"export≈{max(0.0, -grid_power_w):.0f}W)"
                            ),
                        )
                    else:
                        distribution_reason = up_plan.reason
                        summary = self._build_battery_summary(
                            battery_context=battery_context,
                            fallback=(
                                f"hold ({distribution_mode}, {up_plan.reason})"
                            ),
                        )
                else:
                    summary = self._build_battery_summary(
                        battery_context=battery_context,
                        fallback=f"hold ({distribution_mode})",
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
                distribution_reason=distribution_reason,
            )

        elapsed = (
            now_mono - self.state.live_profiles_since_monotonic
            if self.state.live_profiles_since_monotonic is not None
            else 999999.0
        )

        min_switch_interval_bypassed = False
        if (
            self.min_switch_interval_seconds > 0
            and elapsed < self.min_switch_interval_seconds
        ):
            if self._should_bypass_min_switch_interval(
                action=action,
                battery_context=battery_context,
            ):
                min_switch_interval_bypassed = True
                logger.info(
                    (
                        "Live switch bypasses min interval: current=%s candidate=%s "
                        "action=%s grid_power_w=%.1f elapsed=%.1fs min_switch_interval=%.1fs"
                    ),
                    ",".join(current_profiles),
                    ",".join(candidate_profiles),
                    action,
                    grid_power_w,
                    elapsed,
                    self.min_switch_interval_seconds,
                )
            else:
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
                    reason_code="min_switch_interval_blocked",
                    flags=self._build_debug_flags(
                        event_type="blocked_by_min_switch_interval",
                        action=action,
                        battery_context=battery_context,
                        current_profiles=current_profiles,
                        requested_profiles=candidate_profiles,
                        grid_power_w=grid_power_w,
                    ),
                    distribution_reason=distribution_reason,
                    debug_event_type="blocked_by_min_switch_interval",
                    debug_requested_profiles=candidate_profiles,
                    decision_context={
                        "schema_version": 1,
                        "event_type": "blocked_by_min_switch_interval",
                        "action": action,
                        "grid": {
                            "power_w": grid_power_w,
                            "max_import_w": self.max_import_w,
                            "switch_hysteresis_w": self.switch_hysteresis_w,
                            "import_hold_seconds": self.import_hold_seconds,
                            "min_switch_interval_seconds": self.min_switch_interval_seconds,
                            "min_switch_elapsed_seconds": elapsed,
                            "min_switch_remaining_seconds": max(0.0, self.min_switch_interval_seconds - elapsed),
                        },
                        "battery": {
                            "mode": battery_context.mode,
                            "soc_pct": battery_context.soc_pct,
                            "charge_power_w": battery_context.charge_power_w,
                            "discharge_power_w": battery_context.discharge_power_w,
                            "active": battery_context.active,
                            "charging_export_unlocked": battery_context.charging_export_unlocked,
                        },
                        "profiles": {
                            "current": current_profiles,
                            "requested": candidate_profiles,
                            "max_allowed": max_profiles,
                            "battery_targets": target_profiles,
                            "battery_step_down_floors": [
                                policy.step_down_floor_profile
                                for policy in battery_context.policies
                            ],
                            "battery_policy_reasons": [
                                policy.reason
                                for policy in battery_context.policies
                            ],
                        },
                    },
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

        if action in {
            "step_down",
            "battery_limit",
            "battery_step_down",
            "battery_charge_step_down",
        }:
            self._reset_import_tracking()
        else:
            self.state.last_import_log_key = None

        flags = self._build_decision_flags(
            action=action,
            battery_context=battery_context,
            current_profiles=current_profiles,
            candidate_profiles=candidate_profiles,
            grid_power_w=grid_power_w,
        )
        if min_switch_interval_bypassed:
            flags.append("min_switch_interval_bypassed")
        return ControlDecision(
            profiles=candidate_profiles,
            action=action,
            summary=summary,
            reason_code=self._reason_code_for_action(action),
            flags=flags,
            distribution_reason=distribution_reason,
            decision_context={
                "schema_version": 1,
                "action": action,
                "grid": {
                    "power_w": grid_power_w,
                    "max_import_w": self.max_import_w,
                    "switch_hysteresis_w": self.switch_hysteresis_w,
                    "import_hold_seconds": self.import_hold_seconds,
                    "min_switch_interval_seconds": self.min_switch_interval_seconds,
                    "min_switch_interval_bypassed": min_switch_interval_bypassed,
                },
                "battery": {
                    "mode": battery_context.mode,
                    "soc_pct": battery_context.soc_pct,
                    "charge_power_w": battery_context.charge_power_w,
                    "discharge_power_w": battery_context.discharge_power_w,
                    "active": battery_context.active,
                    "charging_export_unlocked": battery_context.charging_export_unlocked,
                },
                "profiles": {
                    "old": current_profiles,
                    "new": candidate_profiles,
                    "max_allowed": max_profiles,
                    "battery_targets": target_profiles,
                    "battery_step_down_floors": [
                        policy.step_down_floor_profile
                        for policy in battery_context.policies
                    ],
                    "battery_policy_reasons": [
                        policy.reason
                        for policy in battery_context.policies
                    ],
                },
            },
        )


    def _build_debug_flags(
        self,
        *,
        event_type: str,
        action: str,
        battery_context: BatteryContext,
        current_profiles: list[str],
        requested_profiles: list[str],
        grid_power_w: float,
    ) -> list[str]:
        flags: list[str] = ["debug", str(event_type or "controller_debug").strip().lower()]
        action = str(action or "").strip().lower()
        if action and action not in flags:
            flags.append(action)
        if requested_profiles != current_profiles:
            flags.append("profile_change_requested")
        if any(
            is_profile_higher(new, old)
            for old, new in zip(current_profiles, requested_profiles)
        ):
            flags.append("profile_step_up_requested")
        if any(
            is_profile_higher(old, new)
            for old, new in zip(current_profiles, requested_profiles)
        ):
            flags.append("profile_step_down_requested")
        if grid_power_w > self.max_import_w:
            flags.append("grid_import")
        elif grid_power_w < -self.switch_hysteresis_w:
            flags.append("grid_export")
        else:
            flags.append("grid_near_zero")
        if battery_context.mode == "charging":
            flags.append("battery_charging")
        elif battery_context.mode == "discharging":
            flags.append("battery_discharging")
        if battery_context.soc_pct is None:
            flags.append("battery_soc_missing")
        elif any(
            policy.reason in {
                "battery_discharge_soc_below_min",
                "battery_charge_soc_below_min",
            }
            for policy in battery_context.policies
        ):
            flags.append("battery_soc_low")
        else:
            flags.append("battery_soc_ok")
        for policy in battery_context.policies:
            if policy.reason:
                flag = str(policy.reason).strip().lower()
                if flag and flag not in flags:
                    flags.append(flag)
        return flags


    @staticmethod
    def _build_battery_target_profiles(
        *,
        current_profiles: list[str],
        battery_context: BatteryContext,
        miners: list,
    ) -> list[str]:
        """Return effective battery targets without lowering existing profiles.

        Battery target profiles are release targets, not a request to reset every
        miner exactly to that profile. They must only affect miners that are
        currently active for distribution. Otherwise an inactive miner could
        create a synthetic target change (for example off -> p1), start the
        controller's min-switch timer, and block real active miners even though
        no profile was actually applied. If one active miner is below its battery
        target while another active miner is already above its battery target,
        the target action must only raise the lower miner. Lowering remains
        handled by the dedicated battery limit / battery step-down paths.
        """
        targets: list[str] = []
        for idx, policy in enumerate(battery_context.policies):
            current_profile = current_profiles[idx]
            if idx >= len(miners) or not miners[idx].is_active_for_distribution():
                targets.append(current_profile)
                continue
            if policy.target_profile is None:
                targets.append(current_profile)
                continue
            targets.append(max_profile(current_profile, policy.target_profile))
        return targets


    @staticmethod
    def _should_bypass_min_switch_interval(
        *,
        action: str,
        battery_context: BatteryContext,
    ) -> bool:
        if str(action or "").strip().lower() != "battery_limit":
            return False

        hard_limit_reasons = {
            "battery_discharge_blocked",
            "battery_soc_missing",
            "battery_discharge_soc_below_min",
        }
        return any(
            policy.reason in hard_limit_reasons
            for policy in battery_context.policies
        )

    @staticmethod
    def _reason_code_for_action(action: str) -> str:
        mapping = {
            "step_up": "grid_export_step_up",
            "step_down": "grid_import_step_down",
            "battery_target": "battery_target_up",
            "battery_limit": "battery_limit_apply",
            "battery_step_down": "battery_discharge_step_down",
            "battery_charge_step_down": "battery_charge_soc_step_down",
            "fallback_off": "source_loss_fallback_off",
            "fallback_profile": "source_loss_fallback_profile",
            "fallback_hold": "source_loss_hold_current",
        }
        return mapping.get(str(action or "").strip().lower(), str(action or "unknown"))

    def _build_decision_flags(
        self,
        *,
        action: str,
        battery_context: BatteryContext,
        current_profiles: list[str],
        candidate_profiles: list[str],
        grid_power_w: float,
    ) -> list[str]:
        flags: list[str] = ["applied"]
        action = str(action or "").strip().lower()
        if action:
            flags.append(action)
        if candidate_profiles != current_profiles:
            flags.append("profile_change")
        if any(
            is_profile_higher(new, old)
            for old, new in zip(current_profiles, candidate_profiles)
        ):
            flags.append("profile_step_up")
        if any(
            is_profile_higher(old, new)
            for old, new in zip(current_profiles, candidate_profiles)
        ):
            flags.append("profile_step_down")
        if grid_power_w > self.max_import_w:
            flags.append("grid_import")
        elif grid_power_w < -self.switch_hysteresis_w:
            flags.append("grid_export")
        else:
            flags.append("grid_near_zero")
        if battery_context.mode == "charging":
            flags.append("battery_charging")
        elif battery_context.mode == "discharging":
            flags.append("battery_discharging")
        if battery_context.soc_pct is None:
            flags.append("battery_soc_missing")
        elif any(
            policy.reason in {
                "battery_discharge_soc_below_min",
                "battery_charge_soc_below_min",
            }
            for policy in battery_context.policies
        ):
            flags.append("battery_soc_low")
        else:
            flags.append("battery_soc_ok")
        for policy in battery_context.policies:
            if policy.reason:
                flag = str(policy.reason).strip().lower()
                if flag and flag not in flags:
                    flags.append(flag)
        return flags

    def _build_battery_context(
        self,
        *,
        snapshot: EnergySnapshot,
        miners: list,
    ) -> BatteryContext:
        charge_power_w = float(snapshot.battery_charge_power_w or 0.0)
        discharge_power_w = float(snapshot.battery_discharge_power_w or 0.0)

        is_charging = self._is_battery_flow_active(
            flag=snapshot.battery_is_charging,
            power_w=snapshot.battery_charge_power_w,
            threshold_w=self.battery_charge_active_threshold_w,
        )
        is_discharging = self._is_battery_flow_active(
            flag=snapshot.battery_is_discharging,
            power_w=snapshot.battery_discharge_power_w,
            threshold_w=self.battery_discharge_active_threshold_w,
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
        grid_export_w = max(0.0, -float(snapshot.grid_power_w))
        available_charge_surplus_w = grid_export_w + max(
            0.0,
            charge_power_w,
        ) + self.max_import_w
        charging_export_unlocked = (
            mode == "charging"
            and grid_export_w > self.switch_hysteresis_w
        )

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
            charging_export_unlocked=charging_export_unlocked,
            policies=policies,
        )


    @staticmethod
    def _is_battery_flow_active(
        *,
        flag: bool | None,
        power_w: float | None,
        threshold_w: float,
    ) -> bool:
        if power_w is not None:
            try:
                return float(power_w) >= threshold_w
            except (TypeError, ValueError):
                return bool(flag)

        if flag is None:
            return False

        return bool(flag)

    def _get_effective_battery_max_profiles(
        self,
        *,
        battery_context: BatteryContext,
    ) -> list[str]:
        if (
            battery_context.mode == "charging"
            and battery_context.charging_export_unlocked
        ):
            return [
                "p4" if policy.reason == "battery_charge_target" else policy.max_profile
                for policy in battery_context.policies
            ]

        return [policy.max_profile for policy in battery_context.policies]

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
            step_down_floor_profile=None,
            reason=None,
        )

        if mode == "discharging":
            if not miner.use_battery_when_discharging():
                return MinerBatteryPolicy(
                    target_profile=None,
                    max_profile=min_profile,
                    step_down_floor_profile=None,
                    reason="battery_discharge_blocked",
                )

            if soc_pct is None:
                return MinerBatteryPolicy(
                    target_profile=None,
                    max_profile=min_profile,
                    step_down_floor_profile=None,
                    reason="battery_soc_missing",
                )

            if soc_pct < miner.get_battery_discharge_soc_min():
                return MinerBatteryPolicy(
                    target_profile=None,
                    max_profile=min_profile,
                    step_down_floor_profile=None,
                    reason="battery_discharge_soc_below_min",
                )

            configured_profile = max_profile(
                miner.get_battery_discharge_profile(),
                min_profile,
            )
            return MinerBatteryPolicy(
                target_profile=configured_profile,
                max_profile="p4",
                step_down_floor_profile=configured_profile,
                reason="battery_discharge_target",
            )

        if mode == "charging":
            if not miner.use_battery_when_charging():
                return unrestricted

            if soc_pct is None:
                return MinerBatteryPolicy(
                    target_profile=None,
                    max_profile="p4",
                    step_down_floor_profile=min_profile,
                    reason="battery_charge_soc_missing",
                )

            if soc_pct < miner.get_battery_charge_soc_min():
                return MinerBatteryPolicy(
                    target_profile=None,
                    max_profile="p4",
                    step_down_floor_profile=min_profile,
                    reason="battery_charge_soc_below_min",
                )

            configured_profile = max_profile(
                miner.get_battery_charge_profile(),
                min_profile,
            )
            return MinerBatteryPolicy(
                target_profile=configured_profile,
                max_profile=configured_profile,
                step_down_floor_profile=None,
                reason="battery_charge_target",
            )

        return unrestricted

    def _should_step_down_for_battery_charge_guard(
        self,
        *,
        battery_context: BatteryContext,
        current_profiles: list[str],
    ) -> bool:
        if battery_context.mode != "charging":
            return False

        # Real grid export always remains allowed to control the miner.
        # If the PV surplus cannot be stored because the battery limits charging,
        # the normal grid-export step-up/hold logic may use that export even below
        # the battery charge SOC release threshold.
        if battery_context.charging_export_unlocked:
            return False

        guarded_reasons = {
            "battery_charge_soc_below_min",
            "battery_charge_soc_missing",
        }

        for idx, policy in enumerate(battery_context.policies):
            if policy.reason not in guarded_reasons:
                continue

            if policy.step_down_floor_profile is None:
                continue

            if is_profile_higher(current_profiles[idx], policy.step_down_floor_profile):
                return True

        return False

    def _should_step_down_for_battery_discharge(
        self,
        *,
        battery_context: BatteryContext,
        current_profiles: list[str],
    ) -> bool:
        if battery_context.mode != "discharging":
            return False

        for idx, policy in enumerate(battery_context.policies):
            if policy.step_down_floor_profile is None:
                continue

            if is_profile_higher(current_profiles[idx], policy.step_down_floor_profile):
                return True

        return False

    def _should_hold_normal_step_up_for_battery_discharge(
        self,
        *,
        battery_context: BatteryContext,
    ) -> bool:
        if battery_context.mode != "discharging":
            return False

        return any(
            policy.step_down_floor_profile is not None
            for policy in battery_context.policies
        )

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
            if idx >= len(miners) or not miners[idx].is_active_for_distribution():
                continue
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
            export_suffix = (
                ", grid-export-unlocked"
                if battery_context.charging_export_unlocked
                else ""
            )
            return (
                f"{fallback}, battery=charging, "
                f"soc={self._format_soc(battery_context.soc_pct)}"
                f"{export_suffix}"
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
