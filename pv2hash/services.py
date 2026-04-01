from datetime import UTC, datetime

from pv2hash.config.store import load_config
from pv2hash.controller.basic import BasicController
from pv2hash.factory import build_miners, build_source
from pv2hash.runtime import AppState


class RuntimeServices:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.source = None
        self.miners = []
        self.controller = None
        self.last_error: str | None = None

    def reload_from_config(self) -> None:
        config = load_config()
        self.state.config = config
        self.source = build_source(config)
        self.miners = build_miners(config)
        self.controller = BasicController(config["control"])
        self.last_error = None
        now = datetime.now(UTC)
        self.state.last_reload_at = now
        self.state.source_reloaded_at = now

    def get_source_debug_info(self) -> dict:
        if self.source is None:
            return {}
        return getattr(self.source, "debug_info", {}) or {}