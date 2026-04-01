from dataclasses import dataclass, field


@dataclass
class SourceConfig:
    type: str
    name: str
    enabled: bool = True
    settings: dict = field(default_factory=dict)


@dataclass
class MinerConfig:
    id: str
    name: str
    host: str
    driver: str
    enabled: bool = True
    priority: int = 100
    serial_number: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    settings: dict = field(default_factory=dict)
