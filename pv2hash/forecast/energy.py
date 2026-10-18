"""Convert adjacent, valid power samples into energy without filling gaps.

Forecast data are instantaneous hourly W values; live plant data are 10-second
samples. Never bridge gaps or convert missing/partial production to zero.
"""
from __future__ import annotations

from datetime import datetime
import math


def energy_summary(points: list[tuple[datetime, float]], *, start: datetime,
                   end: datetime, max_gap_seconds: float) -> dict:
    """Trapezoidal Wh integration of *only* adjacent trustworthy intervals.

    Points at start/end boundaries are allowed; no implicit extrapolation.
    Returns null if no complete interval exists (rather than false 0 kWh).
    """
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("Energy integration requires aware, ordered day boundaries")
    day_seconds = (end - start).total_seconds()
    total_wh = 0.0
    covered_seconds = 0.0
    previous: tuple[datetime, float] | None = None
    for ts, power in sorted(points, key=lambda item: item[0]):
        if ts < start or ts > end or not math.isfinite(power):
            previous = None
            continue
        if previous is not None:
            delta = (ts - previous[0]).total_seconds()
            if 0 < delta <= max_gap_seconds:
                # PV generation cannot be negative. Small negative inverter
                # rounding/noise is legitimately measured as zero production.
                total_wh += (max(0.0, previous[1]) + max(0.0, power)) / 2 * delta / 3600
                covered_seconds += delta
        previous = (ts, power)
    return {
        "kwh": round(total_wh / 1000, 6) if covered_seconds else None,
        "coverage_hours": round(covered_seconds / 3600, 6),
        "day_hours": round(day_seconds / 3600, 3),
        "complete": covered_seconds >= day_seconds - max_gap_seconds / 2,
    }
