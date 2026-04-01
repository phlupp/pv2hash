class BasicController:
    def __init__(self, control_config: dict) -> None:
        thresholds = control_config.get("coarse_thresholds", {})
        self.eco_threshold = thresholds.get("eco", -500)
        self.mid_threshold = thresholds.get("mid", -1500)
        self.high_threshold = thresholds.get("high", -2500)

    def decide_profile(self, grid_power_w: float) -> str:
        if grid_power_w < self.high_threshold:
            return "high"
        if grid_power_w < self.mid_threshold:
            return "mid"
        if grid_power_w < self.eco_threshold:
            return "eco"
        return "off"
