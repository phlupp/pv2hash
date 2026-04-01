from datetime import datetime, UTC

from pv2hash.miners.base import MinerAdapter
from pv2hash.models.miner import MinerInfo


PROFILE_POWER = {
    "off": 0,
    "eco": 900,
    "mid": 1800,
    "high": 3000,
}


class SimulatorMiner(MinerAdapter):
    def __init__(
        self,
        miner_id: str,
        name: str,
        host: str,
        priority: int = 100,
    ) -> None:
        self.info = MinerInfo(
            id=miner_id,
            name=name,
            host=host,
            driver="simulator",
            priority=priority,
            serial_number=f"SIM-{miner_id.upper()}",
            model="Simulator",
            firmware_version="sim-0.1",
            profile="off",
            power_w=0.0,
        )

    async def set_profile(self, profile: str) -> None:
        self.info.profile = profile
        self.info.power_w = float(PROFILE_POWER.get(profile, 0))
        self.info.last_seen = datetime.now(UTC)

    async def get_status(self) -> MinerInfo:
        self.info.last_seen = datetime.now(UTC)
        return self.info
