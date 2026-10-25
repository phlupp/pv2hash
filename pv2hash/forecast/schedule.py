"""Fixed UTC 15-minute forecast cadence for comparable issued-forecast quality.

Successful automatically issued forecasts are timed to xx:10/:25/:40/:55 UTC,
five minutes before the next quarter-hour target boundary. The actual issue
instant (not the nominal slot) is archived for all subsequent comparisons.
"""
from __future__ import annotations

from datetime import UTC, datetime

FORECAST_REFRESH_MINUTES = 15
PERIOD_SECONDS = FORECAST_REFRESH_MINUTES * 60
PHASE_SECONDS = 10 * 60
ISSUE_WINDOW_SECONDS = 4 * 60


def due_in_forecast_slot(*, now: datetime, last_success: str | None) -> bool:
    """Allow one scheduled request per UTC slot, unaffected by process restart.

    No attempt is backdated. Last successful issuance is persisted already;
    a failed request may retry within the same slot. Other forecast settings
    and legacy stored refresh_minutes cannot influence the schedule.
    """
    if now.tzinfo is None:
        raise ValueError("Current time must be timezone-aware")
    now_utc = now.astimezone(UTC)
    slot_epoch = ((int(now_utc.timestamp()) - PHASE_SECONDS)
                  // PERIOD_SECONDS) * PERIOD_SECONDS + PHASE_SECONDS
    slot = datetime.fromtimestamp(slot_epoch, UTC)
    elapsed = (now_utc - slot).total_seconds()
    if not 0 <= elapsed < ISSUE_WINDOW_SECONDS:
        return False
    if last_success:
        last = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
        if last.tzinfo is None:
            raise ValueError("Last forecast timestamp must be timezone-aware")
        if last.astimezone(UTC) >= slot:
            return False
    return True
