from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin


class PortalError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


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

    request = urllib.request.Request(
        _api_url(base_url, path),
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
                raise PortalError("Portal returned an unexpected JSON response.")
            return decoded
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        code, message = _decode_error_body(raw)
        text = message or code or f"Portal request failed with HTTP {exc.code}."
        raise PortalError(text, status_code=exc.code, code=code) from exc
    except urllib.error.URLError as exc:
        raise PortalError(f"Portal ist nicht erreichbar: {exc.reason}") from exc
    except TimeoutError as exc:
        raise PortalError("Portal-Anfrage ist abgelaufen.") from exc
    except json.JSONDecodeError as exc:
        raise PortalError("Portal returned invalid JSON.") from exc


@dataclass(frozen=True)
class PortalClaimResult:
    api_token: str
    api_token_prefix: str
    portal_uuid: str
    instance_name: str


def claim_pairing_code(base_url: str, pairing_code: str, instance_payload: dict[str, Any]) -> PortalClaimResult:
    code = str(pairing_code or "").strip().upper()
    if not code:
        raise PortalError("Pairing-Code fehlt.")

    response = post_json(
        base_url,
        "/api/v1/pairing/claim/",
        {"pairing_code": code, "instance": instance_payload},
        timeout_seconds=15.0,
    )
    token = str(response.get("api_token") or "").strip()
    if not token:
        raise PortalError("Portal returned no API token.")

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
        raise PortalError("Portal API token fehlt.")
    return post_json(
        base_url,
        "/api/v1/snapshots/",
        snapshot_payload,
        bearer_token=token,
        timeout_seconds=15.0,
    )
