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

    def reload_from_config(self) -> None:
        config = load_config()
        self.state.config = config
        self.source = build_source(config)
        self.miners = build_miners(config)
        self.controller = BasicController(config["control"])
