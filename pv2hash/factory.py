from pv2hash.logging_ext.setup import get_logger
from pv2hash.miners.base import MinerAdapter
from pv2hash.miners.braiins import BraiinsMiner
from pv2hash.miners.simulator import SimulatorMiner
from pv2hash.sources.base import EnergySource
from pv2hash.sources.simulator import SimulatorSource
from pv2hash.sources.sma_meter_protocol import SmaMeterProtocolSource

logger = get_logger("pv2hash.factory")


BATTERY_PROFILE_NAMES = {"p1", "p2", "p3", "p4"}
MIN_REGULATED_PROFILE_NAMES = {"off", "p1", "p2", "p3", "p4"}


def _default_profiles_for_driver(driver: str) -> dict:
    if driver == "braiins":
        return {
            "p1": {"power_w": 1200},
            "p2": {"power_w": 2200},
            "p3": {"power_w": 3200},
            "p4": {"power_w": 4200},
        }

    return {
        "p1": {"power_w": 900},
        "p2": {"power_w": 1800},
        "p3": {"power_w": 3000},
        "p4": {"power_w": 4200},
    }


def _normalize_profiles(driver: str, profiles: dict | None) -> dict:
    normalized = dict(profiles or {})
    defaults = _default_profiles_for_driver(driver)

    result: dict = {}
    for name in ("p1", "p2", "p3", "p4"):
        value = normalized.get(name, defaults[name])

        if isinstance(value, dict):
            power_w = value.get("power_w", defaults[name]["power_w"])
        else:
            power_w = defaults[name]["power_w"]

        try:
            result[name] = {"power_w": float(power_w)}
        except Exception:
            result[name] = {"power_w": float(defaults[name]["power_w"])}

    return result


def _normalize_min_regulated_profile(value: str | None) -> str:
    if value in MIN_REGULATED_PROFILE_NAMES:
        return str(value)
    return "off"


def _normalize_battery_override_profile(value: str | None, default: str = "p1") -> str:
    normalized = str(value or default).strip().lower()
    if normalized in BATTERY_PROFILE_NAMES:
        return normalized
    return default


def _normalize_soc_threshold(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return max(0.0, min(parsed, 100.0))


def build_source(config: dict) -> EnergySource:
    source_cfg = config["source"]
    source_type = source_cfg.get("type", "simulator")
    settings = source_cfg.get("settings", {})

    logger.info("Building source adapter: %s", source_type)

    if source_type == "simulator":
        return SimulatorSource(
            simulator_import_power_w=float(
                settings.get("simulator_import_power_w", 1000.0)
            ),
            simulator_export_power_w=float(
                settings.get("simulator_export_power_w", 10000.0)
            ),
            simulator_ramp_rate_w_per_minute=float(
                settings.get("simulator_ramp_rate_w_per_minute", 600.0)
            ),
        )

    if source_type == "sma_meter_protocol":
        return SmaMeterProtocolSource(
            multicast_ip=settings.get("multicast_ip", "239.12.255.254"),
            bind_port=int(settings.get("bind_port", 9522)),
            interface_ip=settings.get("interface_ip", "0.0.0.0"),
            packet_timeout_seconds=float(settings.get("packet_timeout_seconds", 1.0)),
            stale_after_seconds=float(settings.get("stale_after_seconds", 8.0)),
            offline_after_seconds=float(settings.get("offline_after_seconds", 30.0)),
            device_ip=settings.get("device_ip", ""),
        )

    raise ValueError(f"Unsupported source type: {source_type}")


def build_miners(config: dict) -> list[MinerAdapter]:
    miner_adapters: list[MinerAdapter] = []

    miner_items = sorted(
        config.get("miners", []),
        key=lambda m: (m.get("priority", 100), m.get("name", "")),
    )

    for miner_cfg in miner_items:
        if not miner_cfg.get("enabled", True):
            continue

        driver = miner_cfg.get("driver", "simulator")
        settings = miner_cfg.get("settings", {})
        profiles = _normalize_profiles(driver, miner_cfg.get("profiles"))
        min_regulated_profile = _normalize_min_regulated_profile(
            miner_cfg.get("min_regulated_profile", "off")
        )

        battery_kwargs = {
            "use_battery_when_charging": bool(
                miner_cfg.get("use_battery_when_charging", False)
            ),
            "battery_charge_soc_min": _normalize_soc_threshold(
                miner_cfg.get("battery_charge_soc_min", 95.0),
                95.0,
            ),
            "battery_charge_profile": _normalize_battery_override_profile(
                miner_cfg.get("battery_charge_profile", "p1"),
                default="p1",
            ),
            "use_battery_when_discharging": bool(
                miner_cfg.get("use_battery_when_discharging", False)
            ),
            "battery_discharge_soc_min": _normalize_soc_threshold(
                miner_cfg.get("battery_discharge_soc_min", 80.0),
                80.0,
            ),
            "battery_discharge_profile": _normalize_battery_override_profile(
                miner_cfg.get("battery_discharge_profile", "p1"),
                default="p1",
            ),
        }

        logger.info(
            "Building miner adapter: id=%s name=%s driver=%s host=%s",
            miner_cfg.get("id"),
            miner_cfg.get("name"),
            driver,
            miner_cfg.get("host"),
        )

        if driver == "simulator":
            miner_adapters.append(
                SimulatorMiner(
                    miner_id=miner_cfg["id"],
                    name=miner_cfg["name"],
                    host=miner_cfg["host"],
                    priority=miner_cfg.get("priority", 100),
                    enabled=miner_cfg.get("enabled", True),
                    serial_number=miner_cfg.get("serial_number"),
                    model=miner_cfg.get("model"),
                    firmware_version=miner_cfg.get("firmware_version"),
                    profiles=profiles,
                    min_regulated_profile=min_regulated_profile,
                    **battery_kwargs,
                )
            )
            continue

        if driver == "braiins":
            miner_adapters.append(
                BraiinsMiner(
                    miner_id=miner_cfg["id"],
                    name=miner_cfg["name"],
                    host=miner_cfg["host"],
                    port=int(settings.get("port", 50051)),
                    priority=miner_cfg.get("priority", 100),
                    enabled=miner_cfg.get("enabled", True),
                    serial_number=miner_cfg.get("serial_number"),
                    model=miner_cfg.get("model"),
                    firmware_version=miner_cfg.get("firmware_version"),
                    profiles=profiles,
                    min_regulated_profile=min_regulated_profile,
                    username=settings.get("username", "root"),
                    password=settings.get("password", ""),
                    **battery_kwargs,
                )
            )
            continue

        raise ValueError(f"Unsupported miner driver: {driver}")

    logger.info("Built %d enabled miner adapter(s)", len(miner_adapters))
    return miner_adapters
