from __future__ import annotations

from copy import deepcopy
from typing import Any

REAL_MINER_SOCKET_DRIVERS = {"tasmota_http"}
SOCKET_MODES = {"measure_only", "switching"}
DEFAULT_STARTUP_DELAY_SECONDS = 60
MIN_POWER_OFF_AFTER_OFF_SECONDS = 600
DEFAULT_POWER_OFF_AFTER_OFF_SECONDS = 3600


def _safe_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _normalize_mode(value: Any) -> str:
    mode = str(value or "measure_only").strip().lower()
    return mode if mode in SOCKET_MODES else "measure_only"


def _normalize_miner_socket_config(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(raw or {})
    socket_id = str(raw.get("socket_id") or "").strip()
    mode = _normalize_mode(raw.get("mode"))
    startup_delay = max(0, _safe_int(raw.get("startup_delay_seconds"), DEFAULT_STARTUP_DELAY_SECONDS))
    power_off_delay = max(
        MIN_POWER_OFF_AFTER_OFF_SECONDS,
        _safe_int(raw.get("power_off_after_off_seconds"), DEFAULT_POWER_OFF_AFTER_OFF_SECONDS),
    )
    return {
        "enabled": bool(socket_id),
        "socket_id": socket_id,
        "mode": mode,
        "startup_delay_seconds": startup_delay,
        "power_off_after_off_seconds": power_off_delay,
    }


def _socket_by_id(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id") or ""): item for item in config.get("sockets", []) or [] if item.get("id")}


def _miner_by_id(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id") or ""): item for item in config.get("miners", []) or [] if item.get("id")}


def _is_real_miner_socket(socket_cfg: dict[str, Any] | None) -> bool:
    if not socket_cfg:
        return False
    return str(socket_cfg.get("driver") or "").strip().lower() in REAL_MINER_SOCKET_DRIVERS


def _socket_assignment(socket_cfg: dict[str, Any] | None) -> dict[str, Any]:
    if not socket_cfg:
        return {}
    assignment = socket_cfg.get("assignment")
    return assignment if isinstance(assignment, dict) else {}


def _socket_assigned_miner_id(socket_cfg: dict[str, Any] | None) -> str:
    assignment = _socket_assignment(socket_cfg)
    if str(assignment.get("role") or "").strip().lower() != "miner":
        return ""
    return str(assignment.get("target_id") or "").strip()


def _assigned_socket_id_for_miner(miner_cfg: dict[str, Any] | None) -> str:
    if not miner_cfg:
        return ""
    socket_cfg = _normalize_miner_socket_config(miner_cfg.get("socket") if isinstance(miner_cfg.get("socket"), dict) else {})
    return socket_cfg.get("socket_id") or ""


def sync_socket_assignments(config: dict[str, Any]) -> None:
    """Synchronize reverse socket.assignment metadata from miner.socket config.

    The miner config is the leading configuration. Socket assignment metadata is
    mirrored so the sockets page can clearly show that a socket is reserved for a
    miner and should not be used for a later consumer automation.
    """
    sockets = config.get("sockets", []) or []
    miners = config.get("miners", []) or []
    socket_ids = {str(item.get("id") or "") for item in sockets}
    miner_socket_map: dict[str, str] = {}

    for miner in miners:
        miner_id = str(miner.get("id") or "").strip()
        if not miner_id:
            continue
        normalized = _normalize_miner_socket_config(miner.get("socket") if isinstance(miner.get("socket"), dict) else {})
        socket_id = str(normalized.get("socket_id") or "").strip()
        if socket_id and socket_id in socket_ids:
            miner["socket"] = normalized
            miner_socket_map[miner_id] = socket_id
        else:
            if socket_id and socket_id not in socket_ids:
                normalized["enabled"] = False
                normalized["socket_id"] = ""
                miner["socket"] = normalized
            elif "socket" in miner:
                miner["socket"] = normalized

    for socket_cfg in sockets:
        assignment = _socket_assignment(socket_cfg)
        if str(assignment.get("role") or "").strip().lower() == "miner":
            target_id = str(assignment.get("target_id") or "").strip()
            if not target_id or miner_socket_map.get(target_id) != str(socket_cfg.get("id") or ""):
                socket_cfg.pop("assignment", None)

    for miner_id, socket_id in miner_socket_map.items():
        socket_cfg = next((item for item in sockets if str(item.get("id") or "") == socket_id), None)
        if not socket_cfg:
            continue
        socket_cfg["assignment"] = {"role": "miner", "target_id": miner_id}
        # A socket reserved for a miner must not be available for future
        # automatic consumer control at the same time.
        socket_cfg["control_enabled"] = False


def validate_socket_assignment(config: dict[str, Any], miner_id: str, socket_config: dict[str, Any]) -> str | None:
    socket_id = str(socket_config.get("socket_id") or "").strip()
    if not socket_id:
        return None

    sockets = _socket_by_id(config)
    socket_cfg = sockets.get(socket_id)
    if socket_cfg is None:
        return "Die ausgewählte Steckdose wurde nicht gefunden."
    if not _is_real_miner_socket(socket_cfg):
        return "Simulierte Sockets können Minern nicht als Stromversorgung zugewiesen werden."
    if bool(socket_cfg.get("control_enabled", False)):
        return "Diese Steckdose ist bereits für Automatikbetrieb vorgesehen und kann keinem Miner zugeordnet werden."

    assigned_miner_id = _socket_assigned_miner_id(socket_cfg)
    if assigned_miner_id and assigned_miner_id != miner_id:
        miner_name = (_miner_by_id(config).get(assigned_miner_id) or {}).get("name") or assigned_miner_id
        return f"Diese Steckdose ist bereits dem Miner ‚{miner_name}‘ zugeordnet."

    for miner in config.get("miners", []) or []:
        other_id = str(miner.get("id") or "")
        if not other_id or other_id == miner_id:
            continue
        if _assigned_socket_id_for_miner(miner) == socket_id:
            miner_name = str(miner.get("name") or other_id)
            return f"Diese Steckdose ist bereits dem Miner ‚{miner_name}‘ zugeordnet."

    return None


def _socket_label(socket_cfg: dict[str, Any]) -> str:
    name = str(socket_cfg.get("name") or socket_cfg.get("id") or "Socket")
    host = str(socket_cfg.get("host") or "").strip()
    if host:
        return f"{name} ({host})"
    return name


def _available_socket_options(config: dict[str, Any], miner_id: str, selected_socket_id: str) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for socket_cfg in sorted(config.get("sockets", []) or [], key=lambda item: (int(item.get("priority", 100) or 100), str(item.get("name") or ""))):
        socket_id = str(socket_cfg.get("id") or "").strip()
        if not socket_id:
            continue
        if not _is_real_miner_socket(socket_cfg):
            continue
        assigned_miner_id = _socket_assigned_miner_id(socket_cfg)
        assigned_to_other = assigned_miner_id and assigned_miner_id != miner_id
        automatik = bool(socket_cfg.get("control_enabled", False))
        if socket_id != selected_socket_id and (assigned_to_other or automatik):
            continue
        options.append({
            "id": socket_id,
            "label": _socket_label(socket_cfg),
            "driver": str(socket_cfg.get("driver") or ""),
            "host": str(socket_cfg.get("host") or ""),
            "selected": socket_id == selected_socket_id,
        })
    return options


def _runtime_socket_payload(runtime_sockets: list[dict[str, Any]], socket_id: str) -> dict[str, Any] | None:
    if not socket_id:
        return None
    for item in runtime_sockets or []:
        if str(item.get("key") or item.get("id") or "") == socket_id:
            return item
    return None


def _assignment_payload(config: dict[str, Any], socket_cfg: dict[str, Any]) -> dict[str, Any]:
    assignment = _socket_assignment(socket_cfg)
    if str(assignment.get("role") or "").strip().lower() != "miner":
        return {"role": "", "target_id": "", "target_name": "", "label": "Frei", "class": "neutral"}
    miner_id = str(assignment.get("target_id") or "").strip()
    miner = _miner_by_id(config).get(miner_id) or {}
    miner_name = str(miner.get("name") or miner_id or "Miner")
    return {
        "role": "miner",
        "target_id": miner_id,
        "target_name": miner_name,
        "label": f"Miner: {miner_name}",
        "class": "ok",
    }


def _decorate_miner_view(app_mod: Any, miner_view: dict[str, Any], runtime_sockets: list[dict[str, Any]]) -> None:
    miner_id = str(miner_view.get("id") or "")
    socket_config = _normalize_miner_socket_config(miner_view.get("socket") if isinstance(miner_view.get("socket"), dict) else {})
    selected_socket_id = str(socket_config.get("socket_id") or "")
    options = _available_socket_options(app_mod.state.config, miner_id, selected_socket_id)
    real_socket_count = sum(1 for item in app_mod.state.config.get("sockets", []) or [] if _is_real_miner_socket(item))
    runtime = _runtime_socket_payload(runtime_sockets, selected_socket_id)
    socket_cfg = _socket_by_id(app_mod.state.config).get(selected_socket_id)

    miner_view["socket"] = socket_config
    miner_view["socket_options"] = options
    miner_view["show_socket_power_section"] = bool(real_socket_count or selected_socket_id)
    miner_view["socket_runtime"] = runtime or {}
    miner_view["socket_name"] = str((socket_cfg or {}).get("name") or selected_socket_id or "")
    miner_view["socket_missing"] = bool(selected_socket_id and socket_cfg is None)
    miner_view["socket_mode_label"] = "Messen und schalten" if socket_config.get("mode") == "switching" else "Nur messen"


def _decorate_socket_view(app_mod: Any, socket_view: dict[str, Any]) -> None:
    socket_id = str(socket_view.get("id") or "")
    socket_cfg = _socket_by_id(app_mod.state.config).get(socket_id) or socket_view
    assignment = _assignment_payload(app_mod.state.config, socket_cfg)
    socket_view["assignment"] = assignment
    socket_view["is_reserved_for_miner"] = assignment.get("role") == "miner"
    if socket_view["is_reserved_for_miner"]:
        socket_view["control_enabled"] = False


def _decorate_runtime_payload(app_mod: Any, payload: dict[str, Any]) -> dict[str, Any]:
    sync_socket_assignments(app_mod.state.config)
    socket_payloads = payload.get("sockets") if isinstance(payload.get("sockets"), list) else []
    socket_cfgs = _socket_by_id(app_mod.state.config)

    for socket_item in socket_payloads:
        if not isinstance(socket_item, dict):
            continue
        socket_id = str(socket_item.get("key") or socket_item.get("id") or "")
        socket_cfg = socket_cfgs.get(socket_id)
        if socket_cfg:
            socket_item["assignment"] = _assignment_payload(app_mod.state.config, socket_cfg)

    for miner_item in payload.get("miners") or []:
        if not isinstance(miner_item, dict):
            continue
        miner_id = str(miner_item.get("key") or "")
        miner_cfg = _miner_by_id(app_mod.state.config).get(miner_id) or {}
        socket_config = _normalize_miner_socket_config(miner_cfg.get("socket") if isinstance(miner_cfg.get("socket"), dict) else {})
        socket_id = str(socket_config.get("socket_id") or "")
        socket_cfg = socket_cfgs.get(socket_id)
        runtime = _runtime_socket_payload(socket_payloads, socket_id)
        miner_item["socket"] = {
            **socket_config,
            "socket_name": str((socket_cfg or {}).get("name") or ""),
            "socket_driver": str((socket_cfg or {}).get("driver") or ""),
            "socket_host": str((socket_cfg or {}).get("host") or ""),
            "runtime": runtime or {},
            "missing": bool(socket_id and socket_cfg is None),
        }
    return payload


def _parse_socket_config_from_form(form: Any) -> dict[str, Any]:
    socket_id = str(form.get("socket.socket_id") or "").strip()
    mode = _normalize_mode(form.get("socket.mode"))
    startup_delay = max(0, _safe_int(form.get("socket.startup_delay_seconds"), DEFAULT_STARTUP_DELAY_SECONDS))
    power_off_delay = max(
        MIN_POWER_OFF_AFTER_OFF_SECONDS,
        _safe_int(form.get("socket.power_off_after_off_seconds"), DEFAULT_POWER_OFF_AFTER_OFF_SECONDS),
    )
    return _normalize_miner_socket_config({
        "socket_id": socket_id,
        "mode": mode,
        "startup_delay_seconds": startup_delay,
        "power_off_after_off_seconds": power_off_delay,
    })


def _apply_miner_socket_config(app_mod: Any, miner_id: str, socket_config: dict[str, Any]) -> None:
    for miner in app_mod.state.config.get("miners", []) or []:
        if str(miner.get("id") or "") != miner_id:
            continue
        miner["socket"] = _normalize_miner_socket_config(socket_config)
        break
    sync_socket_assignments(app_mod.state.config)


def install(app_mod: Any) -> None:
    if getattr(app_mod, "_pv2hash_socket_miner_assignment_patched", False):
        return

    original_build_miners_view = app_mod._build_miners_view
    original_build_sockets_view = app_mod._build_sockets_view
    original_build_runtime_snapshot_payload = app_mod._build_runtime_snapshot_payload
    original_update_miner_config_result = app_mod._update_miner_config_result

    def build_miners_view():
        sync_socket_assignments(app_mod.state.config)
        runtime_sockets = app_mod._build_socket_snapshot_items()
        views = original_build_miners_view()
        for view in views:
            _decorate_miner_view(app_mod, view, runtime_sockets)
        return views

    def build_sockets_view():
        sync_socket_assignments(app_mod.state.config)
        views = original_build_sockets_view()
        for view in views:
            _decorate_socket_view(app_mod, view)
        return views

    def build_runtime_snapshot_payload():
        return _decorate_runtime_payload(app_mod, original_build_runtime_snapshot_payload())

    async def update_miner_config_result(form: Any) -> dict[str, Any]:
        miner_id = str(form.get("miner_id", "")).strip()
        socket_config = _parse_socket_config_from_form(form)
        error = validate_socket_assignment(app_mod.state.config, miner_id, socket_config)
        if error:
            return {"status": "error", "message": error, "miner_id": miner_id}

        result = await original_update_miner_config_result(form)
        if result.get("status") != "ok":
            return result

        _apply_miner_socket_config(app_mod, miner_id, socket_config)
        app_mod.save_config(app_mod.state.config)
        app_mod.reload_runtime()
        result["message"] = "Miner-Konfiguration gespeichert."
        result["socket"] = socket_config
        return result

    app_mod._build_miners_view = build_miners_view
    app_mod._build_sockets_view = build_sockets_view
    app_mod._build_runtime_snapshot_payload = build_runtime_snapshot_payload
    app_mod._update_miner_config_result = update_miner_config_result

    # Re-register delete endpoints so stale reverse assignments are removed when
    # either side of the relation is deleted.
    def remove_route(path: str, method: str) -> None:
        method = method.upper()
        kept = []
        for route in list(getattr(app_mod.app.router, "routes", [])):
            methods = {str(item).upper() for item in getattr(route, "methods", set()) or set()}
            if getattr(route, "path", None) == path and method in methods:
                continue
            kept.append(route)
        app_mod.app.router.routes = kept

    remove_route("/api/miner/{miner_id}/delete", "POST")
    remove_route("/api/socket/{socket_id}/delete", "POST")

    @app_mod.app.post("/api/miner/{miner_id}/delete")
    async def api_delete_miner(miner_id: str):
        miner_id = str(miner_id).strip()
        before = len(app_mod.state.config.get("miners", []) or [])
        app_mod.state.config["miners"] = [
            miner for miner in app_mod.state.config.get("miners", []) or [] if miner.get("id") != miner_id
        ]
        if len(app_mod.state.config.get("miners", []) or []) == before:
            return app_mod.JSONResponse({"status": "error", "message": "Miner nicht gefunden."}, status_code=404)
        sync_socket_assignments(app_mod.state.config)
        app_mod.logger.info("Deleting miner: id=%s", miner_id)
        app_mod.save_config(app_mod.state.config)
        app_mod.reload_runtime()
        return app_mod.JSONResponse({"status": "ok", "message": "Miner gelöscht.", "miner_id": miner_id})

    @app_mod.app.post("/api/socket/{socket_id}/delete")
    async def api_delete_socket(socket_id: str):
        socket_id = str(socket_id).strip()
        before = len(app_mod.state.config.get("sockets", []) or [])
        app_mod.state.config["sockets"] = [
            socket_cfg for socket_cfg in app_mod.state.config.get("sockets", []) or [] if socket_cfg.get("id") != socket_id
        ]
        if len(app_mod.state.config.get("sockets", []) or []) == before:
            return app_mod.JSONResponse({"status": "error", "message": "Socket nicht gefunden."}, status_code=404)
        for miner in app_mod.state.config.get("miners", []) or []:
            socket_config = _normalize_miner_socket_config(miner.get("socket") if isinstance(miner.get("socket"), dict) else {})
            if socket_config.get("socket_id") == socket_id:
                socket_config["enabled"] = False
                socket_config["socket_id"] = ""
                miner["socket"] = socket_config
        sync_socket_assignments(app_mod.state.config)
        app_mod.save_config(app_mod.state.config)
        app_mod.reload_runtime()
        return app_mod.JSONResponse({"status": "ok", "message": "Socket gelöscht.", "socket_id": socket_id})

    app_mod._pv2hash_socket_miner_assignment_patched = True
