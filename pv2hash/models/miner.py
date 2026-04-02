from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class MinerProfile:
    power_w: float


@dataclass
class MinerProfiles:
    off: MinerProfile
    eco: MinerProfile
    mid: MinerProfile
    high: MinerProfile


@dataclass
class MinerInfo:
    id: str
    name: str
    host: str
    driver: str

    enabled: bool = True
    is_active: bool = True
    priority: int = 100

    serial_number: str | None = None
    model: str | None = None
    firmware_version: str | None = None

    profile: str = "off"
    power_w: float = 0.0
    profiles: MinerProfiles | None = None

    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))