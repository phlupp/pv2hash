from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pv2hash.models.energy import EnergySnapshot
from pv2hash.models.miner import MinerInfo


@dataclass
class UpdateCheckState:
    enabled: bool = True
    checking: bool = False
    status: str = "idle"
    repo: str | None = None
    local_version_full: str | None = None
    checked_at: datetime | None = None
    release_tag: str | None = None
    release_name: str | None = None
    release_url: str | None = None
    release_version: str | None = None
    release_build: str | None = None
    release_version_full: str | None = None
    release_published_at: datetime | None = None
    error: str | None = None


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
    update_check: UpdateCheckState = field(default_factory=UpdateCheckState)
