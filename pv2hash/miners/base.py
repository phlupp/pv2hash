from abc import ABC, abstractmethod

from pv2hash.models.miner import MinerInfo


class MinerAdapter(ABC):
    info: MinerInfo

    @abstractmethod
    async def set_profile(self, profile: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self) -> MinerInfo:
        raise NotImplementedError

    def get_current_profile(self) -> str:
        return self.info.profile

    def get_profile_power_w(self, profile: str) -> float:
        if self.info.profiles is None:
            return 0.0

        profile_obj = getattr(self.info.profiles, profile, None)
        if profile_obj is None:
            return 0.0

        return float(profile_obj.power_w)

    def is_active_for_distribution(self) -> bool:
        return bool(self.info.enabled and self.info.is_active)