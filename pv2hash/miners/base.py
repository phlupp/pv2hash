from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pv2hash.models.miner import MinerInfo

PROFILE_ORDER = ("off", "p1", "p2", "p3", "p4")
BATTERY_PROFILE_ORDER = ("p1", "p2", "p3", "p4")


@dataclass(frozen=True)
class DriverFieldChoice:
    value: str
    label: str


@dataclass(frozen=True)
class DriverField:
    name: str
    label: str
    type: str
    required: bool = False
    preset: Any = None
    default: Any = None
    placeholder: str = ""
    help: str = ""
    create_phase: str = "full"
    advanced: bool = False
    choices: tuple[DriverFieldChoice, ...] = field(default_factory=tuple)


class MinerAdapter(ABC):
    info: MinerInfo
    DRIVER_LABEL: ClassVar[str] = "Unknown"

    @classmethod
    def get_driver_label(cls) -> str:
        return cls.DRIVER_LABEL

    @classmethod
    def get_config_schema(cls) -> list[DriverField]:
        return []

    @classmethod
    def get_device_settings_schema(cls) -> list[DriverField]:
        return []

    @classmethod
    def supports_gui_schema(cls) -> bool:
        return len(cls.get_config_schema()) > 0

    @classmethod
    def supports_device_settings(cls) -> bool:
        return len(cls.get_device_settings_schema()) > 0

    def apply_device_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "message": "not supported"}

    def get_details(self) -> dict:
        return {}

    @abstractmethod
    async def set_profile(self, profile: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self) -> MinerInfo:
        raise NotImplementedError

    def get_current_profile(self) -> str:
        return self.info.profile

    def get_profile_power_w(self, profile: str) -> float:
        if self.info.profiles is None:
            return 0.0

        profile_obj = getattr(self.info.profiles, profile, None)
        if profile_obj is None:
            return 0.0

        return float(profile_obj.power_w)

    def get_min_regulated_profile(self) -> str:
        profile = getattr(self.info, "min_regulated_profile", "off")
        if profile in PROFILE_ORDER:
            return "off" if profile == "off" else profile
        return "off"

    def use_battery_when_charging(self) -> bool:
        return bool(getattr(self.info, "use_battery_when_charging", False))

    def get_battery_charge_soc_min(self) -> float:
        try:
            return float(getattr(self.info, "battery_charge_soc_min", 95.0))
        except Exception:
            return 95.0

    def get_battery_charge_profile(self) -> str:
        profile = getattr(self.info, "battery_charge_profile", "p1")
        if profile in BATTERY_PROFILE_ORDER:
            return profile
        return "p1"

    def use_battery_when_discharging(self) -> bool:
        return bool(getattr(self.info, "use_battery_when_discharging", False))

    def get_battery_discharge_soc_min(self) -> float:
        try:
            return float(getattr(self.info, "battery_discharge_soc_min", 80.0))
        except Exception:
            return 80.0

    def get_battery_discharge_profile(self) -> str:
        profile = getattr(self.info, "battery_discharge_profile", "p1")
        if profile in BATTERY_PROFILE_ORDER:
            return profile
        return "p1"

    def allows_regulated_off(self) -> bool:
        return self.get_min_regulated_profile() == "off"

    def is_active_for_distribution(self) -> bool:
        return bool(self.info.enabled and self.info.is_active)
