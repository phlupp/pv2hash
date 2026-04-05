from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class EnergySnapshot:
    grid_power_w: float
    pv_power_w: float | None = None
    house_power_w: float | None = None

    battery_charge_power_w: float | None = None
    battery_discharge_power_w: float | None = None
    battery_soc_pct: float | None = None
    battery_is_charging: bool | None = None
    battery_is_discharging: bool | None = None
    battery_is_active: bool | None = None

    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str = "unknown"
    quality: str | None = None
