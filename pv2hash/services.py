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
        self._retired_miners = []

    def reload_from_config(self) -> None:
        config = load_config()
        self.state.config = config

        old_miners = list(self.miners)
        old_by_id = {getattr(miner.info, "id", None): miner for miner in old_miners}

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
        self._carry_runtime_state(old_by_id)
        self._retired_miners = [
            miner
            for miner_id, miner in old_by_id.items()
            if miner_id is not None and miner_id not in {m.info.id for m in self.miners}
        ]
        self.controller = BasicController(config["control"], config.get("battery", {}))
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

    def _carry_runtime_state(self, old_by_id: dict) -> None:
        for miner in self.miners:
            old = old_by_id.get(getattr(miner.info, "id", None))
            if old is None:
                continue

            miner.info.profile = old.info.profile
            miner.info.power_w = old.info.power_w
            miner.info.reachable = old.info.reachable
            miner.info.runtime_state = old.info.runtime_state
            miner.info.last_error = old.info.last_error
            miner.info.last_seen = old.info.last_seen
            miner.info.api_version = old.info.api_version
            miner.info.control_mode = old.info.control_mode
            miner.info.autotuning_enabled = old.info.autotuning_enabled
            miner.info.power_target_min_w = old.info.power_target_min_w
            miner.info.power_target_default_w = old.info.power_target_default_w
            miner.info.power_target_max_w = old.info.power_target_max_w

    def pop_retired_miners(self) -> list:
        retired = list(self._retired_miners)
        self._retired_miners = []
        return retired

    def get_source_debug_info(self) -> dict:
        if self.source is None:
            return {}
        return getattr(self.source, "debug_info", {}) or {}

    def get_battery_source_debug_info(self) -> dict:
        if self.battery_source is None:
            return {}
        return getattr(self.battery_source, "debug_info", {}) or {}

    def get_source_gui_models(self) -> list[dict]:
        snapshot = self.state.snapshot
        models: list[dict] = []

        if self.source is not None:
            models.append(
                self.source.get_gui_model(
                    source_id="grid",
                    role=str(self.state.config.get("source", {}).get("role", "grid")),
                    title="Netz-Messung",
                    enabled=bool(self.state.config.get("source", {}).get("enabled", True)),
                    config=self.state.config.get("source", {}),
                    snapshot=snapshot,
                    debug_info=self.get_source_debug_info(),
                )
            )

        battery_cfg = self.state.config.get("battery", {}) or {}
        battery_enabled = bool(battery_cfg.get("enabled")) and battery_cfg.get("type", "none") != "none"
        if self.battery_source is not None:
            models.append(
                self.battery_source.get_gui_model(
                    source_id="battery",
                    role=str(battery_cfg.get("role", "battery")),
                    title="Batterie",
                    enabled=battery_enabled,
                    config=battery_cfg,
                    snapshot=snapshot,
                    debug_info=self.get_battery_source_debug_info(),
                )
            )
        else:
            models.append(
                {
                    "id": "battery",
                    "role": str(battery_cfg.get("role", "battery")),
                    "title": "Batterie",
                    "enabled": False,
                    "driver": str(battery_cfg.get("type", "none") or "none"),
                    "driver_label": str(battery_cfg.get("name", "Keine Batterie") or "Keine Batterie"),
                    "status": {
                        "state": "disabled",
                        "text": "Deaktiviert",
                        "age_seconds": None,
                        "updated_at": None,
                    },
                    "config_fields": [],
                    "detail_groups": [],
                    "capabilities": {},
                }
            )

        return models
