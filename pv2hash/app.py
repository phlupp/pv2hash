import asyncio
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pv2hash.config.store import load_config, save_config
from pv2hash.controller.basic import BasicController
from pv2hash.factory import build_miners, build_source
from pv2hash.runtime import AppState


app = FastAPI(title="PV2Hash")

app.mount("/static", StaticFiles(directory="pv2hash/static"), name="static")
templates = Jinja2Templates(directory="pv2hash/templates")

config = load_config()
state = AppState(config=config)

source = build_source(config)
miners = build_miners(config)
controller = BasicController(config["control"])


async def control_loop() -> None:
    refresh_seconds = state.config["app"].get("refresh_seconds", 5)

    while True:
        snapshot = await source.read()
        target_profile = controller.decide_profile(snapshot.grid_power_w)

        for miner in miners:
            await miner.set_profile(target_profile)

        miner_states = [await miner.get_status() for miner in miners]

        state.snapshot = snapshot
        state.miners = miner_states

        await asyncio.sleep(refresh_seconds)


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(control_loop())


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

    return RedirectResponse(url="/settings", status_code=303)


@app.get("/api/status")
async def api_status():
    payload = {
        "snapshot": asdict(state.snapshot) if state.snapshot else None,
        "miners": [asdict(m) for m in state.miners],
        "config": state.config,
        "started_at": state.started_at.isoformat(),
    }
    return JSONResponse(payload)


@app.get("/api/config")
async def api_config():
    return JSONResponse(state.config)
