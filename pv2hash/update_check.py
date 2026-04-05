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
UPDATE_CHECK_CACHE_SECONDS = 6 * 60 * 60
UPDATE_CHECK_TIMEOUT_SECONDS = 10.0
_RELEASE_TAG_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)-build\.(?P<build>\d+)$")

logger = get_logger("pv2hash.update_check")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def _parse_local_version(version: str, build: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in str(version).split(".")]
    if len(parts) != 3:
        raise ValueError(f"Ungültige lokale Version: {version}")
    return parts[0], parts[1], parts[2], int(build)


def _parse_release_tag(tag_name: str) -> dict[str, Any]:
    match = _RELEASE_TAG_RE.match(str(tag_name).strip())
    if not match:
        raise ValueError(f"Unbekanntes Release-Tag-Format: {tag_name!r}")

    version = match.group("version")
    build = match.group("build")
    major, minor, patch = [int(part) for part in version.split(".")]

    return {
        "tag": tag_name,
        "version": version,
        "build": build,
        "version_full": f"{version}+build.{build}",
        "tuple": (major, minor, patch, int(build)),
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
        "error": status.error,
    }


class UpdateChecker:
    def __init__(self, state: AppState, *, current_version: str, current_build: str) -> None:
        self.state = state
        self.current_version = current_version
        self.current_build = current_build
        self.current_version_full = f"{current_version}+build.{current_build}"
        self.current_tuple = _parse_local_version(current_version, current_build)
        self._lock = asyncio.Lock()

    def _is_enabled(self) -> bool:
        return bool(self.state.config.get("system", {}).get("check_updates", True))

    def _repo(self) -> str:
        raw = str(
            self.state.config.get("system", {}).get("update_repo", DEFAULT_UPDATE_REPO)
        ).strip()
        return raw or DEFAULT_UPDATE_REPO

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

        checked_at = self.state.update_check.checked_at
        if checked_at is not None:
            age = datetime.now(UTC) - checked_at
            if age < timedelta(seconds=UPDATE_CHECK_CACHE_SECONDS):
                return self.snapshot()

        return await self.refresh()

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
                    error=str(exc),
                )

            return self.snapshot()
