from dataclasses import dataclass, field
from datetime import datetime, UTC


@dataclass
class MinerInfo:
    id: str
    name: str
    host: str
    driver: str
    enabled: bool = True
    priority: int = 100
    serial_number: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    profile: str = "off"
    power_w: float = 0.0
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
