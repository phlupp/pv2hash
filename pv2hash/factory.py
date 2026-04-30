from pv2hash.logging_ext.setup import get_logger
from pv2hash.miners.base import MinerAdapter
from pv2hash.miners.braiins import BraiinsMiner
from pv2hash.miners.axeos import AxeOsMiner
from pv2hash.miners.simulator import SimulatorMiner
from pv2hash.miners.whatsminer_api3 import WhatsminerApi3Miner
from pv2hash.sources.base import EnergySource
from pv2hash.sources.battery_modbus import BatteryModbusSource, ModbusValueConfig
from pv2hash.sources.simulator import SimulatorSource
from pv2hash.sources.sma_meter_protocol import SmaMeterProtocolSource
from pv2hash.sockets.base import SocketInfo
from pv2hash.sockets.simulator import SimulatorSocket

logger = get_logger("pv2hash.factory")


MODBUS_REGISTER_TYPES = ("holding", "input", "coil", "discrete_input")
MODBUS_VALUE_TYPES = ("int8", "uint8", "int16", "uint16", "int32", "uint32", "float32")
MODBUS_ENDIAN_TYPES = ("big_endian", "little_endian")


def _normalize_miner_driver(driver: str | None) -> str:
    normalized = str(driver or "simulator").strip().lower()
    if normalized in {"axeos", "espminer", "esp-miner", "bitaxe"}:
        return "axeos"
    if normalized in {"whatsminer3", "whatsminer_api3"}:
        return "whatsminer_api3"
    return normalized or "simulator"


def _default_profiles_for_driver(driver: str) -> dict:
    driver = _normalize_miner_driver(driver)
    if driver == "braiins":
        return {
            "p1": {"power_w": 1200},
            "p2": {"power_w": 2200},
            "p3": {"power_w": 3200},
            "p4": {"power_w": 4200},
        }
    if driver == "axeos":
        return {
            "p1": {"power_w": 200},
            "p2": {"power_w": 200},
            "p3": {"power_w": 200},
            "p4": {"power_w": 200},
        }
    if driver == "whatsminer_api3":
        return {
            "p1": {"power_w": 1000},
            "p2": {"power_w": 1400},
            "p3": {"power_w": 1800},
            "p4": {"power_w": 2200},
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
    if value in {"off", "p1", "p2", "p3", "p4"}:
        return str(value)
    return "off"


def _normalize_battery_override_profile(value: str | None) -> str:
    if value in {"p1", "p2", "p3", "p4"}:
        return str(value)
    return "p1"


def _apply_runtime_flags(adapter: MinerAdapter, monitor_enabled: bool, control_enabled: bool) -> MinerAdapter:
    adapter.info.monitor_enabled = bool(monitor_enabled)
    adapter.info.control_enabled = bool(control_enabled)
    adapter.info.enabled = bool(monitor_enabled)
    return adapter


def _build_modbus_value_config(name: str, cfg: dict | None) -> ModbusValueConfig:
    cfg = dict(cfg or {})
    register_type = str(cfg.get("register_type", "holding")).strip().lower()
    if register_type not in MODBUS_REGISTER_TYPES:
        register_type = "holding"

    value_type = str(cfg.get("value_type", "uint16")).strip().lower()
    if value_type not in MODBUS_VALUE_TYPES:
        value_type = "uint16"

    endian = str(cfg.get("endian", "big_endian")).strip().lower()
    if endian not in MODBUS_ENDIAN_TYPES:
        endian = "big_endian"

    address = cfg.get("address")
    try:
        address = int(address) if address not in (None, "") else None
    except Exception:
        address = None

    try:
        factor = float(cfg.get("factor", 1.0))
    except Exception:
        factor = 1.0

    return ModbusValueConfig(
        name=name,
        register_type=register_type,
        address=address,
        value_type=value_type,
        endian=endian,
        factor=factor,
    )


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
            device_serial_number=settings.get("device_serial_number", ""),
            debug_dump_obis=bool(settings.get("debug_dump_obis", False)),
        )

    raise ValueError(f"Unsupported source type: {source_type}")


def build_battery_source(config: dict) -> EnergySource | None:
    battery_cfg = config.get("battery", {}) or {}
    if not battery_cfg.get("enabled", False):
        return None

    battery_type = battery_cfg.get("type", "none")
    settings = battery_cfg.get("settings", {})

    logger.info("Building battery source adapter: %s", battery_type)

    if battery_type in {"", "none", None}:
        return None

    if battery_type == "battery_modbus":
        return BatteryModbusSource(
            host=str(settings.get("host", "")).strip(),
            port=int(settings.get("port", 502)),
            unit_id=int(settings.get("unit_id", 1)),
            poll_interval_ms=int(settings.get("poll_interval_ms", 1000)),
            request_timeout_seconds=float(settings.get("request_timeout_seconds", 1.0)),
            soc=_build_modbus_value_config("soc", settings.get("soc")),
            charge_power=_build_modbus_value_config("charge_power", settings.get("charge_power")),
            discharge_power=_build_modbus_value_config("discharge_power", settings.get("discharge_power")),
            voltage=_build_modbus_value_config("voltage", settings.get("voltage")),
            current=_build_modbus_value_config("current", settings.get("current")),
            soh=_build_modbus_value_config("soh", settings.get("soh")),
            temperature=_build_modbus_value_config("temperature", settings.get("temperature")),
            capacity=_build_modbus_value_config("capacity", settings.get("capacity")),
            max_charge_current=_build_modbus_value_config("max_charge_current", settings.get("max_charge_current")),
            max_discharge_current=_build_modbus_value_config("max_discharge_current", settings.get("max_discharge_current")),
        )

    raise ValueError(f"Unsupported battery source type: {battery_type}")


def build_miners(config: dict) -> list[MinerAdapter]:
    miner_adapters: list[MinerAdapter] = []

    miner_items = sorted(
        config.get("miners", []),
        key=lambda m: (m.get("priority", 100), m.get("name", "")),
    )

    for miner_cfg in miner_items:
        monitor_enabled = bool(miner_cfg.get("monitor_enabled", True))
        control_enabled = bool(miner_cfg.get("control_enabled", True))
        if control_enabled:
            monitor_enabled = True

        if not monitor_enabled:
            continue

        driver = _normalize_miner_driver(miner_cfg.get("driver", "simulator"))
        settings = miner_cfg.get("settings", {})
        profiles = _normalize_profiles(driver, miner_cfg.get("profiles"))
        min_regulated_profile = _normalize_min_regulated_profile(
            miner_cfg.get("min_regulated_profile", "off")
        )

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
                    enabled=monitor_enabled,
                    serial_number=miner_cfg.get("serial_number"),
                    model=miner_cfg.get("model"),
                    firmware_version=miner_cfg.get("firmware_version"),
                    profiles=profiles,
                    min_regulated_profile=min_regulated_profile,
                    use_battery_when_charging=bool(
                        miner_cfg.get("use_battery_when_charging", False)
                    ),
                    battery_charge_soc_min=float(
                        miner_cfg.get("battery_charge_soc_min", 95.0)
                    ),
                    battery_charge_profile=_normalize_battery_override_profile(
                        miner_cfg.get("battery_charge_profile", "p1")
                    ),
                    use_battery_when_discharging=bool(
                        miner_cfg.get("use_battery_when_discharging", False)
                    ),
                    battery_discharge_soc_min=float(
                        miner_cfg.get("battery_discharge_soc_min", 80.0)
                    ),
                    battery_discharge_profile=_normalize_battery_override_profile(
                        miner_cfg.get("battery_discharge_profile", "p1")
                    ),
                )
            )
            continue

        if driver == "whatsminer_api3":
            miner_adapters.append(
                WhatsminerApi3Miner(
                    miner_id=miner_cfg["id"],
                    name=miner_cfg["name"],
                    host=miner_cfg["host"],
                    port=int(settings.get("port", 4433)),
                    account=settings.get("account", "super"),
                    password=settings.get("password", ""),
                    priority=miner_cfg.get("priority", 100),
                    enabled=monitor_enabled,
                    serial_number=miner_cfg.get("serial_number"),
                    model=miner_cfg.get("model"),
                    firmware_version=miner_cfg.get("firmware_version"),
                    profiles=profiles,
                    min_regulated_profile=min_regulated_profile,
                    use_battery_when_charging=bool(
                        miner_cfg.get("use_battery_when_charging", False)
                    ),
                    battery_charge_soc_min=float(
                        miner_cfg.get("battery_charge_soc_min", 95.0)
                    ),
                    battery_charge_profile=_normalize_battery_override_profile(
                        miner_cfg.get("battery_charge_profile", "p1")
                    ),
                    use_battery_when_discharging=bool(
                        miner_cfg.get("use_battery_when_discharging", False)
                    ),
                    battery_discharge_soc_min=float(
                        miner_cfg.get("battery_discharge_soc_min", 80.0)
                    ),
                    battery_discharge_profile=_normalize_battery_override_profile(
                        miner_cfg.get("battery_discharge_profile", "p1")
                    ),
                    timeout_s=float(settings.get("timeout_s", 5.0)),
                )
            )
            continue

        if driver == "axeos":
            miner_adapters.append(
                AxeOsMiner(
                    miner_id=miner_cfg["id"],
                    name=miner_cfg["name"],
                    host=miner_cfg["host"],
                    port=int(settings.get("port", 80)),
                    priority=miner_cfg.get("priority", 100),
                    enabled=monitor_enabled,
                    serial_number=miner_cfg.get("serial_number"),
                    model=miner_cfg.get("model"),
                    firmware_version=miner_cfg.get("firmware_version"),
                    profiles=profiles,
                    min_regulated_profile=min_regulated_profile,
                    use_battery_when_charging=bool(
                        miner_cfg.get("use_battery_when_charging", False)
                    ),
                    battery_charge_soc_min=float(
                        miner_cfg.get("battery_charge_soc_min", 95.0)
                    ),
                    battery_charge_profile=_normalize_battery_override_profile(
                        miner_cfg.get("battery_charge_profile", "p1")
                    ),
                    use_battery_when_discharging=bool(
                        miner_cfg.get("use_battery_when_discharging", False)
                    ),
                    battery_discharge_soc_min=float(
                        miner_cfg.get("battery_discharge_soc_min", 80.0)
                    ),
                    battery_discharge_profile=_normalize_battery_override_profile(
                        miner_cfg.get("battery_discharge_profile", "p1")
                    ),
                    timeout_s=float(settings.get("timeout_s", 3.0)),
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
                    enabled=monitor_enabled,
                    serial_number=miner_cfg.get("serial_number"),
                    model=miner_cfg.get("model"),
                    firmware_version=miner_cfg.get("firmware_version"),
                    profiles=profiles,
                    min_regulated_profile=min_regulated_profile,
                    use_battery_when_charging=bool(
                        miner_cfg.get("use_battery_when_charging", False)
                    ),
                    battery_charge_soc_min=float(
                        miner_cfg.get("battery_charge_soc_min", 95.0)
                    ),
                    battery_charge_profile=_normalize_battery_override_profile(
                        miner_cfg.get("battery_charge_profile", "p1")
                    ),
                    use_battery_when_discharging=bool(
                        miner_cfg.get("use_battery_when_discharging", False)
                    ),
                    battery_discharge_soc_min=float(
                        miner_cfg.get("battery_discharge_soc_min", 80.0)
                    ),
                    battery_discharge_profile=_normalize_battery_override_profile(
                        miner_cfg.get("battery_discharge_profile", "p1")
                    ),
                    timeout_s=float(settings.get("timeout_s", 2.0)),
                    power_limit_w=float(settings.get("power_limit_w", 0) or 0),
                )
            )
            continue

        raise ValueError(f"Unsupported miner driver: {driver}")

    flags_by_id = {
        str(m.get("id")): (bool(m.get("monitor_enabled", True)), bool(m.get("control_enabled", True)))
        for m in config.get("miners", [])
    }
    for adapter in miner_adapters:
        monitor_enabled, control_enabled = flags_by_id.get(str(adapter.info.id), (True, True))
        if control_enabled:
            monitor_enabled = True
        _apply_runtime_flags(adapter, monitor_enabled, control_enabled)

    return miner_adapters


def _normalize_socket_driver(driver: str | None) -> str:
    normalized = str(driver or "simulator").strip().lower()
    return normalized or "simulator"


def build_sockets(config: dict) -> list[SimulatorSocket]:
    socket_adapters = []
    socket_items = sorted(
        config.get("sockets", []) or [],
        key=lambda s: (s.get("priority", 100), s.get("name", "")),
    )

    for socket_cfg in socket_items:
        enabled = bool(socket_cfg.get("enabled", True))
        monitor_enabled = bool(socket_cfg.get("monitor_enabled", True))
        if not enabled or not monitor_enabled:
            continue

        driver = _normalize_socket_driver(socket_cfg.get("driver", "simulator"))
        info = SocketInfo(
            id=str(socket_cfg.get("id") or ""),
            uuid=str(socket_cfg.get("uuid") or ""),
            name=str(socket_cfg.get("name") or "Socket"),
            driver=driver,
            host=str(socket_cfg.get("host") or ""),
            priority=int(socket_cfg.get("priority", 100) or 100),
            enabled=enabled,
            monitor_enabled=monitor_enabled,
            control_enabled=bool(socket_cfg.get("control_enabled", False)),
        )
        settings = socket_cfg.get("settings", {}) or {}

        logger.info(
            "Building socket adapter: id=%s name=%s driver=%s host=%s",
            info.id,
            info.name,
            driver,
            info.host,
        )

        if driver == "simulator":
            socket_adapters.append(SimulatorSocket(info=info, settings=settings))
            continue

        raise ValueError(f"Unsupported socket driver: {driver}")

    return socket_adapters
