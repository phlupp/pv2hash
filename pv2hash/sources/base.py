from abc import ABC, abstractmethod
from pv2hash.models.energy import EnergySnapshot


class EnergySource(ABC):
    @abstractmethod
    async def read(self) -> EnergySnapshot:
        raise NotImplementedError
