from datetime import UTC, datetime

from pv2hash.config.store import load_config
from pv2hash.controller.basic import BasicController
from pv2hash.factory import build_battery_source, build_miners, build_source
from pv2hash.sources.battery_modbus import BatteryModbusSource, ModbusValueConfig
from pv2hash.sources.simulator import SimulatorSource
from pv2hash.sources.sma_meter_protocol import SmaMeterProtocolSource
from pv2hash.logging_ext.setup import get_logger
from pv2hash.runtime import AppState


logger = get_logger("pv2hash.runtime")


class RuntimeServices:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.source = None
        self.battery_source = None
        self.miners = []
        self.controller = None
        self.last_error: str | None = None
        self.reload_generation = 0
        self._retired_miners = []

    @staticmethod
    def _close_adapter(adapter) -> None:
        if adapter is None:
            return
        close = getattr(adapter, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Error while closing old adapter", exc_info=True)

    def reload_from_config(self) -> None:
        config = load_config()
        self.state.config = config

        old_source = self.source
        old_battery_source = self.battery_source
        old_miners = list(self.miners)
        old_by_id = {getattr(miner.info, "id", None): miner for miner in old_miners}

        logger.info("Reloading runtime from config")
        logger.info(
            "Source type=%s, battery_type=%s, distribution=%s, policy=%s",
            config["source"].get("type"),
            config.get("battery", {}).get("type"),
            config["control"].get("distribution_mode"),
            config["control"].get("policy_mode"),
        )

        self.source = build_source(config)
        self.battery_source = build_battery_source(config)
        self.miners = build_miners(config)
        self._carry_runtime_state(old_by_id)
        self._retired_miners = [
            miner
            for miner_id, miner in old_by_id.items()
            if miner_id is not None and miner_id not in {m.info.id for m in self.miners}
        ]
        self.controller = BasicController(config["control"], config.get("battery", {}))
        if old_source is not self.source:
            self._close_adapter(old_source)
        if old_battery_source is not self.battery_source:
            self._close_adapter(old_battery_source)
        self.last_error = None
        self.reload_generation += 1

        now = datetime.now(UTC)
        self.state.last_reload_at = now
        self.state.source_reloaded_at = now

        logger.info(
            "Runtime reload finished, miners=%d generation=%d source=%s battery_source=%s",
            len(self.miners),
            self.reload_generation,
            config["source"].get("type"),
            config.get("battery", {}).get("type") if config.get("battery", {}).get("enabled") else "disabled",
        )

    def _carry_runtime_state(self, old_by_id: dict) -> None:
        for miner in self.miners:
            old = old_by_id.get(getattr(miner.info, "id", None))
            if old is None:
                continue

            miner.info.profile = old.info.profile
            miner.info.power_w = old.info.power_w
            miner.info.reachable = old.info.reachable
            miner.info.runtime_state = old.info.runtime_state
            miner.info.last_error = old.info.last_error
            miner.info.last_seen = old.info.last_seen
            miner.info.api_version = old.info.api_version
            miner.info.control_mode = old.info.control_mode
            miner.info.autotuning_enabled = old.info.autotuning_enabled
            miner.info.power_target_min_w = old.info.power_target_min_w
            miner.info.power_target_default_w = old.info.power_target_default_w
            miner.info.power_target_max_w = old.info.power_target_max_w

    def pop_retired_miners(self) -> list:
        retired = list(self._retired_miners)
        self._retired_miners = []
        return retired

    def get_source_debug_info(self) -> dict:
        if self.source is None:
            return {}
        return getattr(self.source, "debug_info", {}) or {}

    def get_battery_source_debug_info(self) -> dict:
        if self.battery_source is None:
            return {}
        return getattr(self.battery_source, "debug_info", {}) or {}

    def _preview_source_adapter(self, source_type: str, source_cfg: dict):
        settings = source_cfg.get("settings", {}) or {}
        if source_type == "simulator":
            return SimulatorSource(
                simulator_import_power_w=float(settings.get("simulator_import_power_w", 1000.0) or 1000.0),
                simulator_export_power_w=float(settings.get("simulator_export_power_w", 10000.0) or 10000.0),
                simulator_ramp_rate_w_per_minute=float(settings.get("simulator_ramp_rate_w_per_minute", 600.0) or 600.0),
            )
        if source_type == "sma_meter_protocol":
            class SmaPreviewSource(SmaMeterProtocolSource):
                def __init__(self):
                    self.driver_id = SmaMeterProtocolSource.driver_id
                    self.driver_label = SmaMeterProtocolSource.driver_label
                    self.debug_info = {}

                def get_config_fields(self, *, config: dict | None = None) -> list[dict]:
                    return SmaMeterProtocolSource.config_fields_from_settings(
                        settings=(config or {}).get("settings", {}) or {},
                        debug_info=self.debug_info,
                    )

            preview = SmaPreviewSource()
            preview.debug_info = self.get_source_debug_info()
            return preview
        return None

    def _preview_battery_adapter(self, battery_type: str, battery_cfg: dict):
        settings = battery_cfg.get("settings", {}) or {}
        if battery_type == "battery_modbus":
            def cfg(name: str) -> ModbusValueConfig:
                raw = settings.get(name, {}) if isinstance(settings.get(name), dict) else {}
                address = raw.get("address")
                try:
                    address = int(address) if address not in (None, "") else None
                except Exception:
                    address = None
                try:
                    factor = float(raw.get("factor", 1.0))
                except Exception:
                    factor = 1.0
                return ModbusValueConfig(
                    name=name,
                    register_type=str(raw.get("register_type", "holding") or "holding"),
                    address=address,
                    value_type=str(raw.get("value_type", "uint16") or "uint16"),
                    endian=str(raw.get("endian", "big_endian") or "big_endian"),
                    factor=factor,
                )

            def as_int(value, default: int) -> int:
                try:
                    return int(value)
                except Exception:
                    return default

            def as_float(value, default: float) -> float:
                try:
                    return float(value)
                except Exception:
                    return default

            return BatteryModbusSource(
                host=str(settings.get("host", "") or ""),
                port=as_int(settings.get("port", 502), 502),
                unit_id=as_int(settings.get("unit_id", 1), 1),
                poll_interval_ms=as_int(settings.get("poll_interval_ms", 1000), 1000),
                request_timeout_seconds=as_float(settings.get("request_timeout_seconds", 1.0), 1.0),
                soc=cfg("soc"),
                charge_power=cfg("charge_power"),
                discharge_power=cfg("discharge_power"),
            )
        return None

    def get_source_gui_models(self, config: dict | None = None, *, source_debug_override: dict | None = None) -> list[dict]:
        runtime_config = self.state.config or {}
        config = config or runtime_config
        preview_mode = config is not runtime_config
        snapshot = self.state.snapshot if not preview_mode else None
        models: list[dict] = []

        source_cfg = config.get("source", {}) or {}
        source_type = str(source_cfg.get("type", "simulator") or "simulator")
        source_adapter = self.source if (not preview_mode and self.source is not None) else self._preview_source_adapter(source_type, source_cfg)
        source_debug = source_debug_override
        if source_debug is None:
            source_debug = self.get_source_debug_info() if (not preview_mode or source_type == runtime_config.get("source", {}).get("type")) else {}
        if source_adapter is not None:
            if source_debug_override is not None and hasattr(source_adapter, "debug_info"):
                source_adapter.debug_info = source_debug_override
            source_model = source_adapter.get_gui_model(
                source_id="grid",
                role=str(source_cfg.get("role", "grid")),
                title="Netz-Messung",
                enabled=bool(source_cfg.get("enabled", True)),
                config=source_cfg,
                snapshot=(getattr(source_adapter, "last_snapshot", None) or snapshot),
                debug_info=source_debug,
            )
            source_model["summary"] = [
                {"label": "Profil", "value": source_model.get("driver_label")},
                {"label": "Status", "value": source_model.get("status", {}).get("text")},
            ]
            source_model["driver_field"] = {
                "name": "source_type",
                "label": "Messprofil",
                "type": "select",
                "value": source_type,
                "help": "Das Profil bestimmt, welche weiteren Eingabefelder benötigt werden.",
                "options": [
                    {"value": "simulator", "label": "Simulierter Netzanschlusspunkt"},
                    {"value": "sma_meter_protocol", "label": "SMA Energy Meter"},
                ],
            }
            models.append(source_model)

        battery_cfg = config.get("battery", {}) or {}
        battery_type = str(battery_cfg.get("type", "none") or "none")
        battery_enabled = bool(battery_cfg.get("enabled")) and battery_type != "none"
        battery_adapter = self.battery_source if (not preview_mode and self.battery_source is not None) else self._preview_battery_adapter(battery_type, battery_cfg)
        if battery_adapter is not None and battery_type != "none":
            battery_model = battery_adapter.get_gui_model(
                source_id="battery",
                role=str(battery_cfg.get("role", "battery")),
                title="Batterie",
                enabled=battery_enabled,
                config=battery_cfg,
                snapshot=(getattr(battery_adapter, "last_snapshot", None) if not preview_mode else None),
                debug_info=(self.get_battery_source_debug_info() if not preview_mode or battery_type == runtime_config.get("battery", {}).get("type") else {}),
            )
        else:
            battery_model = {
                "id": "battery",
                "role": str(battery_cfg.get("role", "battery")),
                "title": "Batterie",
                "enabled": False,
                "driver": battery_type,
                "driver_label": str(battery_cfg.get("name", "Keine Batterie") or "Keine Batterie"),
                "status": {
                    "state": "disabled",
                    "text": "Deaktiviert",
                    "age_seconds": None,
                    "updated_at": None,
                },
                "header_fields": [
                    {"label": "Status", "value": "Deaktiviert"},
                ],
                "config_fields": [],
                "detail_groups": [],
                "capabilities": {},
            }

        battery_model["summary"] = [
            {"label": "Profil", "value": battery_model.get("driver_label")},
            {"label": "Status", "value": "aktiv" if battery_enabled else "deaktiviert"},
        ]
        battery_model["driver_field"] = {
            "name": "battery_type",
            "label": "Batterieprofil",
            "type": "select",
            "value": battery_type,
            "help": "Das Profil bestimmt die nachfolgenden Eingabefelder.",
            "options": [
                {"value": "none", "label": "Keine Batterie"},
                {"value": "battery_modbus", "label": "Modbus TCP Batterie"},
            ],
        }
        battery_model["config_fields"] = [
            {
                "name": "battery_enabled",
                "label": "Batterie aktiviert",
                "type": "checkbox",
                "value": battery_enabled,
                "disabled_when_driver": "none",
            },
            *list(battery_model.get("config_fields") or []),
        ]
        models.append(battery_model)

        return models
