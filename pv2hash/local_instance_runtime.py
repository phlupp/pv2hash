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


def _patch_app_module(app_mod: Any) -> None:
    if getattr(app_mod, "_pv2hash_local_instance_runtime_patched", False):
        return

    from pv2hash import local_instance_extensions as ext
    from pv2hash import socket_miner_assignment
    from pv2hash import socket_miner_assignment_api
    from pv2hash import socket_miner_workflow
    from pv2hash import spot_values

    ext._patch_app_module(app_mod)
    socket_miner_assignment.install(app_mod)
    socket_miner_assignment_api.install(app_mod)
    socket_miner_workflow.install(app_mod)
    spot_values.install(app_mod)

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
