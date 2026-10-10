"""Safe parsing of Stratum pool endpoints (no passwords or URL userinfo)."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


def pool_endpoint(value: Any, port: Any = None) -> tuple[str, int | None] | None:
    """Read host and TCP port from host, host:port or Stratum URL.

    URL userinfo (including embedded passwords) is deliberately discarded.
    A missing port remains unknown rather than inventing a default.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
        host = parsed.hostname
        if not host:
            return None
        actual_port = int(port) if port not in (None, "") else parsed.port
        if actual_port is not None and not 1 <= actual_port <= 65535:
            return None
        return host, actual_port
    except (TypeError, ValueError):
        return None


def explicit_bool(value: Any) -> bool | None:
    """Only explicit boolean-like API values prove an active status."""
    if isinstance(value, bool):
        return value
    if value in (0, 1, "0", "1"):
        return str(value) == "1"
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "false"):
            return v == "true"
    return None
