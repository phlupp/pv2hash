from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from pv2hash.logging_ext.setup import get_logger
from pv2hash.models.energy import EnergySnapshot


logger = get_logger("pv2hash.controller.basic")


@dataclass
class ControllerState:
    current_live_profile: str | None = None
    live_profile_since_monotonic: float | None = None

    degraded_quality: str | None = None
    degraded_since_monotonic: float | None = None

    last_fallback_log_key: str | None = None
    last_live_hold_log_key: str | None = None


class BasicController:
    def __init__(self, control_config: dict) -> None:
        thresholds = control_config.get("coarse_thresholds", {})
        self.eco_threshold = float(thresholds.get("eco", -500))
        self.mid_threshold = float(thresholds.get("mid", -1500))
        self.high_threshold = float(thresholds.get("high", -2500))

        self.min_switch_interval_seconds = float(
            control_config.get("min_switch_interval_seconds", 0)
        )
        self.switch_hysteresis_w = float(
            control_config.get("switch_hysteresis_w", 0)
        )

        self.source_loss = control_config.get("source_loss", {})
        self.state = ControllerState()

    def decide(self, snapshot: EnergySnapshot) -> str:
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

            return self._decide_live(snapshot.grid_power_w)

        return self._decide_degraded(quality)

    def _decide_live(self, grid_power_w: float) -> str:
        now_mono = monotonic()
        current = self.state.current_live_profile

        candidate = self._choose_live_profile(
            grid_power_w=grid_power_w,
            current_profile=current,
        )

        if current is None:
            self.state.current_live_profile = candidate
            self.state.live_profile_since_monotonic = now_mono
            self.state.last_live_hold_log_key = None
            logger.info(
                "Initial live decision: profile=%s grid_power_w=%.1f",
                candidate,
                grid_power_w,
            )
            return candidate

        if candidate == current:
            self.state.last_live_hold_log_key = None
            return current

        elapsed = (
            now_mono - self.state.live_profile_since_monotonic
            if self.state.live_profile_since_monotonic is not None
            else 999999.0
        )

        if (
            self.min_switch_interval_seconds > 0
            and elapsed < self.min_switch_interval_seconds
        ):
            self._log_live_hold_once(
                f"{current}->{candidate}",
                (
                    "Live switch suppressed: current=%s candidate=%s "
                    "grid_power_w=%.1f elapsed=%.1fs min_switch_interval=%.1fs"
                ),
                current,
                candidate,
                grid_power_w,
                elapsed,
                self.min_switch_interval_seconds,
            )
            return current

        logger.info(
            "Live profile switch: %s -> %s (grid_power_w=%.1f)",
            current,
            candidate,
            grid_power_w,
        )

        self.state.current_live_profile = candidate
        self.state.live_profile_since_monotonic = now_mono
        self.state.last_live_hold_log_key = None

        return candidate

    def _choose_live_profile(
        self,
        *,
        grid_power_w: float,
        current_profile: str | None,
    ) -> str:
        # Beim allerersten Live-Wert keine zusätzliche Hysterese,
        # damit das System normal anlaufen kann.
        if current_profile is None:
            return self._decide_without_hysteresis(grid_power_w)

        h = self.switch_hysteresis_w

        if current_profile == "off":
            if grid_power_w < self.high_threshold - h:
                return "high"
            if grid_power_w < self.mid_threshold - h:
                return "mid"
            if grid_power_w < self.eco_threshold - h:
                return "eco"
            return "off"

        if current_profile == "eco":
            if grid_power_w < self.high_threshold - h:
                return "high"
            if grid_power_w < self.mid_threshold - h:
                return "mid"
            if grid_power_w > self.eco_threshold + h:
                return "off"
            return "eco"

        if current_profile == "mid":
            if grid_power_w < self.high_threshold - h:
                return "high"
            if grid_power_w > self.mid_threshold + h:
                if grid_power_w > self.eco_threshold + h:
                    return "off"
                return "eco"
            return "mid"

        if current_profile == "high":
            if grid_power_w > self.high_threshold + h:
                if grid_power_w > self.mid_threshold + h:
                    if grid_power_w > self.eco_threshold + h:
                        return "off"
                    return "eco"
                return "mid"
            return "high"

        logger.warning(
            "Unknown current live profile=%r -> falling back to plain coarse logic",
            current_profile,
        )
        return self._decide_without_hysteresis(grid_power_w)

    def _decide_without_hysteresis(self, grid_power_w: float) -> str:
        if grid_power_w < self.high_threshold:
            return "high"
        if grid_power_w < self.mid_threshold:
            return "mid"
        if grid_power_w < self.eco_threshold:
            return "eco"
        return "off"

    def _decide_degraded(self, quality: str) -> str:
        now_mono = monotonic()

        if self.state.degraded_quality != quality:
            logger.warning(
                "Source quality changed: %s -> %s",
                self.state.degraded_quality or "live",
                quality,
            )
            self.state.degraded_quality = quality
            self.state.degraded_since_monotonic = now_mono
            self.state.last_fallback_log_key = None

        behavior = self._get_source_loss_behavior(quality)
        mode = str(behavior.get("mode", "off")).strip().lower()

        hold_seconds_raw = behavior.get("hold_seconds")
        hold_seconds: float | None
        if hold_seconds_raw in (None, "", False):
            hold_seconds = None
        else:
            hold_seconds = float(hold_seconds_raw)

        if mode == "off":
            self._log_fallback_once(
                f"{quality}:off",
                "Fallback active: quality=%s mode=off -> off",
                quality,
            )
            return "off"

        if mode == "hold_last":
            if self.state.current_live_profile is None:
                self._log_fallback_once(
                    f"{quality}:hold_last:no_cached",
                    "Fallback active: quality=%s mode=hold_last but no cached live profile -> off",
                    quality,
                )
                return "off"

            if (
                hold_seconds is not None
                and hold_seconds > 0
                and self.state.degraded_since_monotonic is not None
            ):
                elapsed = now_mono - self.state.degraded_since_monotonic
                if elapsed > hold_seconds:
                    self._log_fallback_once(
                        f"{quality}:hold_last:expired",
                        "Fallback expired: quality=%s mode=hold_last hold_seconds=%.1f elapsed=%.1f -> off",
                        quality,
                        hold_seconds,
                        elapsed,
                    )
                    return "off"

                self._log_fallback_once(
                    f"{quality}:hold_last:{self.state.current_live_profile}:timed",
                    "Fallback active: quality=%s mode=hold_last profile=%s remaining=%.1fs",
                    quality,
                    self.state.current_live_profile,
                    max(0.0, hold_seconds - elapsed),
                )
                return self.state.current_live_profile

            self._log_fallback_once(
                f"{quality}:hold_last:{self.state.current_live_profile}:infinite",
                "Fallback active: quality=%s mode=hold_last profile=%s",
                quality,
                self.state.current_live_profile,
            )
            return self.state.current_live_profile

        self._log_fallback_once(
            f"{quality}:unsupported:{mode}",
            "Fallback active: unsupported mode=%s for quality=%s -> off",
            mode,
            quality,
        )
        return "off"

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