"""Common UTC cadence for reproducible as-issued forecast comparisons.

Issue at xx:10/25/40/55 UTC for 15-minute refreshes, leaving 5 minutes
until each :15/:30/:45/:00 cutoff. UTC epochs are immune to DST restarts.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

BASE_SLOT_SECONDS = 15 * 60
PHASE_SECONDS = 10 * 60
ISSUE_WINDOW_SECONDS = 4 * 60


def effective_refresh_minutes(requested_minutes: int) -> int:
    """Round upward to a common 15-minute UTC grid, never poll *faster*."""
    if not isinstance(requested_minutes, int) or not 15 <= requested_minutes <= 360:
        raise ValueError("Forecast refresh must be between 15 and 360 minutes")
    return ((requested_minutes + 14) // 15) * 15


def due_in_forecast_slot(
    *, now: datetime, last_success: str | None, refresh_minutes: int,
) -> bool:
    """True only once per fixed UTC slot; do not backdate missed issue times.

    last_success comes from persisted forecast metadata. Manual refreshes
    in the current slot are respected; failures can retry within the slot.
    """
    if now.tzinfo is None:
        raise ValueError("Current time must be timezone-aware")
    now_utc = now.astimezone(UTC)
    period = effective_refresh_minutes(refresh_minutes) * 60
    slot_epoch = ((int(now_utc.timestamp()) - PHASE_SECONDS) // period) * period + PHASE_SECONDS
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
