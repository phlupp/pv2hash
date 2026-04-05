from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class MinerProfile:
    power_w: float


@dataclass
class MinerProfiles:
    p1: MinerProfile
    p2: MinerProfile
    p3: MinerProfile
    p4: MinerProfile


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

    min_regulated_profile: str = "off"

    reachable: bool = False
    runtime_state: str = "unknown"

    api_version: str | None = None
    control_mode: str | None = None
    autotuning_enabled: bool | None = None

    power_target_min_w: float | None = None
    power_target_default_w: float | None = None
    power_target_max_w: float | None = None

    last_error: str | None = None
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))

    def has_power_constraints(self) -> bool:
        return (
            self.power_target_min_w is not None
            and self.power_target_default_w is not None
            and self.power_target_max_w is not None
        )

    def is_paused_like(self) -> bool:
        return self.runtime_state in {"paused", "stopped"}