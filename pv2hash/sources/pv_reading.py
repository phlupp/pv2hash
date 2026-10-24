"""Driver-neutral result of one completed photovoltaic power observation.

A driver decides what a response means (including standby). The PV group
aggregates only these normalized results; it never knows manufacturer sentinels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal


PvQuality = Literal["live", "standby"]


@dataclass(frozen=True)
class PvReading:
    power_w: float
    quality: PvQuality = "live"
    register_values: list[dict] = field(default_factory=list)
    note: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.power_w, bool) or not math.isfinite(float(self.power_w)):
            raise ValueError("PV reading must have finite power")
        if self.quality not in ("live", "standby"):
            raise ValueError("Unsupported successful PV reading quality")
        if self.quality == "standby" and self.power_w != 0:
            raise ValueError("Standby PV readings must contribute exactly 0 W")
