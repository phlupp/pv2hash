from datetime import UTC, datetime

from pv2hash.miners.base import MinerAdapter
from pv2hash.models.miner import MinerInfo, MinerProfile, MinerProfiles


class SimulatorMiner(MinerAdapter):
    def __init__(
        self,
        miner_id: str,
        name: str,
        host: str,
        priority: int = 100,
        enabled: bool = True,
        serial_number: str | None = None,
        model: str | None = None,
        firmware_version: str | None = None,
        profiles: dict | None = None,
        min_regulated_profile: str = "off",
    ) -> None:
        profile_cfg = profiles or {
            "p1": {"power_w": 900},
            "p2": {"power_w": 1800},
            "p3": {"power_w": 3000},
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
            driver="simulator",
            enabled=enabled,
            is_active=True,
            priority=priority,
            serial_number=serial_number or f"SIM-{miner_id.upper()}",
            model=model or "Simulator",
            firmware_version=firmware_version or "sim-0.1",
            profile="off",
            power_w=0.0,
            profiles=miner_profiles,
            min_regulated_profile=normalized_min_regulated_profile,
            reachable=True,
            runtime_state="paused",
            control_mode="power_target",
            autotuning_enabled=True,
            power_target_min_w=float(profile_cfg["p1"]["power_w"]),
            power_target_default_w=float(profile_cfg["p2"]["power_w"]),
            power_target_max_w=float(profile_cfg["p4"]["power_w"]),
        )

    async def set_profile(self, profile: str) -> None:
        self.info.profile = profile

        if profile == "off":
            self.info.power_w = 0.0
            self.info.runtime_state = "paused"
        else:
            desired_w = self.get_profile_power_w(profile)
            if desired_w <= 0:
                self.info.power_w = 0.0
                self.info.runtime_state = "paused"
            else:
                self.info.power_w = desired_w
                self.info.runtime_state = "running"

        self.info.last_error = None
        self.info.last_seen = datetime.now(UTC)

    async def get_status(self) -> MinerInfo:
        self.info.is_active = True
        self.info.reachable = True
        self.info.last_seen = datetime.now(UTC)
        return self.info