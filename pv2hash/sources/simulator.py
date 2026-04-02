from time import monotonic
from datetime import UTC, datetime

from pv2hash.sources.base import EnergySource
from pv2hash.models.energy import EnergySnapshot


class SimulatorSource(EnergySource):
    def __init__(
        self,
        simulator_import_power_w: float = 1000.0,
        simulator_export_power_w: float = 10000.0,
        simulator_ramp_rate_w_per_minute: float = 600.0,
    ) -> None:
        self.import_power_w = max(0.0, float(simulator_import_power_w))
        self.export_power_w = max(0.0, float(simulator_export_power_w))
        self.ramp_rate_w_per_minute = max(0.0, float(simulator_ramp_rate_w_per_minute))
        self.ramp_rate_w_per_second = self.ramp_rate_w_per_minute / 60.0

        # Basiswert ohne Minerlast:
        # + = Netzbezug, - = Einspeisung
        self._base_grid_power_w = self.import_power_w
        self._direction = -1.0  # startet Richtung Einspeisung
        self._last_update_monotonic = monotonic()

        # von außen gesetzte aktuelle Minerlast
        self._simulated_miner_power_w = 0.0

        self.debug_info = {
            "mode": "linear_triangle_wave",
            "import_power_w": self.import_power_w,
            "export_power_w": self.export_power_w,
            "ramp_rate_w_per_minute": self.ramp_rate_w_per_minute,
            "base_grid_power_w": self._base_grid_power_w,
            "simulated_miner_power_w": self._simulated_miner_power_w,
            "measured_grid_power_w": self._base_grid_power_w,
            "direction": "towards_export",
        }

    def set_simulated_miner_power_w(self, power_w: float) -> None:
        self._simulated_miner_power_w = max(0.0, float(power_w))
        self.debug_info["simulated_miner_power_w"] = self._simulated_miner_power_w

    def _advance_base_value(self) -> None:
        now_mono = monotonic()
        elapsed = max(0.0, now_mono - self._last_update_monotonic)
        self._last_update_monotonic = now_mono

        if elapsed <= 0.0 or self.ramp_rate_w_per_second <= 0.0:
            return

        lower = -self.export_power_w
        upper = self.import_power_w

        candidate = self._base_grid_power_w + (
            self._direction * self.ramp_rate_w_per_second * elapsed
        )

        # Dreiecksfunktion mit Reflexion an den Grenzen
        while candidate < lower or candidate > upper:
            if candidate < lower:
                overshoot = lower - candidate
                candidate = lower + overshoot
                self._direction = 1.0
            elif candidate > upper:
                overshoot = candidate - upper
                candidate = upper - overshoot
                self._direction = -1.0

        self._base_grid_power_w = candidate

        self.debug_info["base_grid_power_w"] = self._base_grid_power_w
        self.debug_info["direction"] = (
            "towards_import" if self._direction > 0 else "towards_export"
        )

    async def read(self) -> EnergySnapshot:
        self._advance_base_value()

        measured_grid_power_w = self._base_grid_power_w + self._simulated_miner_power_w

        self.debug_info["measured_grid_power_w"] = measured_grid_power_w

        return EnergySnapshot(
            grid_power_w=measured_grid_power_w,
            pv_power_w=None,
            house_power_w=None,
            battery_charge_power_w=None,
            battery_discharge_power_w=None,
            battery_soc_pct=None,
            updated_at=datetime.now(UTC),
            source="simulator",
            quality="simulated",
        )