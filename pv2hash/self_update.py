from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pv2hash.logging_ext.setup import get_logger

DEFAULT_INSTALL_INFO_FILE = Path("/etc/pv2hash/install.env")
DEFAULT_HELPER_PATH = Path("/usr/local/libexec/pv2hash-self-update")
DEFAULT_STATE_FILE = Path("data/self_update_status.json")
DEFAULT_LOCK_DIR = Path("data/self_update.lock")
STARTING_TIMEOUT_SECONDS = 20.0
LAUNCH_CONFIRM_TIMEOUT_SECONDS = 3.0
_SEMVER_TAG_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)$")
_LEGACY_BUILD_TAG_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)-build\.(?P<build>\d+)$")
_LOCAL_VERSION_RE = re.compile(r"^(?P<version>\d+\.\d+\.\d+)(?:\+.+)?$")

logger = get_logger("pv2hash.self_update")


class SelfUpdateError(RuntimeError):
    pass


class SelfUpdateManager:
    def __init__(
        self,
        *,
        current_version: str,
        install_info_file: Path = DEFAULT_INSTALL_INFO_FILE,
        helper_path: Path = DEFAULT_HELPER_PATH,
        state_file: Path = DEFAULT_STATE_FILE,
        lock_dir: Path = DEFAULT_LOCK_DIR,
    ) -> None:
        match = _LOCAL_VERSION_RE.match(str(current_version).strip())
        if not match:
            raise SelfUpdateError(f"Ungültige lokale Version: {current_version!r}")
        self.current_version = match.group("version")
        self.current_version_full = self.current_version
        self.install_info_file = install_info_file
        self.helper_path = helper_path
        self.state_file = state_file
        self.lock_dir = lock_dir

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
        raw = str(tag).strip()
        match = _SEMVER_TAG_RE.match(raw)
        if match is None:
            match = _LEGACY_BUILD_TAG_RE.match(raw)
        if match is None:
            raise SelfUpdateError(f"Ungültiges Release-Tag: {tag!r}")

        version = match.group("version")
        normalized_tag = raw if raw.startswith("v") else f"v{raw}"
        return normalized_tag, version

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

    def _write_error_state(
        self,
        *,
        message: str,
        last_error: str,
        target_tag: str | None = None,
        target_version_full: str | None = None,
        started_at: str | None = None,
        log_file: str | None = None,
    ) -> None:
        self._write_state(
            status="error",
            message=message,
            target_tag=target_tag,
            target_version_full=target_version_full,
            started_at=started_at,
            finished_at=self._now_iso(),
            last_error=last_error,
            helper_path=str(self.helper_path),
            log_file=log_file,
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
                "Update-Helper fehlt. Bitte einmal manuell auf ein Release mit Helper aktualisieren."
            )

        if not os.access(self.helper_path, os.X_OK):
            raise SelfUpdateError("Update-Helper ist nicht ausführbar.")

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

    def _lock_exists(self) -> bool:
        return self.lock_dir.exists()

    def _parse_iso(self, value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _seconds_since(self, value: Any) -> float | None:
        parsed = self._parse_iso(value)
        if parsed is None:
            return None
        return max(0.0, (datetime.now(UTC) - parsed).total_seconds())

    def _normalize_version_text(self, value: Any) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        match = _LOCAL_VERSION_RE.match(raw)
        if match:
            return match.group("version")
        return raw

    def _recover_state(self, file_state: dict[str, Any]) -> dict[str, Any]:
        base_status = str(file_state.get("status") or "idle")
        target_version_full = self._normalize_version_text(file_state.get("target_version_full")) or ""

        if base_status not in {"starting", "running"}:
            return file_state

        started_at = file_state.get("started_at")
        target_tag = file_state.get("target_tag")
        helper_path = str(file_state.get("helper_path") or self.helper_path)
        log_file = file_state.get("log_file")

        if target_version_full and target_version_full == self.current_version_full:
            finished_at = str(file_state.get("finished_at") or "").strip() or self._now_iso()
            self._write_state(
                status="success",
                message=(
                    f"Update auf {self.current_version_full} erfolgreich abgeschlossen. "
                    "Dienst wurde neu gestartet."
                ),
                target_tag=target_tag,
                target_version_full=target_version_full,
                started_at=started_at,
                finished_at=finished_at,
                last_error=None,
                updated_version_full=self.current_version_full,
                helper_path=helper_path,
                log_file=log_file,
            )
            logger.info(
                "Recovered stale self-update state after restart: target=%s current=%s",
                target_version_full,
                self.current_version_full,
            )
            return self._read_state()

        lock_exists = self._lock_exists()
        started_age = self._seconds_since(started_at)

        if (
            base_status == "starting"
            and not lock_exists
            and started_age is not None
            and started_age >= STARTING_TIMEOUT_SECONDS
        ):
            self._write_error_state(
                message="Update konnte nicht sauber gestartet werden.",
                last_error="Helper-Start wurde nicht bestätigt. Bitte Update erneut starten.",
                target_tag=target_tag,
                target_version_full=target_version_full or None,
                started_at=started_at,
                log_file=log_file,
            )
            logger.warning(
                "Recovered stale self-update starting state without lock: target=%s age=%.1fs",
                target_version_full,
                started_age,
            )
            return self._read_state()

        if base_status == "running" and not lock_exists:
            self._write_error_state(
                message="Update ist nicht mehr aktiv. Bitte erneut starten.",
                last_error="Kein aktiver Update-Prozess mehr gefunden.",
                target_tag=target_tag,
                target_version_full=target_version_full or None,
                started_at=started_at,
                log_file=log_file,
            )
            logger.warning(
                "Recovered stale self-update running state without lock: target=%s",
                target_version_full,
            )
            return self._read_state()

        return file_state

    def snapshot(
        self,
        *,
        update_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        install_info = self._read_install_info()
        file_state = self._recover_state(self._read_state())
        is_release_install = install_info.get("PV2HASH_INSTALL_MODE") == "release"
        helper_exists = self.helper_path.exists() and os.access(self.helper_path, os.X_OK)
        helper_configured = is_release_install and helper_exists
        base_status = str(file_state.get("status") or "idle")
        update_status_value = str((update_status or {}).get("status") or "idle")
        available_release_tag = (update_status or {}).get("release_tag")
        available_release_version_full = self._normalize_version_text((update_status or {}).get("release_version_full"))
        lock_exists = self._lock_exists()
        running = base_status in {"starting", "running"}

        if not is_release_install:
            status = "unavailable"
            message = "Update über die Weboberfläche ist nur in Release-Installationen verfügbar."
        elif not helper_exists:
            status = "unavailable"
            message = (
                "Update-Helper fehlt. Bitte dieses Release einmal manuell installieren/aktualisieren, "
                "damit der Helper eingerichtet wird."
            )
        elif base_status in {"starting", "running", "success", "error"}:
            status = base_status
            message = str(file_state.get("message") or "—")
            if base_status == "success" and update_status_value == "update_available":
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
                message = "Update ist bereit, aber es liegt noch kein neueres Release vor."

        can_start = (
            helper_configured
            and not running
            and update_status_value == "update_available"
            and bool(available_release_tag)
        )

        return {
            "enabled": True,
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
            "target_version_full": self._normalize_version_text(file_state.get("target_version_full")),
            "started_at": file_state.get("started_at"),
            "finished_at": file_state.get("finished_at"),
            "last_error": file_state.get("last_error"),
            "updated_version_full": self._normalize_version_text(file_state.get("updated_version_full")),
            "log_file": file_state.get("log_file"),
            "lock_exists": lock_exists,
            "lock_path": str(self.lock_dir),
        }

    def _confirm_launch(self, process: subprocess.Popen[bytes], *, target_tag: str) -> None:
        deadline = time.monotonic() + LAUNCH_CONFIRM_TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            time.sleep(0.25)
            state = self._read_state()
            status = str(state.get("status") or "")
            state_target = str(state.get("target_tag") or "").strip()

            if status == "error":
                raise SelfUpdateError(
                    str(state.get("last_error") or state.get("message") or "Helper-Start fehlgeschlagen.")
                )

            if state_target == target_tag and status in {"running", "success"}:
                return

            if state_target == target_tag and self._lock_exists():
                return

            returncode = process.poll()
            if returncode is not None:
                raise SelfUpdateError(f"Helper wurde vorzeitig beendet (Exitcode {returncode}).")

        raise SelfUpdateError("Helper hat den Laufzustand nicht rechtzeitig bestätigt.")

    def start_latest(
        self,
        *,
        update_status: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        snapshot = self.snapshot(
            update_status=update_status,
        )


        if snapshot["running"]:
            return snapshot, 409

        if update_status.get("status") != "update_available":
            self._write_error_state(
                message="Es ist aktuell kein neueres Release für ein Update verfügbar.",
                last_error="Kein Update verfügbar.",
            )
            return (
                self.snapshot(update_status=update_status),
                409,
            )

        target_tag_raw = str(update_status.get("release_tag") or "").strip()
        if not target_tag_raw:
            self._write_error_state(
                message="Release-Tag fehlt im Update-Status.",
                last_error="Release-Tag fehlt.",
            )
            return (
                self.snapshot(update_status=update_status),
                500,
            )

        target_tag, target_version_full = self._parse_release_tag(target_tag_raw)
        started_at = self._now_iso()

        try:
            self._verify_helper_ready()
            self._write_state(
                status="starting",
                message=f"Update auf {target_version_full} wird gestartet …",
                target_tag=target_tag,
                target_version_full=target_version_full,
                started_at=started_at,
                finished_at=None,
                last_error=None,
                updated_version_full=None,
                helper_path=str(self.helper_path),
                log_file=None,
            )

            command = self._launch_command(target_tag)
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            self._confirm_launch(process, target_tag=target_tag)
            logger.info("Web update launched: target=%s", target_tag)
        except Exception as exc:
            logger.warning("Self-update launch failed: %s", exc)
            self._write_error_state(
                message=f"Update konnte nicht gestartet werden: {exc}",
                last_error=str(exc),
                target_tag=target_tag,
                target_version_full=target_version_full,
                started_at=started_at,
            )
            return (
                self.snapshot(update_status=update_status),
                500,
            )

        return (
            self.snapshot(update_status=update_status),
            202,
        )
