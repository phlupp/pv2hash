from abc import ABC, abstractmethod
from pv2hash.models.miner import MinerInfo


class MinerAdapter(ABC):
    @abstractmethod
    async def set_profile(self, profile: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self) -> MinerInfo:
        raise NotImplementedError
