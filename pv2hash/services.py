from datetime import UTC, datetime

from pv2hash.config.store import load_config
from pv2hash.controller.basic import BasicController
from pv2hash.factory import build_battery_source, build_miners, build_source
from pv2hash.logging_ext.setup import get_logger
from pv2hash.runtime import AppState


logger = get_logger("pv2hash.runtime")


class RuntimeServices:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.source = None
        self.battery_source = None
        self.miners = []
        self.controller = None
        self.last_error: str | None = None
        self.reload_generation = 0

    def reload_from_config(self) -> None:
        config = load_config()
        self.state.config = config

        logger.info("Reloading runtime from config")
        logger.info(
            "Source type=%s, battery_type=%s, distribution=%s, policy=%s",
            config["source"].get("type"),
            config.get("battery", {}).get("type"),
            config["control"].get("distribution_mode"),
            config["control"].get("policy_mode"),
        )

        self.source = build_source(config)
        self.battery_source = build_battery_source(config)
        self.miners = build_miners(config)
        self.controller = BasicController(config["control"])
        self.last_error = None
        self.reload_generation += 1

        now = datetime.now(UTC)
        self.state.last_reload_at = now
        self.state.source_reloaded_at = now

        logger.info(
            "Runtime reload finished, miners=%d generation=%d source=%s battery_source=%s",
            len(self.miners),
            self.reload_generation,
            config["source"].get("type"),
            config.get("battery", {}).get("type") if config.get("battery", {}).get("enabled") else "disabled",
        )

    def get_source_debug_info(self) -> dict:
        if self.source is None:
            return {}
        return getattr(self.source, "debug_info", {}) or {}

    def get_battery_source_debug_info(self) -> dict:
        if self.battery_source is None:
            return {}
        return getattr(self.battery_source, "debug_info", {}) or {}
