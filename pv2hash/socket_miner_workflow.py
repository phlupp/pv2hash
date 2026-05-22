from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from pv2hash import socket_miner_assignment as assignment

_RUNTIME: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _profile_is_off(profile: Any) -> bool:
    return str(profile or "off").strip().lower() == "off"


def _workflow_state(miner_id: str) -> dict[str, Any]:
    return _RUNTIME.setdefault(miner_id, {})


def _format_remaining(seconds: float | int | None) -> str:
    if seconds is None:
        return ""
    try:
        value = max(0, int(round(float(seconds))))
    except Exception:
        return ""
    if value >= 3600:
        hours = value // 3600
        minutes = (value % 3600) // 60
        return f"{hours} h {minutes:02d} min"
    if value >= 60:
        return f"{value // 60} min {value % 60:02d} s"
    return f"{value} s"


def _miner_config(app_mod: Any, miner_id: str) -> dict[str, Any] | None:
    return next((item for item in app_mod.state.config.get("miners", []) or [] if str(item.get("id") or "") == miner_id), None)


def _socket_adapter(app_mod: Any, socket_id: str):
    for adapter in list(getattr(app_mod.services, "sockets", []) or []):
        if str(getattr(getattr(adapter, "info", None), "id", "") or "") == socket_id:
            return adapter
    return None


def _socket_config_for_miner(app_mod: Any, miner_id: str) -> dict[str, Any]:
    cfg = _miner_config(app_mod, miner_id) or {}
    socket_cfg = assignment._normalize_miner_socket_config(cfg.get("socket") if isinstance(cfg.get("socket"), dict) else {})
    if socket_cfg.get("mode") != "switching" or not socket_cfg.get("socket_id"):
        return {**socket_cfg, "enabled": False}
    error = assignment.validate_socket_assignment(app_mod.state.config, miner_id, socket_cfg)
    if error:
        return {**socket_cfg, "enabled": False, "validation_error": error}
    return socket_cfg


def _reset_startup(state: dict[str, Any]) -> None:
    state.pop("startup_due_monotonic", None)
    state.pop("startup_started_at", None)
    state.pop("startup_due_at", None)
    state.pop("startup_delay_seconds", None)


def _reset_off_timer(state: dict[str, Any]) -> None:
    state.pop("off_since_monotonic", None)
    state.pop("off_since", None)
    state.pop("power_off_due_monotonic", None)
    state.pop("power_off_due_at", None)
    state.pop("power_off_after_off_seconds", None)


def _mark_waiting_for_socket(miner: Any, message: str) -> None:
    info = getattr(miner, "info", None)
    if info is None:
        return
    try:
        info.last_error = message
        info.runtime_state = "waiting_for_socket"
    except Exception:
        pass


def _decorate_workflow_payload(miner_item: dict[str, Any], state: dict[str, Any]) -> None:
    socket_payload = miner_item.get("socket")
    if not isinstance(socket_payload, dict):
        return
    now = time.monotonic()
    startup_due = state.get("startup_due_monotonic")
    power_off_due = state.get("power_off_due_monotonic")
    startup_remaining = max(0.0, float(startup_due) - now) if startup_due is not None else None
    power_off_remaining = max(0.0, float(power_off_due) - now) if power_off_due is not None else None
    socket_payload["workflow"] = {
        "state": str(state.get("state") or "idle"),
        "message": str(state.get("message") or ""),
        "startup_started_at": state.get("startup_started_at"),
        "startup_due_at": state.get("startup_due_at"),
        "startup_remaining_seconds": startup_remaining,
        "startup_remaining_text": _format_remaining(startup_remaining),
        "off_since": state.get("off_since"),
        "power_off_due_at": state.get("power_off_due_at"),
        "power_off_remaining_seconds": power_off_remaining,
        "power_off_remaining_text": _format_remaining(power_off_remaining),
        "last_socket_action_at": state.get("last_socket_action_at"),
        "last_socket_action": state.get("last_socket_action"),
        "last_error": state.get("last_error"),
    }


async def _handle_profile_with_socket(
    *,
    app_mod: Any,
    miner: Any,
    original_set_profile: Callable[[str], Awaitable[Any]],
    profile: str,
) -> Any:
    info = getattr(miner, "info", None)
    miner_id = str(getattr(info, "id", "") or "")
    if not miner_id:
        return await original_set_profile(profile)

    socket_cfg = _socket_config_for_miner(app_mod, miner_id)
    state = _workflow_state(miner_id)
    if not socket_cfg.get("enabled"):
        _reset_startup(state)
        _reset_off_timer(state)
        state["state"] = "idle"
        state["message"] = ""
        if socket_cfg.get("validation_error"):
            state["last_error"] = socket_cfg.get("validation_error")
        return await original_set_profile(profile)

    socket_id = str(socket_cfg.get("socket_id") or "")
    socket_adapter = _socket_adapter(app_mod, socket_id)
    if socket_adapter is None:
        msg = "Zugeordnete Steckdose ist nicht verfügbar."
        state.update({"state": "socket_unavailable", "message": msg, "last_error": msg})
        _mark_waiting_for_socket(miner, msg)
        if _profile_is_off(profile):
            return await original_set_profile(profile)
        return None

    now = time.monotonic()

    if not _profile_is_off(profile):
        _reset_off_timer(state)
        try:
            socket_info = socket_adapter.get_status()
        except Exception as exc:
            msg = f"Steckdose konnte nicht gelesen werden: {exc}"
            state.update({"state": "socket_error", "message": msg, "last_error": msg})
            _mark_waiting_for_socket(miner, msg)
            return None

        if not bool(getattr(socket_info, "reachable", False)):
            msg = "Steckdose ist nicht erreichbar; Miner wird nicht gestartet."
            state.update({"state": "socket_unreachable", "message": msg, "last_error": msg})
            _mark_waiting_for_socket(miner, msg)
            return None

        startup_due = state.get("startup_due_monotonic")
        if getattr(socket_info, "is_on", None) is not True and startup_due is None:
            try:
                result = socket_adapter.switch_on()
            except Exception as exc:
                result = {"ok": False, "message": str(exc)}
            if not result or not result.get("ok"):
                msg = str((result or {}).get("message") or "Steckdose konnte nicht eingeschaltet werden.")
                state.update({"state": "socket_on_failed", "message": msg, "last_error": msg})
                _mark_waiting_for_socket(miner, msg)
                return None

            delay = max(0, int(socket_cfg.get("startup_delay_seconds") or 0))
            state.update({
                "state": "startup_delay" if delay else "socket_on",
                "message": f"Steckdose eingeschaltet; warte {_format_remaining(delay)} vor Miner-Start." if delay else "Steckdose eingeschaltet.",
                "startup_started_at": _now_iso(),
                "startup_due_at": datetime.fromtimestamp(time.time() + delay, UTC).isoformat(),
                "startup_due_monotonic": now + delay,
                "startup_delay_seconds": delay,
                "last_socket_action": "on",
                "last_socket_action_at": _now_iso(),
                "last_error": "",
            })
            if delay > 0:
                _mark_waiting_for_socket(miner, state["message"])
                return None

        startup_due = state.get("startup_due_monotonic")
        if startup_due is not None and now < float(startup_due):
            remaining = float(startup_due) - now
            state.update({
                "state": "startup_delay",
                "message": f"Warte auf Miner-Start: {_format_remaining(remaining)} verbleibend.",
            })
            _mark_waiting_for_socket(miner, state["message"])
            return None

        _reset_startup(state)
        state.update({"state": "running", "message": "Steckdose bereit.", "last_error": ""})
        return await original_set_profile(profile)

    # Desired profile is off. Always let the miner stop/pause first, then start
    # or continue the conservative socket-off timer.
    result = await original_set_profile(profile)
    _reset_startup(state)

    delay = max(assignment.MIN_POWER_OFF_AFTER_OFF_SECONDS, int(socket_cfg.get("power_off_after_off_seconds") or assignment.DEFAULT_POWER_OFF_AFTER_OFF_SECONDS))
    if state.get("off_since_monotonic") is None:
        state.update({
            "state": "off_timer",
            "message": f"Miner ist off; Socket-Off in {_format_remaining(delay)}.",
            "off_since": _now_iso(),
            "off_since_monotonic": now,
            "power_off_due_at": datetime.fromtimestamp(time.time() + delay, UTC).isoformat(),
            "power_off_due_monotonic": now + delay,
            "power_off_after_off_seconds": delay,
            "last_error": "",
        })
        return result

    due = float(state.get("power_off_due_monotonic") or (now + delay))
    if now < due:
        remaining = due - now
        state.update({
            "state": "off_timer",
            "message": f"Socket-Off in {_format_remaining(remaining)}.",
        })
        return result

    try:
        socket_info = socket_adapter.get_status()
    except Exception as exc:
        msg = f"Steckdose konnte für Socket-Off nicht gelesen werden: {exc}"
        state.update({"state": "socket_error", "message": msg, "last_error": msg})
        return result

    if not bool(getattr(socket_info, "reachable", False)):
        msg = "Steckdose ist für Socket-Off nicht erreichbar."
        state.update({"state": "socket_unreachable", "message": msg, "last_error": msg})
        return result

    if getattr(socket_info, "is_on", None) is True:
        try:
            off_result = socket_adapter.switch_off()
        except Exception as exc:
            off_result = {"ok": False, "message": str(exc)}
        if not off_result or not off_result.get("ok"):
            msg = str((off_result or {}).get("message") or "Steckdose konnte nicht ausgeschaltet werden.")
            state.update({"state": "socket_off_failed", "message": msg, "last_error": msg})
            return result
        state.update({
            "state": "socket_off",
            "message": "Steckdose nach stabiler Off-Zeit ausgeschaltet.",
            "last_socket_action": "off",
            "last_socket_action_at": _now_iso(),
            "last_error": "",
        })
    else:
        state.update({"state": "socket_off", "message": "Steckdose ist aus.", "last_error": ""})
    return result


def _wrap_miner_adapter(app_mod: Any, miner: Any) -> None:
    if getattr(miner, "_pv2hash_socket_workflow_wrapped", False):
        return
    original_set_profile = miner.set_profile

    async def set_profile_with_socket(profile: str):
        return await _handle_profile_with_socket(
            app_mod=app_mod,
            miner=miner,
            original_set_profile=original_set_profile,
            profile=str(profile or "off"),
        )

    miner.set_profile = set_profile_with_socket
    miner._pv2hash_socket_workflow_wrapped = True


def _wrap_runtime_miners(app_mod: Any) -> None:
    for miner in list(getattr(app_mod.services, "miners", []) or []):
        _wrap_miner_adapter(app_mod, miner)


def install(app_mod: Any) -> None:
    if getattr(app_mod, "_pv2hash_socket_miner_workflow_patched", False):
        return

    original_reload_runtime = app_mod.reload_runtime
    original_build_runtime_snapshot_payload = app_mod._build_runtime_snapshot_payload

    def reload_runtime_with_socket_workflow() -> None:
        result = original_reload_runtime()
        _wrap_runtime_miners(app_mod)
        return result

    def build_runtime_snapshot_payload():
        payload = original_build_runtime_snapshot_payload()
        for miner_item in payload.get("miners") or []:
            if not isinstance(miner_item, dict):
                continue
            miner_id = str(miner_item.get("key") or "")
            _decorate_workflow_payload(miner_item, _workflow_state(miner_id))
        return payload

    app_mod.reload_runtime = reload_runtime_with_socket_workflow
    app_mod._build_runtime_snapshot_payload = build_runtime_snapshot_payload
    _wrap_runtime_miners(app_mod)
    app_mod._pv2hash_socket_miner_workflow_patched = True
