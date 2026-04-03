from pv2hash.logging_ext.setup import get_logger
from pv2hash.miners.base import MinerAdapter
from pv2hash.miners.braiins import BraiinsMiner
from pv2hash.miners.simulator import SimulatorMiner
from pv2hash.sources.base import EnergySource
from pv2hash.sources.simulator import SimulatorSource
from pv2hash.sources.sma_meter_protocol import SmaMeterProtocolSource

logger = get_logger("pv2hash.factory")


def _default_profiles_for_driver(driver: str) -> dict:
    if driver == "braiins":
        return {
            "floor": {"power_w": 0},
            "eco": {"power_w": 1200},
            "mid": {"power_w": 2200},
            "high": {"power_w": 3200},
        }

    return {
        "floor": {"power_w": 0},
        "eco": {"power_w": 900},
        "mid": {"power_w": 1800},
        "high": {"power_w": 3000},
    }


def _normalize_profiles(driver: str, profiles: dict | None) -> dict:
    normalized = dict(profiles or {})

    # Altbestand migrieren: off -> floor
    if "floor" not in normalized and "off" in normalized:
        normalized["floor"] = normalized["off"]

    defaults = _default_profiles_for_driver(driver)

    result: dict = {}
    for name in ("floor", "eco", "mid", "high"):
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
                    username=settings.get("username", "root"),
                    password=settings.get("password", ""),
                    priority=miner_cfg.get("priority", 100),
                    enabled=miner_cfg.get("enabled", True),
                    serial_number=miner_cfg.get("serial_number"),
                    model=miner_cfg.get("model"),
                    firmware_version=miner_cfg.get("firmware_version"),
                    profiles=profiles,
                    timeout_s=float(settings.get("timeout_s", 8.0)),
                )
            )
            continue

        raise ValueError(f"Unsupported miner driver: {driver}")

    return miner_adapters