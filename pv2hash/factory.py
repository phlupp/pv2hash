from pv2hash.miners.base import MinerAdapter
from pv2hash.miners.simulator import SimulatorMiner
from pv2hash.sources.base import EnergySource
from pv2hash.sources.simulator import SimulatorSource


def build_source(config: dict) -> EnergySource:
    source_cfg = config["source"]
    source_type = source_cfg.get("type", "simulator")

    if source_type == "simulator":
        return SimulatorSource()

    raise ValueError(f"Unsupported source type: {source_type}")


def build_miners(config: dict) -> list[MinerAdapter]:
    miner_adapters: list[MinerAdapter] = []

    for miner_cfg in config.get("miners", []):
        if not miner_cfg.get("enabled", True):
            continue

        driver = miner_cfg.get("driver", "simulator")

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

        raise ValueError(f"Unsupported miner driver: {driver}")

    return miner_adapters
