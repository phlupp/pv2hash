from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pv2hash.models.energy import EnergySnapshot
from pv2hash.models.miner import MinerInfo


@dataclass
class AppState:
    config: dict[str, Any]
    snapshot: EnergySnapshot | None = None
    miners: list[MinerInfo] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_decision: str | None = None
    last_reload_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_live_packet_at: datetime | None = None
    source_reloaded_at: datetime = field(default_factory=lambda: datetime.now(UTC))
