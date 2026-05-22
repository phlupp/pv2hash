from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
from typing import Any

_INSTALLED = False
_APP_HOOK_INSTALLED = False


def _remove_route(app: Any, path: str, method: str) -> None:
    method = method.upper()
    kept = []
    for route in list(getattr(app.router, "routes", [])):
        methods = {str(item).upper() for item in getattr(route, "methods", set()) or set()}
        if getattr(route, "path", None) == path and method in methods:
            continue
        kept.append(route)
    app.router.routes = kept


def _patch_location_settings_model(app_mod: Any) -> None:
    if getattr(app_mod, "_pv2hash_location_settings_model_patched", False):
        return
    original_build_settings_model = app_mod._build_settings_model

    def build_settings_model():
        model = original_build_settings_model()
        for section in model.get("sections", []) or []:
            if section.get("id") == "portal-location":
                section["id"] = "instance-location"
                section["title"] = "Standort"
                section["subtitle"] = (
                    "Optionaler Standort dieser lokalen PV2Hash-Instanz. "
                    "Koordinaten werden nur bei aktiver Portal-Synchronisierung übertragen."
                )
                for field in section.get("fields", []) or []:
                    if field.get("name") == "portal_location_address":
                        field["label"] = "Adresse"
                        field["help"] = (
                            "Adresse der lokalen Instanz. Koordinaten können manuell eingetragen "
                            "oder über den Button aus der Adresse ermittelt werden."
                        )
                break
        return model

    app_mod._build_settings_model = build_settings_model
    app_mod._pv2hash_location_settings_model_patched = True


def _patch_app_module(app_mod: Any) -> None:
    if getattr(app_mod, "_pv2hash_local_instance_runtime_patched", False):
        return

    # Install the existing extension first. It provides the DataLogger queue,
    # SoC sanitizing and settings-model additions. We then replace only the
    # geocode route because the first implementation used a postponed annotation
    # that FastAPI interpreted as a query parameter.
    from pv2hash import local_instance_extensions as ext
    from pv2hash import socket_miner_assignment
    import pv2hash.portal as portal_mod

    ext._patch_app_module(app_mod)

    # app.py imports send_snapshot directly at module import time:
    #   from pv2hash.portal import ..., send_snapshot, ...
    # Patching pv2hash.portal.send_snapshot alone is therefore not enough. The
    # global function reference inside app.py must be rebound as well, otherwise
    # the real portal upload bypasses the DataLogger queue wrapper.
    app_mod.send_snapshot = portal_mod.send_snapshot

    _patch_location_settings_model(app_mod)
    socket_miner_assignment.install(app_mod)

    _remove_route(app_mod.app, "/api/portal/location/geocode", "POST")

    async def api_portal_location_geocode(request: app_mod.Request):  # noqa: ANN001 - real FastAPI Request object from app.py
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        portal = app_mod._portal_config()
        current_location = portal.get("location", {}) if isinstance(portal.get("location"), dict) else {}
        address = str(payload.get("address") or current_location.get("address") or "").strip()
        save = bool(payload.get("save", False))
        try:
            result = await app_mod.asyncio.to_thread(ext._geocode_address, address)
        except Exception as exc:
            return app_mod.JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
        if save:
            portal["location"] = {
                "address": address,
                "lat": result["lat"],
                "lon": result["lon"],
                "source": result.get("source") or "nominatim",
            }
            app_mod.save_config(app_mod.state.config)
        return app_mod.JSONResponse(app_mod.jsonable_encoder({
            "status": "ok",
            "message": "Koordinaten wurden ermittelt." if not save else "Koordinaten wurden ermittelt und gespeichert.",
            "location": result,
            "model": app_mod._build_settings_model(),
        }))

    api_portal_location_geocode.__annotations__["request"] = app_mod.Request
    app_mod.app.post("/api/portal/location/geocode")(api_portal_location_geocode)
    app_mod._pv2hash_local_instance_runtime_patched = True


class _AppPatchLoader(importlib.abc.Loader):
    def __init__(self, original_loader: importlib.abc.Loader) -> None:
        self.original_loader = original_loader

    def create_module(self, spec):
        create_module = getattr(self.original_loader, "create_module", None)
        if create_module:
            return create_module(spec)
        return None

    def exec_module(self, module):
        self.original_loader.exec_module(module)
        _patch_app_module(module)


class _AppPatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "pv2hash.app":
            return None
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None:
            return spec
        spec.loader = _AppPatchLoader(spec.loader)
        return spec


def _install_app_import_hook() -> None:
    global _APP_HOOK_INSTALLED
    if _APP_HOOK_INSTALLED:
        return
    if "pv2hash.app" in sys.modules:
        _patch_app_module(sys.modules["pv2hash.app"])
        _APP_HOOK_INSTALLED = True
        return
    sys.meta_path.insert(0, _AppPatchFinder())
    _APP_HOOK_INSTALLED = True


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    _install_app_import_hook()
