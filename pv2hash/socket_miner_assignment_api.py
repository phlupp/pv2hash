from __future__ import annotations

from typing import Any

from pv2hash import socket_miner_assignment as assignment


def install(app_mod: Any) -> None:
    if getattr(app_mod, "_pv2hash_socket_miner_assignment_api_patched", False):
        return

    @app_mod.app.get("/api/miners/socket-assignment/model")
    async def api_miners_socket_assignment_model():
        assignment.sync_socket_assignments(app_mod.state.config)
        runtime_sockets = app_mod._build_socket_snapshot_items()
        socket_cfgs = assignment._socket_by_id(app_mod.state.config)
        miner_items: dict[str, dict[str, Any]] = {}

        for miner in app_mod.state.config.get("miners", []) or []:
            miner_id = str(miner.get("id") or "")
            if not miner_id:
                continue
            socket_config = assignment._normalize_miner_socket_config(
                miner.get("socket") if isinstance(miner.get("socket"), dict) else {}
            )
            selected_socket_id = str(socket_config.get("socket_id") or "")
            socket_cfg = socket_cfgs.get(selected_socket_id)
            runtime = assignment._runtime_socket_payload(runtime_sockets, selected_socket_id)
            real_socket_count = sum(
                1 for item in app_mod.state.config.get("sockets", []) or [] if assignment._is_real_miner_socket(item)
            )
            miner_items[miner_id] = {
                "miner_id": miner_id,
                "miner_name": str(miner.get("name") or miner_id),
                "show_section": bool(real_socket_count or selected_socket_id),
                "socket": socket_config,
                "socket_name": str((socket_cfg or {}).get("name") or selected_socket_id or ""),
                "socket_missing": bool(selected_socket_id and socket_cfg is None),
                "options": assignment._available_socket_options(app_mod.state.config, miner_id, selected_socket_id),
                "runtime": runtime or {},
                "mode_options": [
                    {"value": "measure_only", "label": "Nur messen"},
                    {"value": "switching", "label": "Messen und schalten"},
                ],
                "min_power_off_after_off_seconds": assignment.MIN_POWER_OFF_AFTER_OFF_SECONDS,
            }

        socket_items: dict[str, dict[str, Any]] = {}
        for socket_cfg in app_mod.state.config.get("sockets", []) or []:
            socket_id = str(socket_cfg.get("id") or "")
            if not socket_id:
                continue
            socket_items[socket_id] = {
                "socket_id": socket_id,
                "assignment": assignment._assignment_payload(app_mod.state.config, socket_cfg),
                "is_real_miner_socket": assignment._is_real_miner_socket(socket_cfg),
                "is_reserved_for_miner": assignment._socket_assigned_miner_id(socket_cfg) != "",
            }

        return app_mod.JSONResponse(app_mod.jsonable_encoder({
            "status": "ok",
            "miners": miner_items,
            "sockets": socket_items,
        }))

    app_mod._pv2hash_socket_miner_assignment_api_patched = True
