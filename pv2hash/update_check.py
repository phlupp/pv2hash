from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import httpx

from pv2hash.logging_ext.setup import get_logger
from pv2hash.runtime import AppState, UpdateCheckState

DEFAULT_UPDATE_BASE_URL = "https://get.pv2hash.xyz"
DEFAULT_UPDATE_CHANNEL = "stable"
# Kept for older configs/UI fields. New installations use update_base_url + update_channel.
DEFAULT_UPDATE_REPO = DEFAULT_UPDATE_BASE_URL
UPDATE_CHECK_INTERVAL_SECONDS = 60 * 60
UPDATE_CHECK_TIMEOUT_SECONDS = 10.0
BACKGROUND_TICK_SECONDS = 60
_SEMVER_TAG_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)$")
_LEGACY_BUILD_TAG_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)-build\.(?P<build>\d+)$")
_LOCAL_VERSION_RE = re.compile(r"^(?P<version>\d+\.\d+\.\d+)(?:\+.+)?$")

logger = get_logger("pv2hash.update_check")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def _parse_version_tuple(version: str) -> tuple[int, int, int]:
    match = _LOCAL_VERSION_RE.match(str(version).strip())
    if not match:
        raise ValueError(f"Ungültige Version: {version}")

    core = match.group("version")
    major, minor, patch = [int(part) for part in core.split(".")]
    return major, minor, patch


def _parse_release_tag(tag_name: str) -> dict[str, Any]:
    raw = str(tag_name).strip()
    match = _SEMVER_TAG_RE.match(raw)
    build: str | None = None

    if match is None:
        match = _LEGACY_BUILD_TAG_RE.match(raw)
        if match is not None:
            build = match.group("build")

    if match is None:
        raise ValueError(f"Unbekanntes Release-Tag-Format: {tag_name!r}")

    version = match.group("version")

    return {
        "tag": raw if raw.startswith("v") else f"v{raw}",
        "version": version,
        "build": build,
        "version_full": version,
        "tuple": _parse_version_tuple(version),
    }


def _serialize_update_check(status: UpdateCheckState) -> dict[str, Any]:
    return {
        "enabled": status.enabled,
        "checking": status.checking,
        "status": status.status,
        "repo": status.repo,
        "local_version_full": status.local_version_full,
        "checked_at": status.checked_at.isoformat() if status.checked_at else None,
        "release_tag": status.release_tag,
        "release_name": status.release_name,
        "release_url": status.release_url,
        "release_version": status.release_version,
        "release_build": status.release_build,
        "release_version_full": status.release_version_full,
        "release_published_at": (
            status.release_published_at.isoformat() if status.release_published_at else None
        ),
        "release_body": status.release_body,
        "release_asset_name": status.release_asset_name,
        "release_asset_size_bytes": status.release_asset_size_bytes,
        "release_asset_count": status.release_asset_count,
        "error": status.error,
    }


class UpdateChecker:
    def __init__(self, state: AppState, *, current_version: str) -> None:
        self.state = state
        match = _LOCAL_VERSION_RE.match(str(current_version).strip())
        self.current_version = match.group("version") if match else current_version
        self.current_version_full = self.current_version
        self.current_tuple = _parse_version_tuple(self.current_version)
        self._lock = asyncio.Lock()

    def _is_enabled(self) -> bool:
        return bool(self.state.config.get("system", {}).get("check_updates", True))

    def _base_url(self) -> str:
        system = self.state.config.get("system", {})
        raw = str(system.get("update_base_url") or DEFAULT_UPDATE_BASE_URL).strip()
        return (raw or DEFAULT_UPDATE_BASE_URL).rstrip("/")

    def _channel(self) -> str:
        system = self.state.config.get("system", {})
        raw = str(system.get("update_channel") or DEFAULT_UPDATE_CHANNEL).strip()
        return raw or DEFAULT_UPDATE_CHANNEL

    def _repo(self) -> str:
        return f"{self._base_url()}/channels/{self._channel()}.json"

    def _is_stale(self) -> bool:
        checked_at = self.state.update_check.checked_at
        if checked_at is None:
            return True
        age = datetime.now(UTC) - checked_at
        return age >= timedelta(seconds=UPDATE_CHECK_INTERVAL_SECONDS)

    def _set_disabled_state(self) -> None:
        current = self.state.update_check
        self.state.update_check = UpdateCheckState(
            enabled=False,
            checking=False,
            status="disabled",
            repo=self._repo(),
            local_version_full=self.current_version_full,
            checked_at=current.checked_at,
            release_tag=current.release_tag,
            release_name=current.release_name,
            release_url=current.release_url,
            release_version=current.release_version,
            release_build=current.release_build,
            release_version_full=current.release_version_full,
            release_published_at=current.release_published_at,
            release_body=current.release_body,
            release_asset_name=current.release_asset_name,
            release_asset_size_bytes=current.release_asset_size_bytes,
            release_asset_count=current.release_asset_count,
            error=None,
        )

    def snapshot(self) -> dict[str, Any]:
        if not self._is_enabled():
            self._set_disabled_state()
        else:
            self.state.update_check.enabled = True
            self.state.update_check.repo = self._repo()
            self.state.update_check.local_version_full = self.current_version_full

        return _serialize_update_check(self.state.update_check)

    async def refresh_if_stale(self) -> dict[str, Any]:
        if not self._is_enabled():
            self._set_disabled_state()
            return self.snapshot()

        if not self._is_stale():
            return self.snapshot()

        return await self.refresh()

    async def run_background_loop(self) -> None:
        logger.info(
            "Update check background loop started: interval=%ss tick=%ss",
            UPDATE_CHECK_INTERVAL_SECONDS,
            BACKGROUND_TICK_SECONDS,
        )

        while True:
            try:
                await self.refresh_if_stale()
            except asyncio.CancelledError:
                logger.info("Update check background loop stopped")
                raise
            except Exception:
                logger.exception("Unhandled error in update check background loop")

            await asyncio.sleep(BACKGROUND_TICK_SECONDS)

    async def refresh(self) -> dict[str, Any]:
        if not self._is_enabled():
            self._set_disabled_state()
            return self.snapshot()

        async with self._lock:
            feed_url = self._repo()
            current = self.state.update_check
            current.enabled = True
            current.checking = True
            current.status = "checking"
            current.repo = feed_url
            current.local_version_full = self.current_version_full
            current.error = None

            try:
                headers = {
                    "Accept": "application/json",
                    "User-Agent": f"PV2Hash/{self.current_version_full}",
                }

                async with httpx.AsyncClient(
                    timeout=UPDATE_CHECK_TIMEOUT_SECONDS,
                    follow_redirects=True,
                    headers=headers,
                ) as client:
                    response = await client.get(feed_url)
                    response.raise_for_status()
                    feed = response.json()

                if not isinstance(feed, dict):
                    raise ValueError("Update-Feed hat kein gültiges JSON-Objekt geliefert")

                if feed.get("updates_enabled") is False:
                    message = str(feed.get("message") or "Updates sind serverseitig deaktiviert.")
                    self.state.update_check = UpdateCheckState(
                        enabled=True,
                        checking=False,
                        status="disabled",
                        repo=feed_url,
                        local_version_full=self.current_version_full,
                        checked_at=datetime.now(UTC),
                        error=message,
                    )
                    return self.snapshot()

                latest = feed.get("latest")
                if not isinstance(latest, dict):
                    raise ValueError("Update-Feed enthält kein latest-Release")

                tag_name = str(latest.get("version_full") or latest.get("tag") or latest.get("version") or "").strip()
                release_info = _parse_release_tag(tag_name)
                release_tuple = release_info["tuple"]

                if release_tuple > self.current_tuple:
                    status_value = "update_available"
                elif release_tuple == self.current_tuple:
                    status_value = "up_to_date"
                else:
                    status_value = "ahead_of_release"

                asset = latest.get("asset") if isinstance(latest.get("asset"), dict) else {}
                asset_name = str(asset.get("name") or "").strip() or None
                asset_size = asset.get("size_bytes")
                try:
                    asset_size_int = int(asset_size) if asset_size is not None else None
                except (TypeError, ValueError):
                    asset_size_int = None

                release_url = str(latest.get("manifest_url") or "").strip()
                if not release_url:
                    release_url = urljoin(self._base_url() + "/", f"releases/{release_info['tag']}/manifest.json")

                body = str(latest.get("notes") or latest.get("body") or feed.get("message") or "").strip() or None

                self.state.update_check = UpdateCheckState(
                    enabled=True,
                    checking=False,
                    status=status_value,
                    repo=feed_url,
                    local_version_full=self.current_version_full,
                    checked_at=datetime.now(UTC),
                    release_tag=release_info["tag"],
                    release_name=str(latest.get("title") or f"PV2Hash {release_info['version']}"),
                    release_url=release_url,
                    release_version=release_info["version"],
                    release_build=release_info["build"],
                    release_version_full=release_info["version_full"],
                    release_published_at=_parse_timestamp(str(latest.get("released_at") or "")),
                    release_body=body,
                    release_asset_name=asset_name,
                    release_asset_size_bytes=asset_size_int,
                    release_asset_count=1 if asset_name else 0,
                    error=None,
                )

                logger.info(
                    "Update check finished: status=%s local=%s remote=%s feed=%s",
                    status_value,
                    self.current_version_full,
                    release_info["version_full"],
                    feed_url,
                )
            except Exception as exc:
                logger.warning("Update check failed: feed=%s error=%s", feed_url, exc)
                self.state.update_check = UpdateCheckState(
                    enabled=True,
                    checking=False,
                    status="error",
                    repo=feed_url,
                    local_version_full=self.current_version_full,
                    checked_at=datetime.now(UTC),
                    release_tag=current.release_tag,
                    release_name=current.release_name,
                    release_url=current.release_url,
                    release_version=current.release_version,
                    release_build=current.release_build,
                    release_version_full=current.release_version_full,
                    release_published_at=current.release_published_at,
                    release_body=current.release_body,
                    release_asset_name=current.release_asset_name,
                    release_asset_size_bytes=current.release_asset_size_bytes,
                    release_asset_count=current.release_asset_count,
                    error=str(exc),
                )

            return self.snapshot()
