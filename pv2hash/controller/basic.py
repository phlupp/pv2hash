from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from pv2hash.controller.distribution import (
    get_current_profiles,
    get_step_down_plan,
    get_step_up_plan,
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


class BasicController:
    def __init__(self, control_config: dict) -> None:
        self.min_switch_interval_seconds = float(
            control_config.get("min_switch_interval_seconds", 0)
        )
        self.switch_hysteresis_w = float(
            control_config.get("switch_hysteresis_w", 0)
        )
        self.max_import_w = float(
            control_config.get("max_import_w", 200)
        )
        self.import_hold_seconds = float(
            control_config.get("import_hold_seconds", 15)
        )

        self.source_loss = control_config.get("source_loss", {})
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
                grid_power_w=snapshot.grid_power_w,
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
        grid_power_w: float,
        miners: list,
        distribution_mode: str,
    ) -> ControlDecision:
        now_mono = monotonic()
        current_profiles = get_current_profiles(miners)

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
                summary=f"hold ({distribution_mode}, min-switch-interval)",
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

        if action == "step_down":
            self._reset_import_tracking()
        else:
            self.state.last_import_log_key = None

        return ControlDecision(
            profiles=candidate_profiles,
            action=action,
            summary=summary,
        )

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
            behavior.get("fallback_profile", "eco")
        ).strip().lower()

        if fallback_profile not in ("off", "eco", "mid", "high"):
            fallback_profile = "off"

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
                    "Fallback expired: quality=%s mode=hold_current hold_seconds=%.1f -> off_all",
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
                action="fallback_hold_current",
                summary=f"fallback_hold_current ({quality})",
            )

        if mode == "force_profile":
            forced_profiles = [
                fallback_profile if miner.is_active_for_distribution() else "off"
                for miner in miners
            ]

            if hold_expired():
                self._log_fallback_once(
                    f"{quality}:force_profile:{fallback_profile}:expired",
                    "Fallback expired: quality=%s mode=force_profile profile=%s hold_seconds=%.1f -> off_all",
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
                    f"{quality}:force_profile:{fallback_profile}:timed",
                    "Fallback active: quality=%s mode=force_profile profile=%s remaining=%.1fs",
                    quality,
                    fallback_profile,
                    remaining_seconds(),
                )
            else:
                self._log_fallback_once(
                    f"{quality}:force_profile:{fallback_profile}:infinite",
                    "Fallback active: quality=%s mode=force_profile profile=%s",
                    quality,
                    fallback_profile,
                )

            return ControlDecision(
                profiles=forced_profiles,
                action="fallback_force_profile",
                summary=f"fallback_force_profile ({quality}, {fallback_profile})",
            )

        self._log_fallback_once(
            f"{quality}:unsupported:{mode}",
            "Fallback active: unsupported mode=%s for quality=%s -> off_all",
            mode,
            quality,
        )
        return ControlDecision(
            profiles=["off"] * miner_count,
            action="fallback_off",
            summary=f"fallback_off ({quality}, unsupported)",
        )

    def _get_source_loss_behavior(self, quality: str) -> dict:
        behavior = self.source_loss.get(quality)
        if isinstance(behavior, dict):
            return behavior
        return {"mode": "off"}

    def _normalize_quality(self, quality: str | None) -> str:
        if quality in ("live", "simulated"):
            return "live"
        if quality == "stale":
            return "stale"
        if quality == "offline":
            return "offline"
        if quality in ("no_data", None):
            return "offline"

        logger.warning("Unknown snapshot quality=%r -> treating as offline", quality)
        return "offline"

    def _reset_import_tracking(self) -> None:
        self.state.import_exceeded_since_monotonic = None
        self.state.last_import_log_key = None

    def _log_fallback_once(
        self,
        key: str,
        msg: str,
        *args,
    ) -> None:
        if self.state.last_fallback_log_key == key:
            return

        self.state.last_fallback_log_key = key
        logger.warning(msg, *args)

    def _log_live_hold_once(
        self,
        key: str,
        msg: str,
        *args,
    ) -> None:
        if self.state.last_live_hold_log_key == key:
            return

        self.state.last_live_hold_log_key = key
        logger.info(msg, *args)

    def _log_import_once(
        self,
        key: str,
        msg: str,
        *args,
    ) -> None:
        if self.state.last_import_log_key == key:
            return

        self.state.last_import_log_key = key
        logger.info(msg, *args)