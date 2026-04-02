from pv2hash.logging_ext.setup import get_logger
from pv2hash.miners.base import MinerAdapter
from pv2hash.miners.braiins import BraiinsMiner
from pv2hash.miners.simulator import SimulatorMiner
from pv2hash.sources.base import EnergySource
from pv2hash.sources.simulator import SimulatorSource
from pv2hash.sources.sma_meter_protocol import SmaMeterProtocolSource


logger = get_logger("pv2hash.factory")


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
                )
            )
            continue

        if driver == "braiins":
            miner_adapters.append(
                BraiinsMiner(
                    miner_id=miner_cfg["id"],
                    name=miner_cfg["name"],
                    host=miner_cfg["host"],
                    port=int(settings.get("port", 4028)),
                    priority=miner_cfg.get("priority", 100),
                    serial_number=miner_cfg.get("serial_number"),
                    model=miner_cfg.get("model"),
                    firmware_version=miner_cfg.get("firmware_version"),
                )
            )
            continue

        raise ValueError(f"Unsupported miner driver: {driver}")

    return miner_adapters