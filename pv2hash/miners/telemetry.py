"""Shared interpretation of reported miner hashrate.

Hashrate from a device can be averaged or cached after mining has stopped.
Only a reachable miner in the confirmed 'running' state has a current
hashrate. Paused/stopped miners have known zero hashrate; unknown and
unreachable states must not be mistaken for a measured zero.
"""
from __future__ import annotations

import math


_STOPPED_STATES = frozenset({"paused", "stopped", "off", "idle", "socket_off"})


def effective_hashrate_ghs(
    reported_ghs: float | None,
    runtime_state: str | None,
    *,
    reachable: bool = True,
) -> float | None:
    """Return a current mining hashrate, not a cached device statistic."""
    if not reachable:
        return None

    state = str(runtime_state or "").strip().lower()
    if state in _STOPPED_STATES:
        return 0.0
    if state != "running" or reported_ghs is None:
        return None

    try:
        value = float(reported_ghs)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0.0 else None
