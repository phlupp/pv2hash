from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from pv2hash.logging_ext.setup import get_logger
from pv2hash.runtime import AppState, UpdateCheckState

DEFAULT_UPDATE_REPO = "phlupp/pv2hash"
GITHUB_RELEASES_LATEST_URL = "https://api.github.com/repos/{repo}/releases/latest"
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
        "tag": raw,
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
        self.current_version = _LOCAL_VERSION_RE.match(str(current_version).strip()).group("version") if _LOCAL_VERSION_RE.match(str(current_version).strip()) else current_version
        self.current_version_full = self.current_version
        self.current_tuple = _parse_version_tuple(self.current_version)
        self._lock = asyncio.Lock()

    def _is_enabled(self) -> bool:
        return bool(self.state.config.get("system", {}).get("check_updates", True))

    def _repo(self) -> str:
        raw = str(
            self.state.config.get("system", {}).get("update_repo", DEFAULT_UPDATE_REPO)
        ).strip()
        return raw or DEFAULT_UPDATE_REPO

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
            repo = self._repo()
            current = self.state.update_check
            current.enabled = True
            current.checking = True
            current.status = "checking"
            current.repo = repo
            current.local_version_full = self.current_version_full
            current.error = None

            try:
                headers = {
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"PV2Hash/{self.current_version_full}",
                }
                url = GITHUB_RELEASES_LATEST_URL.format(repo=repo)

                async with httpx.AsyncClient(
                    timeout=UPDATE_CHECK_TIMEOUT_SECONDS,
                    follow_redirects=True,
                    headers=headers,
                ) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    release = response.json()

                tag_name = str(release.get("tag_name") or "").strip()
                release_info = _parse_release_tag(tag_name)
                release_tuple = release_info["tuple"]

                if release_tuple > self.current_tuple:
                    status_value = "update_available"
                elif release_tuple == self.current_tuple:
                    status_value = "up_to_date"
                else:
                    status_value = "ahead_of_release"

                assets = release.get("assets") if isinstance(release.get("assets"), list) else []
                selected_asset = None
                for asset in assets:
                    if not isinstance(asset, dict):
                        continue
                    name = str(asset.get("name") or "").strip()
                    if name.endswith(".zip"):
                        selected_asset = asset
                        break
                if selected_asset is None and assets:
                    selected_asset = next((asset for asset in assets if isinstance(asset, dict)), None)

                self.state.update_check = UpdateCheckState(
                    enabled=True,
                    checking=False,
                    status=status_value,
                    repo=repo,
                    local_version_full=self.current_version_full,
                    checked_at=datetime.now(UTC),
                    release_tag=release_info["tag"],
                    release_name=str(release.get("name") or "").strip() or None,
                    release_url=str(release.get("html_url") or "").strip() or None,
                    release_version=release_info["version"],
                    release_build=release_info["build"],
                    release_version_full=release_info["version_full"],
                    release_published_at=_parse_timestamp(release.get("published_at")),
                    release_body=str(release.get("body") or "").strip() or None,
                    release_asset_name=(str(selected_asset.get("name") or "").strip() if selected_asset else None),
                    release_asset_size_bytes=(int(selected_asset.get("size")) if selected_asset and selected_asset.get("size") is not None else None),
                    release_asset_count=len(assets),
                    error=None,
                )

                logger.info(
                    "Update check finished: status=%s local=%s remote=%s repo=%s",
                    status_value,
                    self.current_version_full,
                    release_info["version_full"],
                    repo,
                )
            except Exception as exc:
                logger.warning("Update check failed: repo=%s error=%s", repo, exc)
                self.state.update_check = UpdateCheckState(
                    enabled=True,
                    checking=False,
                    status="error",
                    repo=repo,
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
