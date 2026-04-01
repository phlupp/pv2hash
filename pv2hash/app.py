import asyncio
from dataclasses import asdict
from datetime import datetime, UTC
from uuid import uuid4

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pv2hash.config.store import load_config, save_config
from pv2hash.controller.distribution import apply_distribution
from pv2hash.runtime import AppState
from pv2hash.services import RuntimeServices


app = FastAPI(title="PV2Hash")

app.mount("/static", StaticFiles(directory="pv2hash/static"), name="static")
templates = Jinja2Templates(directory="pv2hash/templates")

state = AppState(config=load_config())
services = RuntimeServices(state)
services.reload_from_config()


async def control_loop() -> None:
    while True:
        refresh_seconds = state.config["app"].get("refresh_seconds", 5)

        snapshot = await services.source.read()
        target_profile = services.controller.decide_profile(snapshot.grid_power_w)

        profiles = apply_distribution(
            state.config["control"]["distribution_mode"],
            target_profile,
            services.miners,
        )

        for miner, profile in zip(services.miners, profiles):
            await miner.set_profile(profile)

        miner_states = [await miner.get_status() for miner in services.miners]

        state.snapshot = snapshot
        state.miners = miner_states
        state.last_decision = target_profile

        await asyncio.sleep(refresh_seconds)


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(control_loop())


def reload_runtime() -> None:
    services.reload_from_config()
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
    state.config["app"]["refresh_seconds"] = int(form.get("refresh_seconds", 5))
    state.config["control"]["policy_mode"] = form.get("policy_mode", "coarse")
    state.config["control"]["distribution_mode"] = form.get("distribution_mode", "equal")

    state.config["control"]["coarse_thresholds"]["eco"] = int(form.get("threshold_eco", -500))
    state.config["control"]["coarse_thresholds"]["mid"] = int(form.get("threshold_mid", -1500))
    state.config["control"]["coarse_thresholds"]["high"] = int(form.get("threshold_high", -2500))

    save_config(state.config)
    reload_runtime()

    return RedirectResponse(url="/settings", status_code=303)


@app.get("/sources")
async def sources_page(request: Request):
    context = {
        "request": request,
        "source": state.config["source"],
    }
    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context=context,
    )


@app.post("/sources")
async def save_source(
    source_type: str = Form(...),
    source_name: str = Form(...),
    source_enabled: str | None = Form(None),
):
    state.config["source"]["type"] = source_type
    state.config["source"]["name"] = source_name
    state.config["source"]["enabled"] = source_enabled == "on"

    save_config(state.config)
    reload_runtime()

    return RedirectResponse(url="/sources", status_code=303)


@app.get("/miners")
async def miners_page(request: Request):
    context = {
        "request": request,
        "miners": state.config.get("miners", []),
    }
    return templates.TemplateResponse(
        request=request,
        name="miners.html",
        context=context,
    )


@app.post("/miners/add")
async def add_miner(
    name: str = Form(...),
    host: str = Form(...),
    driver: str = Form(...),
    priority: int = Form(100),
):
    miner_id = f"m-{uuid4().hex[:8]}"

    state.config.setdefault("miners", []).append(
        {
            "id": miner_id,
            "name": name,
            "host": host,
            "driver": driver,
            "enabled": True,
            "priority": priority,
            "serial_number": None,
            "model": None,
            "firmware_version": None,
            "settings": {},
        }
    )

    save_config(state.config)
    reload_runtime()

    return RedirectResponse(url="/miners", status_code=303)


@app.post("/miners/update")
async def update_miner(
    miner_id: str = Form(...),
    name: str = Form(...),
    host: str = Form(...),
    driver: str = Form(...),
    priority: int = Form(...),
    enabled: str | None = Form(None),
):
    for miner in state.config.get("miners", []):
        if miner["id"] == miner_id:
            miner["name"] = name
            miner["host"] = host
            miner["driver"] = driver
            miner["priority"] = priority
            miner["enabled"] = enabled == "on"
            break

    save_config(state.config)
    reload_runtime()

    return RedirectResponse(url="/miners", status_code=303)


@app.post("/miners/delete")
async def delete_miner(
    miner_id: str = Form(...),
):
    state.config["miners"] = [
        miner for miner in state.config.get("miners", [])
        if miner["id"] != miner_id
    ]

    save_config(state.config)
    reload_runtime()

    return RedirectResponse(url="/miners", status_code=303)


@app.post("/system/reload")
async def system_reload():
    reload_runtime()
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/status")
async def api_status():
    payload = {
        "snapshot": asdict(state.snapshot) if state.snapshot else None,
        "miners": [asdict(m) for m in state.miners],
        "config": state.config,
        "started_at": state.started_at.isoformat(),
        "last_decision": state.last_decision,
        "last_reload_at": state.last_reload_at.isoformat(),
    }
    return JSONResponse(payload)


@app.get("/api/config")
async def api_config():
    return JSONResponse(state.config)
