from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin


logger = logging.getLogger("pv2hash.portal")


class PortalError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.path = path


def describe_portal_error(exc: BaseException) -> str:
    if isinstance(exc, PortalError):
        parts: list[str] = []
        if exc.status_code is not None:
            parts.append(f"HTTP {exc.status_code}")
        if exc.code:
            parts.append(str(exc.code))
        if exc.path:
            parts.append(str(exc.path))
        parts.append(str(exc))
        return " | ".join(part for part in parts if part)
    return str(exc)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_base_url(value: str | None) -> str:
    text = str(value or "https://pv2hash.xyz").strip().rstrip("/")
    if not text:
        text = "https://pv2hash.xyz"
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    return text.rstrip("/")


def _api_url(base_url: str, path: str) -> str:
    base = normalize_base_url(base_url) + "/"
    return urljoin(base, path.lstrip("/"))


def _decode_error_body(raw: bytes) -> tuple[str | None, str | None]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, None
    if not isinstance(data, dict):
        return None, None
    code = data.get("code") or data.get("detail") or data.get("error")
    message = data.get("message") or data.get("detail") or data.get("error")
    return (str(code) if code else None, str(message) if message else None)


def post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    bearer_token: str | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    url = _api_url(base_url, path)
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=ssl.create_default_context()) as response:
            response_body = response.read()
            if not response_body:
                return {}
            decoded = json.loads(response_body.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise PortalError("Portal returned an unexpected JSON response.", path=path)
            logger.debug("Portal request succeeded: method=POST path=%s status=%s", path, getattr(response, "status", "?"))
            return decoded
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        code, message = _decode_error_body(raw)
        text = message or code or f"Portal request failed with HTTP {exc.code}."
        portal_error = PortalError(text, status_code=exc.code, code=code, path=path)
        logger.warning(
            "Portal request failed: method=POST path=%s status=%s code=%s message=%s",
            path,
            exc.code,
            code or "-",
            text,
        )
        raise portal_error from exc
    except urllib.error.URLError as exc:
        message = f"Portal ist nicht erreichbar: {exc.reason}"
        logger.warning("Portal connection failed: method=POST path=%s url=%s error=%s", path, url, message)
        raise PortalError(message, path=path) from exc
    except TimeoutError as exc:
        message = "Portal-Anfrage ist abgelaufen."
        logger.warning("Portal request timed out: method=POST path=%s url=%s", path, url)
        raise PortalError(message, path=path) from exc
    except json.JSONDecodeError as exc:
        message = "Portal returned invalid JSON."
        logger.warning("Portal returned invalid JSON: method=POST path=%s url=%s", path, url)
        raise PortalError(message, path=path) from exc


@dataclass(frozen=True)
class PortalClaimResult:
    api_token: str
    api_token_prefix: str
    portal_uuid: str
    instance_name: str


def claim_pairing_code(base_url: str, pairing_code: str, instance_payload: dict[str, Any]) -> PortalClaimResult:
    code = str(pairing_code or "").strip().upper()
    if not code:
        raise PortalError("Pairing-Code fehlt.", path="/api/v1/pairing/claim/")

    response = post_json(
        base_url,
        "/api/v1/pairing/claim/",
        {"pairing_code": code, "instance": instance_payload},
        timeout_seconds=15.0,
    )
    token = str(response.get("api_token") or "").strip()
    if not token:
        raise PortalError("Portal returned no API token.", path="/api/v1/pairing/claim/")

    instance = response.get("instance") if isinstance(response.get("instance"), dict) else {}
    return PortalClaimResult(
        api_token=token,
        api_token_prefix=str(response.get("api_token_prefix") or token[:10]).strip(),
        portal_uuid=str(instance.get("portal_uuid") or "").strip(),
        instance_name=str(instance.get("name") or instance_payload.get("name") or "PV2Hash Node"),
    )


def send_snapshot(base_url: str, api_token: str, snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    token = str(api_token or "").strip()
    if not token:
        raise PortalError("Portal API token fehlt.", path="/api/v1/snapshots/")
    return post_json(
        base_url,
        "/api/v1/snapshots/",
        snapshot_payload,
        bearer_token=token,
        timeout_seconds=15.0,
    )
