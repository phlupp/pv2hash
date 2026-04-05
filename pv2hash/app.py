from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, Form, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pv2hash.config.store import load_config, save_config
from pv2hash.logging_ext.setup import (
    get_log_file_path,
    get_logger,
    get_ringbuffer_lines,
    setup_logging,
)
from pv2hash.runtime import AppState
from pv2hash.self_update import SelfUpdateManager
from pv2hash.services import RuntimeServices
from pv2hash.update_check import UpdateChecker
from pv2hash.version import APP_BUILD, APP_VERSION, APP_VERSION_FULL

initial_config = load_config()
setup_logging(initial_config.get("system", {}).get("log_level", "INFO"))
logger = get_logger("pv2hash.app")

app = FastAPI(title="PV2Hash", version=APP_VERSION_FULL)
app.mount("/static", StaticFiles(directory="pv2hash/static"), name="static")
templates = Jinja2Templates(directory="pv2hash/templates")

state = AppState(config=initial_config)
services = RuntimeServices(state)
services.reload_from_config()
update_checker = UpdateChecker(
    state,
    current_version=APP_VERSION,
    current_build=APP_BUILD,
)
self_update_manager = SelfUpdateManager(
    current_version=APP_VERSION,
    current_build=APP_BUILD,
)

EDITABLE_PROFILE_NAMES = ("p1", "p2", "p3", "p4")
ALL_PROFILE_NAMES = ("off", "p1", "p2", "p3", "p4")
EDITABLE_MIN_REGULATED_PROFILE_NAMES = ("off", "p1", "p2", "p3", "p4")


def _driver_profile_defaults(driver: str) -> tuple[int, int, int, int, int]:
    if driver == "braiins":
        return 50051, 1200, 2200, 3200, 4200
    return 4028, 900, 1800, 3000, 4200


def _normalize_min_regulated_profile(value: str | None, default: str = "off") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in EDITABLE_MIN_REGULATED_PROFILE_NAMES:
        return default
    return normalized


def _normalize_fallback_profile(value: str | None, default: str = "p1") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in ALL_PROFILE_NAMES:
        return default
    return normalized


def _get_runtime_miner_map() -> dict[str, dict]:
    return {miner.id: asdict(miner) for miner in state.miners}


def _build_miners_view() -> list[dict]:
    runtime_map = _get_runtime_miner_map()
    miner_views: list[dict] = []

    for miner_cfg in state.config.get("miners", []):
        merged = deepcopy(miner_cfg)
        runtime = runtime_map.get(miner_cfg.get("id"), {})
        merged["runtime"] = runtime
        merged.setdefault("settings", {})
        merged.setdefault("profiles", {})
        merged.setdefault("min_regulated_profile", "off")
        miner_views.append(merged)

    return miner_views


def _miners_context(request: Request, *, error_message: str | None = None) -> dict:
    return {
        "request": request,
        "miners": _build_miners_view(),
        "saved": request.query_params.get("saved") == "1",
        "error_message": error_message,
    }


def _build_miner_settings(
    form,
    driver: str,
    default_port: int,
    existing: dict | None = None,
) -> dict:
    existing = existing or {}

    settings = {
        "port": int(form.get("port", existing.get("port", default_port))),
    }

    if driver == "braiins":
        username = str(form.get("username", existing.get("username") or "")).strip()
        password_raw = str(form.get("password", "")).strip()

        if username:
            settings["username"] = username
        elif existing.get("username"):
            settings["username"] = existing["username"]

        if password_raw:
            settings["password"] = password_raw
        elif existing.get("password"):
            settings["password"] = existing["password"]

    return settings

def _parse_profile_values(form, driver: str) -> dict[str, dict[str, float]]:
    _, default_p1, default_p2, default_p3, default_p4 = _driver_profile_defaults(driver)

    return {
        "p1": {"power_w": float(form.get("profile_p1_power_w", default_p1))},
        "p2": {"power_w": float(form.get("profile_p2_power_w", default_p2))},
        "p3": {"power_w": float(form.get("profile_p3_power_w", default_p3))},
        "p4": {"power_w": float(form.get("profile_p4_power_w", default_p4))},
    }

def _validate_profile_values(
    *,
    profile_values: dict[str, dict[str, float]],
    runtime_constraints: dict | None = None,
) -> str | None:
    values = {name: float(profile_values[name]["power_w"]) for name in EDITABLE_PROFILE_NAMES}

    for name, value in values.items():
        if value <= 0:
            return f"Profil {name} muss größer als 0 W sein."

    if not (values["p1"] <= values["p2"] <= values["p3"] <= values["p4"]):
        return "Profile müssen in aufsteigender Reihenfolge liegen: p1 ≤ p2 ≤ p3 ≤ p4."

    if runtime_constraints:
        min_power = runtime_constraints.get("power_target_min_w")
        max_power = runtime_constraints.get("power_target_max_w")

        if min_power is not None:
            for name in EDITABLE_PROFILE_NAMES:
                if values[name] < float(min_power):
                    return (
                        f"{name} liegt unter dem vom Miner gemeldeten Minimum "
                        f"von {float(min_power):.0f} W."
                    )

        if max_power is not None:
            for name in EDITABLE_PROFILE_NAMES:
                if values[name] > float(max_power):
                    return (
                        f"{name} liegt über dem vom Miner gemeldeten Maximum "
                        f"von {float(max_power):.0f} W."
                    )

    return None


async def control_loop() -> None:
    logger.info("Control loop started")

    while True:
        refresh_seconds = state.config["app"].get("refresh_seconds", 5)

        try:
            generation = services.reload_generation
            source = services.source
            controller = services.controller
            miners = list(services.miners)
            distribution_mode = state.config["control"].get("distribution_mode", "equal")

            current_total_miner_power_w = (
                sum(m.power_w for m in state.miners) if state.miners else 0.0
            )

            if hasattr(source, "set_simulated_miner_power_w"):
                source.set_simulated_miner_power_w(current_total_miner_power_w)

            snapshot = await source.read()

            if generation != services.reload_generation or source is not services.source:
                logger.info("Discarding stale control iteration after runtime reload")
                await asyncio.sleep(0.1)
                continue

            source_last_live_packet_at = getattr(source, "last_live_packet_at", None)
            state.last_live_packet_at = source_last_live_packet_at

            decision = controller.decide(
                snapshot=snapshot,
                miners=miners,
                distribution_mode=distribution_mode,
            )

            for miner, profile in zip(miners, decision.profiles):
                await miner.set_profile(profile)

            miner_states = [await miner.get_status() for miner in miners]

            if generation != services.reload_generation:
                logger.info("Discarding state update after runtime reload")
                await asyncio.sleep(0.1)
                continue

            state.snapshot = snapshot
            state.miners = miner_states
            state.last_decision = decision.summary

        except Exception:
            logger.exception("Unhandled error in control loop")

        await asyncio.sleep(refresh_seconds)


@app.on_event("startup")
async def startup_event() -> None:
    logger.info("Application startup complete")
    asyncio.create_task(control_loop())
    asyncio.create_task(update_checker.run_background_loop())


def reload_runtime() -> None:
    logger.info("Reloading runtime")
    services.reload_from_config()
    state.snapshot = None
    state.miners = []
    state.last_decision = None
    state.last_live_packet_at = None
    state.last_reload_at = datetime.now(UTC)


@app.get("/")
async def dashboard(request: Request):
    total_miner_power = sum(m.power_w for m in state.miners) if state.miners else 0.0
    miner_count = len(state.miners)

    context = {
        "request": request,
        "snapshot": state.snapshot,
        "miners": state.miners,
        "total_miner_power": total_miner_power,
        "miner_count": miner_count,
        "policy_mode": state.config["control"]["policy_mode"],
        "distribution_mode": state.config["control"]["distribution_mode"],
        "started_at": state.started_at,
        "instance_name": state.config["system"]["instance_name"],
        "last_decision": state.last_decision,
        "last_reload_at": state.last_reload_at,
        "source_reloaded_at": state.source_reloaded_at,
        "last_live_packet_at": state.last_live_packet_at,
        "source_debug": services.get_source_debug_info(),
        "app_version_full": APP_VERSION_FULL,
        "update_check": update_checker.snapshot(),
    }

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=context,
    )


@app.get("/settings")
async def settings_page(request: Request):
    context = {
        "request": request,
        "config": state.config,
        "saved": request.query_params.get("saved") == "1",
    }
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=context,
    )


@app.post("/settings")
async def save_settings(request: Request):
    form = await request.form()

    state.config["system"]["instance_name"] = form.get("instance_name", "PV2Hash Node")
    state.config["system"]["check_updates"] = form.get("check_updates") == "on"
    state.config["system"]["auto_update_enabled"] = form.get("auto_update_enabled") == "on"
    state.config["system"]["update_repo"] = (
        str(form.get("update_repo", "phlupp/pv2hash")).strip() or "phlupp/pv2hash"
    )
    state.config["app"]["refresh_seconds"] = int(form.get("refresh_seconds", 5))
    state.config["control"]["policy_mode"] = form.get("policy_mode", "coarse")
    state.config["control"]["distribution_mode"] = form.get("distribution_mode", "equal")
    state.config["control"]["switch_hysteresis_w"] = int(form.get("switch_hysteresis_w", 100))
    state.config["control"]["min_switch_interval_seconds"] = int(
        form.get("min_switch_interval_seconds", 60)
    )
    state.config["control"]["max_import_w"] = int(form.get("max_import_w", 200))
    state.config["control"]["import_hold_seconds"] = int(form.get("import_hold_seconds", 15))

    state.config["control"].setdefault("source_loss", {})
    state.config["control"]["source_loss"]["stale"] = {
        "mode": form.get("stale_mode", "hold_current"),
        "fallback_profile": _normalize_fallback_profile(
            form.get("stale_fallback_profile", "p1")
        ),
        "hold_seconds": int(form.get("stale_hold_seconds", 0)),
    }
    state.config["control"]["source_loss"]["offline"] = {
        "mode": form.get("offline_mode", "off_all"),
        "fallback_profile": _normalize_fallback_profile(
            form.get("offline_fallback_profile", "p1")
        ),
        "hold_seconds": int(form.get("offline_hold_seconds", 0)),
    }

    save_config(state.config)
    logger.info(
        "Settings saved: check_updates=%s auto_update_enabled=%s update_repo=%s",
        state.config["system"].get("check_updates", True),
        state.config["system"].get("auto_update_enabled", False),
        state.config["system"].get("update_repo", "phlupp/pv2hash"),
    )
    reload_runtime()
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@app.get("/sources")
async def sources_page(request: Request):
    from pv2hash.netutils import get_local_ipv4_addresses

    context = {
        "request": request,
        "source": state.config["source"],
        "saved": request.query_params.get("saved") == "1",
        "local_interface_ips": get_local_ipv4_addresses(),
    }
    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context=context,
    )


@app.post("/sources")
async def save_source(request: Request):
    form = await request.form()

    source_type = form.get("source_type", "simulator")
    source_name = form.get("source_name", "Source")
    source_enabled = form.get("source_enabled") == "on"

    state.config["source"]["type"] = source_type
    state.config["source"]["name"] = source_name
    state.config["source"]["enabled"] = source_enabled
    state.config["source"].setdefault("settings", {})
    state.config["source"]["settings"]["multicast_ip"] = form.get(
        "multicast_ip", "239.12.255.254"
    )
    state.config["source"]["settings"]["bind_port"] = int(form.get("bind_port", 9522))
    state.config["source"]["settings"]["interface_ip"] = (
        form.get("interface_ip", "0.0.0.0").strip() or "0.0.0.0"
    )
    state.config["source"]["settings"]["packet_timeout_seconds"] = float(
        form.get("packet_timeout_seconds", 1.0)
    )
    state.config["source"]["settings"]["stale_after_seconds"] = float(
        form.get("stale_after_seconds", 8.0)
    )
    state.config["source"]["settings"]["offline_after_seconds"] = float(
        form.get("offline_after_seconds", 30.0)
    )
    state.config["source"]["settings"]["device_ip"] = form.get("device_ip", "").strip()

    save_config(state.config)
    logger.info("Source settings saved: type=%s name=%s", source_type, source_name)
    reload_runtime()
    return RedirectResponse(url="/sources?saved=1", status_code=303)


@app.get("/miners")
async def miners_page(request: Request):
    context = _miners_context(request)
    return templates.TemplateResponse(
        request=request,
        name="miners.html",
        context=context,
    )


@app.post("/miners/add")
async def add_miner(request: Request):
    form = await request.form()

    miner_id = f"m-{uuid4().hex[:8]}"
    driver = form.get("driver", "simulator")
    name = form.get("name", "Miner")
    host = form.get("host", "")
    min_regulated_profile = _normalize_min_regulated_profile(
        form.get("min_regulated_profile", "off")
    )

    default_port, _, _, _, _ = _driver_profile_defaults(driver)
    profile_values = _parse_profile_values(form, driver)

    validation_error = _validate_profile_values(profile_values=profile_values)
    if validation_error:
        return templates.TemplateResponse(
            request=request,
            name="miners.html",
            context=_miners_context(request, error_message=validation_error),
            status_code=400,
        )

    state.config.setdefault("miners", []).append(
        {
            "id": miner_id,
            "name": name,
            "host": host,
            "driver": driver,
            "enabled": True,
            "priority": int(form.get("priority", 100)),
            "serial_number": form.get("serial_number") or None,
            "model": form.get("model") or None,
            "firmware_version": form.get("firmware_version") or None,
            "settings": _build_miner_settings(form, driver, default_port),
            "profiles": profile_values,
            "min_regulated_profile": min_regulated_profile,
        }
    )

    save_config(state.config)
    logger.info("Miner added: id=%s name=%s driver=%s host=%s", miner_id, name, driver, host)
    reload_runtime()
    return RedirectResponse(url="/miners?saved=1", status_code=303)


@app.post("/miners/update")
async def update_miner(request: Request):
    form = await request.form()
    miner_id = form.get("miner_id")
    runtime_map = _get_runtime_miner_map()

    for miner in state.config.get("miners", []):
        if miner["id"] == miner_id:
            driver = form.get("driver", miner["driver"])
            min_regulated_profile = _normalize_min_regulated_profile(
                form.get("min_regulated_profile", miner.get("min_regulated_profile", "off"))
            )

            default_port, _, _, _, _ = _driver_profile_defaults(driver)
            profile_values = _parse_profile_values(form, driver)

            validation_error = _validate_profile_values(
                profile_values=profile_values,
                runtime_constraints=runtime_map.get(miner_id),
            )
            if validation_error:
                return templates.TemplateResponse(
                    request=request,
                    name="miners.html",
                    context=_miners_context(request, error_message=validation_error),
                    status_code=400,
                )

            miner["name"] = form.get("name", miner["name"])
            miner["host"] = form.get("host", miner["host"])
            miner["driver"] = driver
            miner["priority"] = int(form.get("priority", miner.get("priority", 100)))
            miner["enabled"] = form.get("enabled") == "on"
            miner["serial_number"] = form.get("serial_number") or None
            miner["model"] = form.get("model") or None
            miner["firmware_version"] = form.get("firmware_version") or None
            miner["settings"] = _build_miner_settings(
                form,
                driver,
                default_port,
                existing=miner.get("settings", {}),
            )
            miner["profiles"] = profile_values
            miner["min_regulated_profile"] = min_regulated_profile

            logger.info(
                "Miner updated: id=%s name=%s driver=%s host=%s enabled=%s min_regulated_profile=%s",
                miner["id"],
                miner["name"],
                miner["driver"],
                miner["host"],
                miner["enabled"],
                miner["min_regulated_profile"],
            )
            break

    save_config(state.config)
    reload_runtime()
    return RedirectResponse(url="/miners?saved=1", status_code=303)


@app.post("/miners/delete")
async def delete_miner(miner_id: str = Form(...)):
    logger.info("Deleting miner: id=%s", miner_id)
    state.config["miners"] = [
        miner for miner in state.config.get("miners", []) if miner["id"] != miner_id
    ]
    save_config(state.config)
    reload_runtime()
    return RedirectResponse(url="/miners?saved=1", status_code=303)


@app.get("/system")
async def system_page(request: Request):
    update_status = update_checker.snapshot()
    auto_update_enabled = bool(state.config["system"].get("auto_update_enabled", False))

    context = {
        "request": request,
        "instance_name": state.config["system"]["instance_name"],
        "log_level": state.config["system"].get("log_level", "INFO"),
        "started_at": state.started_at,
        "last_reload_at": state.last_reload_at,
        "last_live_packet_at": state.last_live_packet_at,
        "source_type": state.config["source"].get("type", "unknown"),
        "miner_count": len(state.miners),
        "app_version": APP_VERSION,
        "app_build": APP_BUILD,
        "app_version_full": APP_VERSION_FULL,
        "update_status": update_status,
        "self_update_status": self_update_manager.snapshot(
            auto_update_enabled=auto_update_enabled,
            update_status=update_status,
        ),
    }
    return templates.TemplateResponse(
        request=request,
        name="system.html",
        context=context,
    )


@app.get("/system/update-progress")
async def system_update_progress_page(request: Request):
    update_status = update_checker.snapshot()
    auto_update_enabled = bool(state.config["system"].get("auto_update_enabled", False))

    context = {
        "request": request,
        "instance_name": state.config["system"]["instance_name"],
        "app_version_full": APP_VERSION_FULL,
        "update_status": update_status,
        "self_update_status": self_update_manager.snapshot(
            auto_update_enabled=auto_update_enabled,
            update_status=update_status,
        ),
    }
    return templates.TemplateResponse(
        request=request,
        name="update_progress.html",
        context=context,
    )


@app.post("/system/reload")
async def system_reload():
    logger.info("Manual system reload triggered from UI")
    reload_runtime()
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/system/update-status")
async def api_system_update_status():
    return JSONResponse(content=jsonable_encoder(update_checker.snapshot()))


@app.post("/api/system/update-check")
async def api_system_update_check():
    payload = await update_checker.refresh()
    return JSONResponse(content=jsonable_encoder(payload))


@app.get("/api/system/self-update-status")
async def api_system_self_update_status():
    update_status = update_checker.snapshot()
    payload = self_update_manager.snapshot(
        auto_update_enabled=bool(state.config["system"].get("auto_update_enabled", False)),
        update_status=update_status,
    )
    return JSONResponse(content=jsonable_encoder(payload))


@app.post("/api/system/self-update")
async def api_system_self_update():
    update_status = await update_checker.refresh()
    payload, status_code = self_update_manager.start_latest(
        auto_update_enabled=bool(state.config["system"].get("auto_update_enabled", False)),
        update_status=update_status,
    )
    response_payload = dict(payload)
    response_payload["progress_url"] = "/system/update-progress"
    return JSONResponse(content=jsonable_encoder(response_payload), status_code=status_code)


@app.get("/api/status")
async def api_status():
    payload = {
        "snapshot": asdict(state.snapshot) if state.snapshot else None,
        "miners": [asdict(m) for m in state.miners],
        "config": state.config,
        "started_at": state.started_at.isoformat(),
        "last_decision": state.last_decision,
        "last_reload_at": state.last_reload_at.isoformat(),
        "source_debug": services.get_source_debug_info(),
    }
    return JSONResponse(content=jsonable_encoder(payload))


@app.get("/api/config")
async def api_config():
    return JSONResponse(state.config)


@app.get("/api/logs")
async def api_logs():
    return JSONResponse({"lines": get_ringbuffer_lines()})


@app.get("/api/logs/download")
async def api_logs_download():
    log_file = get_log_file_path()
    return FileResponse(
        path=log_file,
        filename="pv2hash.log",
        media_type="text/plain",
    )