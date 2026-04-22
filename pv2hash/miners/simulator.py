from datetime import UTC, datetime

from pv2hash.miners.base import DriverField, DriverFieldChoice, MinerAdapter
from pv2hash.models.miner import MinerInfo, MinerProfile, MinerProfiles


class SimulatorMiner(MinerAdapter):
    DRIVER_LABEL = "Simulator"

    @classmethod
    def get_config_schema(cls) -> list[DriverField]:
        return [
            DriverField(
                name="host",
                label="Host / IP",
                type="text",
                required=True,
                preset="simulator.local",
                placeholder="simulator.local",
                help="Simulierter Hostname für den Referenztreiber.",
                create_phase="basic",
            ),
            DriverField(
                name="settings.port",
                label="API-Port",
                type="number",
                required=True,
                preset=4028,
                default=4028,
                placeholder="4028",
                help="Simulierter API-Port für die generische GUI.",
                create_phase="basic",
            ),
            DriverField(
                name="settings.username",
                label="Benutzer",
                type="text",
                required=True,
                preset="sim",
                default="sim",
                placeholder="sim",
                help="Simulierter Benutzername für den Referenztreiber.",
                create_phase="basic",
            ),
            DriverField(
                name="settings.password",
                label="Passwort",
                type="password",
                required=True,
                preset="sim",
                default="sim",
                placeholder="sim",
                help="Simuliertes Passwort für den Referenztreiber.",
                create_phase="basic",
            ),
        ]

    def __init__(
        self,
        miner_id: str,
        name: str,
        host: str,
        priority: int = 100,
        enabled: bool = True,
        serial_number: str | None = None,
        model: str | None = None,
        firmware_version: str | None = None,
        profiles: dict | None = None,
        min_regulated_profile: str = "off",
        use_battery_when_charging: bool = False,
        battery_charge_soc_min: float = 95.0,
        battery_charge_profile: str = "p1",
        use_battery_when_discharging: bool = False,
        battery_discharge_soc_min: float = 80.0,
        battery_discharge_profile: str = "p1",
    ) -> None:
        profile_cfg = profiles or {
            "p1": {"power_w": 900},
            "p2": {"power_w": 1800},
            "p3": {"power_w": 3000},
            "p4": {"power_w": 4200},
        }

        miner_profiles = MinerProfiles(
            p1=MinerProfile(power_w=float(profile_cfg["p1"]["power_w"])),
            p2=MinerProfile(power_w=float(profile_cfg["p2"]["power_w"])),
            p3=MinerProfile(power_w=float(profile_cfg["p3"]["power_w"])),
            p4=MinerProfile(power_w=float(profile_cfg["p4"]["power_w"])),
        )

        normalized_min_regulated_profile = (
            min_regulated_profile
            if min_regulated_profile in {"off", "p1", "p2", "p3", "p4"}
            else "off"
        )

        self.info = MinerInfo(
            id=miner_id,
            name=name,
            host=host,
            driver="simulator",
            enabled=enabled,
            is_active=True,
            priority=priority,
            serial_number=serial_number or f"SIM-{miner_id.upper()}",
            model=model or "Simulator",
            firmware_version=firmware_version or "sim-0.1",
            profile="off",
            power_w=0.0,
            profiles=miner_profiles,
            min_regulated_profile=normalized_min_regulated_profile,
            use_battery_when_charging=bool(use_battery_when_charging),
            battery_charge_soc_min=float(battery_charge_soc_min),
            battery_charge_profile=battery_charge_profile,
            use_battery_when_discharging=bool(use_battery_when_discharging),
            battery_discharge_soc_min=float(battery_discharge_soc_min),
            battery_discharge_profile=battery_discharge_profile,
            reachable=True,
            runtime_state="paused",
            control_mode="power_target",
            autotuning_enabled=True,
            power_target_min_w=float(profile_cfg["p1"]["power_w"]),
            power_target_default_w=float(profile_cfg["p2"]["power_w"]),
            power_target_max_w=float(profile_cfg["p4"]["power_w"]),
        )

    async def set_profile(self, profile: str) -> None:
        self.info.profile = profile

        if profile == "off":
            self.info.power_w = 0.0
            self.info.runtime_state = "paused"
        else:
            desired_w = self.get_profile_power_w(profile)
            if desired_w <= 0:
                self.info.power_w = 0.0
                self.info.runtime_state = "paused"
            else:
                self.info.power_w = desired_w
                self.info.runtime_state = "running"

        self.info.last_error = None
        self.info.last_seen = datetime.now(UTC)

    async def get_status(self) -> MinerInfo:
        self.info.is_active = True
        self.info.reachable = True
        self.info.last_seen = datetime.now(UTC)
        return self.info
