from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pv2hash.logging_ext.setup import get_logger

DEFAULT_INSTALL_INFO_FILE = Path("/etc/pv2hash/install.env")
DEFAULT_HELPER_PATH = Path("/usr/local/libexec/pv2hash-self-update")
DEFAULT_STATE_FILE = Path("data/self_update_status.json")
_RELEASE_TAG_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)-build\.(?P<build>\d+)$")

logger = get_logger("pv2hash.self_update")


class SelfUpdateError(RuntimeError):
    pass


class SelfUpdateManager:
    def __init__(
        self,
        *,
        current_version: str,
        current_build: str,
        install_info_file: Path = DEFAULT_INSTALL_INFO_FILE,
        helper_path: Path = DEFAULT_HELPER_PATH,
        state_file: Path = DEFAULT_STATE_FILE,
    ) -> None:
        self.current_version = current_version
        self.current_build = current_build
        self.current_version_full = f"{current_version}+build.{current_build}"
        self.install_info_file = install_info_file
        self.helper_path = helper_path
        self.state_file = state_file

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()

    def _read_install_info(self) -> dict[str, str]:
        if not self.install_info_file.exists():
            return {}

        values: dict[str, str] = {}
        for raw_line in self.install_info_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        return values

    def _parse_release_tag(self, tag: str) -> tuple[str, str]:
        match = _RELEASE_TAG_RE.match(str(tag).strip())
        if not match:
            raise SelfUpdateError(f"Ungültiges Release-Tag: {tag!r}")

        version = match.group("version")
        build = match.group("build")
        normalized_tag = tag if tag.startswith("v") else f"v{tag}"
        return normalized_tag, f"{version}+build.{build}"

    def _read_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}

        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.warning("Failed to read self-update state file: %s", exc)
        return {}

    def _write_state(
        self,
        *,
        status: str,
        message: str,
        target_tag: str | None = None,
        target_version_full: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        last_error: str | None = None,
        updated_version_full: str | None = None,
        helper_path: str | None = None,
        log_file: str | None = None,
    ) -> None:
        payload = {
            "status": status,
            "message": message,
            "target_tag": target_tag,
            "target_version_full": target_version_full,
            "started_at": started_at,
            "finished_at": finished_at,
            "last_error": last_error,
            "updated_version_full": updated_version_full,
            "helper_path": helper_path,
            "log_file": log_file,
            "written_at": self._now_iso(),
        }
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _probe_command(self) -> list[str]:
        if os.geteuid() == 0:
            return [str(self.helper_path), "--probe"]

        sudo = shutil.which("sudo")
        if not sudo:
            raise SelfUpdateError("sudo ist nicht installiert.")
        return [sudo, "-n", str(self.helper_path), "--probe"]

    def _launch_command(self, tag: str) -> list[str]:
        if os.geteuid() == 0:
            return [str(self.helper_path), tag]

        sudo = shutil.which("sudo")
        if not sudo:
            raise SelfUpdateError("sudo ist nicht installiert.")
        return [sudo, "-n", str(self.helper_path), tag]

    def _verify_helper_ready(self) -> None:
        if not self.helper_path.exists():
            raise SelfUpdateError(
                "Self-Update-Helper fehlt. Bitte einmal manuell auf ein Release mit Helper aktualisieren."
            )

        if not os.access(self.helper_path, os.X_OK):
            raise SelfUpdateError("Self-Update-Helper ist nicht ausführbar.")

        command = self._probe_command()
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            if not details:
                details = "Helper-Probe fehlgeschlagen. sudo / Installationsrechte prüfen."
            raise SelfUpdateError(details)

    def snapshot(
        self,
        *,
        auto_update_enabled: bool,
        update_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        install_info = self._read_install_info()
        file_state = self._read_state()
        is_release_install = install_info.get("PV2HASH_INSTALL_MODE") == "release"
        helper_exists = self.helper_path.exists() and os.access(self.helper_path, os.X_OK)
        helper_configured = is_release_install and helper_exists
        base_status = str(file_state.get("status") or "idle")
        update_status_value = str((update_status or {}).get("status") or "idle")
        available_release_tag = (update_status or {}).get("release_tag")
        available_release_version_full = (update_status or {}).get("release_version_full")
        running = base_status in {"starting", "running"}

        if not auto_update_enabled:
            status = "disabled"
            message = "Self-Update ist in den Einstellungen deaktiviert."
        elif not is_release_install:
            status = "unavailable"
            message = "Self-Update ist nur in Release-Installationen verfügbar."
        elif not helper_exists:
            status = "unavailable"
            message = (
                "Self-Update-Helper fehlt. Bitte dieses Release einmal manuell installieren/aktualisieren, "
                "damit der Helper eingerichtet wird."
            )
        elif base_status in {"starting", "running", "success", "error"}:
            status = base_status
            message = str(file_state.get("message") or "—")
            if base_status in {"success", "error"} and update_status_value == "update_available":
                status = "idle"
                message = f"Update auf {available_release_version_full or available_release_tag} ist bereit."
        else:
            status = "idle"
            if update_status_value == "update_available":
                message = f"Update auf {available_release_version_full or available_release_tag} ist bereit."
            elif update_status_value == "up_to_date":
                message = "Kein neueres Release gefunden."
            elif update_status_value == "ahead_of_release":
                message = "Lokaler Stand ist neuer als Latest Release."
            elif update_status_value == "checking":
                message = "Release-Status wird noch geprüft."
            elif update_status_value == "error":
                message = "Release-Check ist fehlgeschlagen."
            else:
                message = "Self-Update ist bereit, aber es liegt noch kein neueres Release vor."

        can_start = (
            auto_update_enabled
            and helper_configured
            and not running
            and update_status_value == "update_available"
            and bool(available_release_tag)
        )

        return {
            "enabled": auto_update_enabled,
            "configured": helper_configured,
            "install_info_exists": bool(install_info),
            "install_mode": install_info.get("PV2HASH_INSTALL_MODE"),
            "helper_path": str(self.helper_path),
            "helper_exists": helper_exists,
            "sudo_available": shutil.which("sudo") is not None,
            "status": status,
            "message": message,
            "running": running,
            "can_start": can_start,
            "current_version_full": self.current_version_full,
            "available_release_tag": available_release_tag,
            "available_release_version_full": available_release_version_full,
            "target_tag": file_state.get("target_tag"),
            "target_version_full": file_state.get("target_version_full"),
            "started_at": file_state.get("started_at"),
            "finished_at": file_state.get("finished_at"),
            "last_error": file_state.get("last_error"),
            "updated_version_full": file_state.get("updated_version_full"),
            "log_file": file_state.get("log_file"),
        }

    def start_latest(
        self,
        *,
        auto_update_enabled: bool,
        update_status: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        snapshot = self.snapshot(
            auto_update_enabled=auto_update_enabled,
            update_status=update_status,
        )

        if not auto_update_enabled:
            self._write_state(
                status="error",
                message="Self-Update ist deaktiviert.",
                finished_at=self._now_iso(),
                last_error="Self-Update ist deaktiviert.",
                helper_path=str(self.helper_path),
            )
            return (
                self.snapshot(auto_update_enabled=auto_update_enabled, update_status=update_status),
                403,
            )

        if snapshot["running"]:
            return snapshot, 409

        if update_status.get("status") != "update_available":
            self._write_state(
                status="error",
                message="Es ist aktuell kein neueres Release für ein Self-Update verfügbar.",
                finished_at=self._now_iso(),
                last_error="Kein Update verfügbar.",
                helper_path=str(self.helper_path),
            )
            return (
                self.snapshot(auto_update_enabled=auto_update_enabled, update_status=update_status),
                409,
            )

        target_tag_raw = str(update_status.get("release_tag") or "").strip()
        if not target_tag_raw:
            self._write_state(
                status="error",
                message="Release-Tag fehlt im Update-Status.",
                finished_at=self._now_iso(),
                last_error="Release-Tag fehlt.",
                helper_path=str(self.helper_path),
            )
            return (
                self.snapshot(auto_update_enabled=auto_update_enabled, update_status=update_status),
                500,
            )

        target_tag, target_version_full = self._parse_release_tag(target_tag_raw)

        try:
            self._verify_helper_ready()
            self._write_state(
                status="starting",
                message=f"Self-Update auf {target_version_full} wird gestartet …",
                target_tag=target_tag,
                target_version_full=target_version_full,
                started_at=self._now_iso(),
                finished_at=None,
                last_error=None,
                updated_version_full=None,
                helper_path=str(self.helper_path),
                log_file=None,
            )

            command = self._launch_command(target_tag)
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            logger.info("Self-update launched: target=%s", target_tag)
        except Exception as exc:
            logger.warning("Self-update launch failed: %s", exc)
            self._write_state(
                status="error",
                message=f"Self-Update konnte nicht gestartet werden: {exc}",
                target_tag=target_tag,
                target_version_full=target_version_full,
                started_at=self._now_iso(),
                finished_at=self._now_iso(),
                last_error=str(exc),
                helper_path=str(self.helper_path),
                log_file=None,
            )
            return (
                self.snapshot(auto_update_enabled=auto_update_enabled, update_status=update_status),
                500,
            )

        return (
            self.snapshot(auto_update_enabled=auto_update_enabled, update_status=update_status),
            202,
        )
