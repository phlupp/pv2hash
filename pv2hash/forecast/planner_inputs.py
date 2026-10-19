"""Read-only input inventory for the *future* battery/mining shadow planner.

This module does not control miners, change configuration, or produce setpoints.
Absent/untrustworthy measurements remain None; never synthesize charge permission.
"""
from __future__ import annotations

import math

DEFAULT_BATTERY_TARGET = {
    "soc_pct": 100,
    "time_mode": "sunset",
    "offset_minutes": -120,
}


def _real(value: object, *, low: float = float("-inf"),
          high: float = float("inf")) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and low <= number <= high else None
    except (TypeError, ValueError):
        return None


def inspect_inputs(snapshot: dict, battery_debug: dict | None = None) -> dict:
    """Expose quality/missing fields, not a control recommendation.

    Grid: positive = import, negative = export. Household demand exclusive
    of miners = PV + grid import + battery discharge - battery charge - miners.
    Requires *all* contributors to be live, including every miner's reading.
    """
    source = snapshot.get("source") or {}
    pv = snapshot.get("pv") or {}
    battery = snapshot.get("battery") or {}
    totals = snapshot.get("totals") or {}
    miners = snapshot.get("miners") or []
    debug = battery_debug or {}
    quality = {
        "grid": source.get("quality"),
        "pv": pv.get("quality"),
        "battery": battery.get("quality"),
    }
    values = {
        "grid_power_w": _real(source.get("grid_power_w")) if quality["grid"] == "live" else None,
        "pv_power_w": _real(pv.get("power_w"), low=-999) if quality["pv"] == "live" else None,
        "battery_soc_pct": _real(battery.get("soc_pct"), low=0, high=100) if quality["battery"] == "live" else None,
        "battery_charge_w": _real(battery.get("charge_power_w"), low=0) if quality["battery"] == "live" else None,
        "battery_discharge_w": _real(battery.get("discharge_power_w"), low=0) if quality["battery"] == "live" else None,
        "miner_power_w": _real(totals.get("miner_power_w"), low=0),
        "battery_nominal_capacity_kwh": _real(debug.get("battery_nominal_capacity_kwh"), low=0.01) if quality["battery"] == "live" else None,
        "battery_usable_capacity_kwh": None,  # Not yet modeled; must be validated.
        "battery_max_charge_w": None,          # Current (A) is not a verified W limit.
        "battery_charge_efficiency": None,     # Do not assume 100%.
        "forecast_household_load_w": None,    # Requires a future load model.
    }
    missing = []
    for key in ("grid_power_w", "pv_power_w", "battery_soc_pct",
                "battery_charge_w", "battery_discharge_w", "miner_power_w"):
        if values[key] is None:
            missing.append(key)
    # Totals report a synthetic 0 for unreachable miners. Do not consider
    # that a valid measured 0 W for a future energy-budget calculation.
    unobserved = [str(item.get("key") or item.get("name") or "unknown")
                  for item in miners if item.get("monitor_enabled", True)
                  and not item.get("reachable")]
    if unobserved:
        values["miner_power_w"] = None
        if "miner_power_w" not in missing:
            missing.append("miner_power_w")
    if not battery.get("enabled"):
        missing.append("battery_enabled")
    # Optional single-reading net balance (no forecast; no mining permission).
    contributors = [values.get(key) for key in ("pv_power_w", "grid_power_w",
                                             "battery_charge_w", "battery_discharge_w",
                                             "miner_power_w")]
    load = None
    if all(value is not None for value in contributors):
        value = (values["pv_power_w"] + values["grid_power_w"]
                 + values["battery_discharge_w"] - values["battery_charge_w"]
                 - values["miner_power_w"])
        if value >= -100:  # meter noise; larger imbalance is unreliable
            load = round(max(0.0, value), 2)
        else:
            missing.append("household_balance_inconsistent")
    values["household_load_now_w"] = load
    if load is None:
        missing.append("household_load_now_w")
    for key in ("battery_usable_capacity_kwh", "battery_max_charge_w",
                "battery_charge_efficiency", "forecast_household_load_w"):
        missing.append(key)
    return {
        "status": "preparation_only", "planner_enabled": False,
        "target_default": dict(DEFAULT_BATTERY_TARGET),
        "inputs": values, "source_quality": quality,
        "unobserved_miners": unobserved,
        "missing": sorted(set(missing)),
        "ready_for_control": False,
        "notice": "Nur Dateninventar, keine Batterie-/Minersteuerung oder Minerfreigabe.",
    }
