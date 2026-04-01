import random
from datetime import datetime, UTC

from pv2hash.sources.base import EnergySource
from pv2hash.models.energy import EnergySnapshot


class SimulatorSource(EnergySource):
    def __init__(self) -> None:
        self._base = -1200.0

    async def read(self) -> EnergySnapshot:
        drift = random.uniform(-250.0, 250.0)
        noise = random.uniform(-120.0, 120.0)
        self._base += drift
        self._base = max(-3200.0, min(1800.0, self._base))
        grid = self._base + noise

        pv_power = max(0.0, 2200.0 + random.uniform(-500.0, 900.0))
        house_power = max(150.0, pv_power + grid)

        battery_charge = None
        battery_discharge = None
        battery_soc = None

        if grid < -400:
            battery_charge = max(0.0, min(1200.0, abs(grid) * 0.18))
            battery_soc = random.uniform(35.0, 92.0)
        elif grid > 300:
            battery_discharge = max(0.0, min(1200.0, grid * 0.15))
            battery_soc = random.uniform(25.0, 88.0)

        return EnergySnapshot(
            grid_power_w=grid,
            pv_power_w=pv_power,
            house_power_w=house_power,
            battery_charge_power_w=battery_charge,
            battery_discharge_power_w=battery_discharge,
            battery_soc_pct=battery_soc,
            updated_at=datetime.now(UTC),
            source="simulator",
            quality="simulated",
        )
