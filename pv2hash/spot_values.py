from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from time import monotonic
from typing import Any

_INSTALLED = False


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "str"


def _unit_for_key(key: str) -> str:
    suffix_units = {
        "_w": "W",
        "_pct": "%",
        "_seconds": "s",
        "_s": "s",
    }
    for suffix, unit in suffix_units.items():
        if key.endswith(suffix):
            return unit
    return ""


def _display_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}" if abs(value) < 10 else f"{value:.1f}"
    if isinstance(value, (list, dict)):
        return str(_json_safe(value))
    return str(value)


def _row(key: str, value: Any, *, source: str = "runtime", unit: str | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "label": key,
        "value": _json_safe(value),
        "value_text": _display_value(value),
        "type": _value_type(value),
        "unit": _unit_for_key(key) if unit is None else unit,
        "source": source,
    }


def _flatten_rows(prefix: str, value: Any, *, source: str = "decision_context") -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows: list[dict[str, Any]] = []
        for key in sorted(value.keys(), key=str):
            rows.extend(_flatten_rows(f"{prefix}.{key}", value[key], source=source))
        return rows
    return [_row(prefix, value, source=source)]


def _seconds_since(value: Any, now_mono: float) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, now_mono - float(value))
    except Exception:
        return None


def _controller_state_rows(controller: Any, now_mono: float) -> list[dict[str, Any]]:
    ctrl_state = getattr(controller, "state", None)
    if ctrl_state is None:
        return []
    rows = [
        _row("controller.state.last_live_profiles", getattr(ctrl_state, "last_live_profiles", None), source="controller.state"),
        _row("controller.state.degraded_quality", getattr(ctrl_state, "degraded_quality", None), source="controller.state"),
        _row("controller.state.export_hold_key", getattr(ctrl_state, "export_hold_key", None), source="controller.state"),
        _row("controller.state.last_import_log_key", getattr(ctrl_state, "last_import_log_key", None), source="controller.state"),
        _row("controller.state.last_export_log_key", getattr(ctrl_state, "last_export_log_key", None), source="controller.state"),
        _row("controller.state.last_battery_log_key", getattr(ctrl_state, "last_battery_log_key", None), source="controller.state"),
    ]
    for name in (
        "live_profiles_since_monotonic",
        "degraded_since_monotonic",
        "import_exceeded_since_monotonic",
        "export_available_since_monotonic",
    ):
        raw = getattr(ctrl_state, name, None)
        rows.append(_row(f"controller.state.{name}", raw, source="controller.state"))
        rows.append(_row(f"controller.state.{name.replace('_monotonic', '_seconds')}", _seconds_since(raw, now_mono), source="controller.state"))
    return rows


def _runtime_rows(app_mod: Any, now_mono: float) -> list[dict[str, Any]]:
    st = app_mod.state
    elapsed = None
    interval = None
    remaining = None
    try:
        interval = float(st.config.get("control", {}).get("min_switch_interval_seconds", 0) or 0)
        if st.last_profile_switch_monotonic is not None:
            elapsed = max(0.0, now_mono - float(st.last_profile_switch_monotonic))
            remaining = max(0.0, interval - elapsed)
    except Exception:
        pass
    return [
        _row("controller.runtime.last_decision", st.last_decision, source="app.state"),
        _row("controller.runtime.last_decision_at", _iso(st.last_decision_at), source="app.state"),
        _row("controller.runtime.last_profile_switch_at", _iso(st.last_profile_switch_at), source="app.state"),
        _row("controller.runtime.last_reload_at", _iso(st.last_reload_at), source="app.state"),
        _row("controller.timers.min_switch_interval_seconds", interval, source="app.state"),
        _row("controller.timers.min_switch_elapsed_seconds", elapsed, source="app.state"),
        _row("controller.timers.min_switch_remaining_seconds", remaining, source="app.state"),
    ]


def _decision_rows(app_mod: Any) -> list[dict[str, Any]]:
    decision = getattr(app_mod.state, "last_spot_controller_decision", None)
    if decision is None:
        return [
            _row("controller.decision.available", False, source="spot_values"),
            _row("controller.decision.summary", app_mod.state.last_decision, source="app.state"),
        ]
    rows = [
        _row("controller.decision.available", True, source="spot_values"),
        _row("controller.decision.action", getattr(decision, "action", None), source="decision"),
        _row("controller.decision.summary", getattr(decision, "summary", None), source="decision"),
        _row("controller.decision.reason_code", getattr(decision, "reason_code", None), source="decision"),
        _row("controller.decision.flags", getattr(decision, "flags", None), source="decision"),
        _row("controller.decision.distribution_reason", getattr(decision, "distribution_reason", None), source="decision"),
        _row("controller.decision.debug_event_type", getattr(decision, "debug_event_type", None), source="decision"),
        _row("controller.decision.debug_requested_profiles", getattr(decision, "debug_requested_profiles", None), source="decision"),
        _row("controller.decision.profiles", getattr(decision, "profiles", None), source="decision"),
    ]
    context = deepcopy(getattr(decision, "decision_context", None) or {})
    rows.extend(_flatten_rows("controller.context", context, source="decision_context"))
    return rows


def build_spot_values(app_mod: Any) -> dict[str, Any]:
    now_mono = monotonic()
    controller = getattr(app_mod.services, "controller", None)
    rows = []
    rows.extend(_decision_rows(app_mod))
    rows.extend(_runtime_rows(app_mod, now_mono))
    rows.extend(_controller_state_rows(controller, now_mono))
    values = {row["key"]: row["value"] for row in rows}
    return {
        "status": "ok",
        "schema_version": 1,
        "updated_at": datetime.now(UTC).isoformat(),
        "sections": [
            {
                "id": "controller",
                "title": "Controller",
                "subtitle": "Read-only Momentwerte aus letzter Reglerentscheidung und Controller-State.",
                "rows": rows,
            }
        ],
        "values": values,
    }


def _wrap_controller(controller: Any, app_mod: Any) -> None:
    if controller is None or getattr(controller, "_pv2hash_spot_values_wrapped", False):
        return
    original_decide = controller.decide

    def decide_with_spot_capture(*args, **kwargs):
        decision = original_decide(*args, **kwargs)
        app_mod.state.last_spot_controller_decision = decision
        app_mod.state.last_spot_controller_decision_at = datetime.now(UTC)
        return decision

    controller.decide = decide_with_spot_capture
    controller._pv2hash_spot_values_wrapped = True


def install(app_mod: Any) -> None:
    global _INSTALLED
    if getattr(app_mod, "_pv2hash_spot_values_installed", False):
        return

    original_reload_runtime = app_mod.reload_runtime

    def reload_runtime_with_spot_values() -> None:
        result = original_reload_runtime()
        _wrap_controller(getattr(app_mod.services, "controller", None), app_mod)
        return result

    app_mod.reload_runtime = reload_runtime_with_spot_values
    _wrap_controller(getattr(app_mod.services, "controller", None), app_mod)

    async def spot_values_page(request: app_mod.Request):
        return app_mod.templates.TemplateResponse(
            request=request,
            name="spot_values.html",
            context={
                "request": request,
                "refresh_seconds": app_mod._safe_int(app_mod.state.config.get("app", {}).get("refresh_seconds", 5), 5),
                "instance_name": app_mod.state.config.get("system", {}).get("instance_name", "PV2Hash Node"),
                "app_version_full": app_mod.APP_VERSION_FULL,
                "update_check": app_mod.update_checker.snapshot(),
            },
        )

    async def api_spot_values():
        return app_mod.JSONResponse(content=app_mod.jsonable_encoder(build_spot_values(app_mod)))

    spot_values_page.__annotations__["request"] = app_mod.Request
    app_mod.app.get("/spot-values")(spot_values_page)
    app_mod.app.get("/api/spot-values")(api_spot_values)
    app_mod._pv2hash_spot_values_installed = True
    _INSTALLED = True
