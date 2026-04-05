from abc import ABC, abstractmethod

from pv2hash.models.miner import MinerInfo

PROFILE_ORDER = ("off", "p1", "p2", "p3", "p4")


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

    def get_min_regulated_profile(self) -> str:
        profile = getattr(self.info, "min_regulated_profile", "off")
        if profile in PROFILE_ORDER:
            return profile
        return "off"

    def allows_regulated_off(self) -> bool:
        return self.get_min_regulated_profile() == "off"

    def is_active_for_distribution(self) -> bool:
        return bool(self.info.enabled and self.info.is_active)