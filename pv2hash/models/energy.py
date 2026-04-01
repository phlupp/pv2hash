from dataclasses import dataclass, field
from datetime import datetime, UTC


@dataclass
class EnergySnapshot:
    grid_power_w: float
    pv_power_w: float | None = None
    house_power_w: float | None = None

    battery_charge_power_w: float | None = None
    battery_discharge_power_w: float | None = None
    battery_soc_pct: float | None = None

    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str = "unknown"
    quality: str | None = None
