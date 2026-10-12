"""Updater lifecycle regressions with an isolated mock systemd-run; no root required."""
from __future__ import annotations

import json
import grp
import os
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from pv2hash.self_update import SelfUpdateManager, SelfUpdateError

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts/pv2hash-self-update").read_text()


class SelfUpdateLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.install_info = self.root / "install.env"
        self.app = self.root / "app"
        self.data = self.root / "data"
        self.bin = self.root / "bin"
        self.app.joinpath("current", "scripts").mkdir(parents=True)
        self.data.mkdir()
        self.bin.mkdir()
        self.helper = self.root / "pv2hash-self-update"
        self.helper.write_text(
            SOURCE.replace(
                'INSTALL_INFO_FILE="/etc/pv2hash/install.env"',
                f'INSTALL_INFO_FILE="{self.install_info}"',
                1,
            ).replace(
                # Fixture only: the shipped helper still requires root.
                'require_root() {\n    if [[ "${EUID}" -ne 0 ]]; then\n        fail "Der Update-Helper muss als root laufen."\n    fi\n}',
                'require_root() { :; }',
                1,
            )
        )
        self.helper.chmod(0o755)
        self.install_info.write_text(
            f'PV2HASH_INSTALL_MODE="release"\n'
            f'PV2HASH_APP_ROOT="{self.app}"\n'
            f'PV2HASH_DATA_DIR="{self.data}"\n'
            f'PV2HASH_APP_USER="{os.environ.get("USER", "chatgpt")}"\n'
            f'PV2HASH_APP_GROUP="{grp.getgrgid(os.getgid()).gr_name}"\n'
            'PV2HASH_VERSION="0.9.4"\n'
        )
        self.installer = self.app / "current/scripts/install_release.sh"
        self.write_installer("exit 0")
        systemd_run = self.bin / "systemd-run"
        systemd_run.write_text(
            '#!/usr/bin/env python3\n'
            'import json, os, subprocess, sys\n'
            'args = sys.argv[1:]\n'
            'with open(os.environ["MOCK_ARGS"], "w") as out: json.dump(args,out)\n'
            'if os.environ.get("MOCK_DISPATCH_FAIL") == "1": sys.exit(47)\n'
            'idx = args.index("--") if "--" in args else next(i for i,a in enumerate(args) if a.startswith("/") and a.endswith("pv2hash-self-update"))\n'
            'cmd = args[idx+1:] if "--" in args else args[idx:]\n'
            'if os.environ.get("MOCK_ASYNC") == "1":\n'
            '  subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n'
            '    stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)\n'
            'else:\n'
            '  subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n'
            '    stderr=subprocess.DEVNULL, check=False)\n'
        )
        systemd_run.chmod(0o755)
        systemctl = self.bin / "systemctl"
        systemctl.write_text(
            '#!/usr/bin/env python3\n'
            'import os\n'
            'pid = os.getppid()\n'
            'worker_pid = 0\n'
            'while pid > 1:\n'
            '  try:\n'
            '    argv = open(f"/proc/{pid}/cmdline", "rb").read().split(bytes([0]))\n'
            '    if argv[0].endswith(b"bash") and b"--worker" in argv and any(v.endswith(b"/pv2hash-self-update") for v in argv):\n'
            '      worker_pid = pid\n'
            '    lines = open(f"/proc/{pid}/status").read().splitlines()\n'
            '    pid = int(next(x.split()[1] for x in lines if x.startswith("PPid:")))\n'
            '  except (OSError, StopIteration, ValueError): break\n'
            'print(worker_pid if os.environ.get("MOCK_WRONG_PID") != "1" else 1)\n'
        )
        systemctl.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "MOCK_ARGS": str(self.root / "systemd-run-args.json"),
        }

    def write_installer(self, script: str) -> None:
        self.installer.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + script + "\n")
        self.installer.chmod(0o755)

    def run_helper(self, *args: str, env: dict | None = None):
        return subprocess.run(
            ["bash", str(self.helper), *args], env=self.env if env is None else env,
            capture_output=True, text=True, timeout=10,
        )

    def state(self):
        return json.loads((self.data / "self_update_status.json").read_text())

    def test_dispatch_starts_dedicated_transient_service(self):
        result = self.run_helper("v0.9.4")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads((self.root / "systemd-run-args.json").read_text())
        self.assertIn("--unit=pv2hash-self-update", args)
        self.assertIn("--service-type=exec", args)
        self.assertIn("--collect", args)
        self.assertIn("--property=RuntimeMaxSec=1800", args)
        self.assertEqual(args[-3:], [str(self.helper), "--worker", "v0.9.4"])
        self.assertEqual(self.state()["status"], "success")
        self.assertEqual(self.state()["updated_version_full"], "0.9.4")
        self.assertFalse((self.data / "self_update.lock").exists())
        self.assertFalse(list(self.data.glob(".self-update-state-*")))

    def test_failed_install_persists_error_and_releases_lock(self):
        self.write_installer("echo 'simulated install failure'\nexit 37")
        result = self.run_helper("v0.9.4")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.state()
        self.assertEqual(state["status"], "error")
        self.assertIn("simulated install failure", state["last_error"])
        self.assertFalse((self.data / "self_update.lock").exists())

    def test_dispatcher_success_before_worker_state_does_not_false_fail(self):
        manager = SelfUpdateManager(
            current_version="0.9.3",
            install_info_file=self.install_info,
            helper_path=self.helper,
            state_file=self.data / "self_update_status.json",
            lock_dir=self.data / "self_update.lock",
        )
        manager._write_state(
            status="starting", message="starting", target_tag="v0.9.4",
            target_version_full="0.9.4",
        )

        class CompletedDispatcher:
            def poll(self):
                return 0

        def mark_started():
            time.sleep(0.6)
            manager._write_state(
                status="running", message="worker active", target_tag="v0.9.4",
                target_version_full="0.9.4",
            )

        thread = threading.Thread(target=mark_started)
        thread.start()
        try:
            manager._confirm_launch(CompletedDispatcher(), target_tag="v0.9.4")
            self.assertEqual(manager._read_state()["status"], "running")
        finally:
            thread.join(timeout=2)

    def test_dispatcher_nonzero_exit_fails_immediately(self):
        manager = SelfUpdateManager(
            current_version="0.9.3",
            install_info_file=self.install_info,
            helper_path=self.helper,
            state_file=self.data / "self_update_status.json",
            lock_dir=self.data / "self_update.lock",
        )

        class FailedDispatcher:
            def poll(self):
                return 17

        with self.assertRaisesRegex(SelfUpdateError, "Exitcode 17"):
            manager._confirm_launch(FailedDispatcher(), target_tag="v0.9.4")

    def test_systemd_start_failure_does_not_create_lock(self):
        env = dict(self.env, MOCK_DISPATCH_FAIL="1")
        result = self.run_helper("v0.9.4", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.data / "self_update.lock").exists())

    def test_invalid_tag_rejected_before_dispatch(self):
        result = self.run_helper("v0.9.4;touch /tmp/not-valid")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "systemd-run-args.json").exists())

    def test_worker_cannot_be_invoked_outside_its_unit(self):
        result = self.run_helper(
            "--worker", "v0.9.4",
            env={**self.env, "MOCK_WRONG_PID": "1"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("eigenen systemd-Unit", result.stderr)
        self.assertFalse((self.data / "self_update.lock").exists())

    def test_recycled_pid_lock_cleaned_without_killing_unrelated_process(self):
        lock = self.data / "self_update.lock"
        lock.mkdir()
        (lock / "pid").write_text(str(os.getpid()) + "\n")
        result = self.run_helper("--worker", "v0.9.4")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()["status"], "success")
        self.assertFalse(lock.exists())

    def test_active_worker_lock_rejects_second_worker(self):
        ready = self.root / "installer-ready"
        self.write_installer(f"touch {ready}\nsleep 4")
        first = subprocess.Popen(
            ["bash", str(self.helper), "--worker", "v0.9.4"],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(35):
                if ready.exists() and (self.data / "self_update.lock/pid").exists():
                    break
                time.sleep(0.05)
            self.assertTrue(ready.exists())
            second = self.run_helper("--worker", "v0.9.4")
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("bereits ein Update", second.stderr)
            self.assertTrue((self.data / "self_update.lock/pid").exists())
        finally:
            first.terminate()
            first.wait(timeout=8)
        self.assertFalse((self.data / "self_update.lock").exists())

    def test_recovery_waits_while_isolated_worker_still_running(self):
        ready = self.root / "installer-ready"
        self.write_installer(f"touch {ready}\nsleep 4")
        manager = SelfUpdateManager(
            current_version="0.9.4",
            install_info_file=self.install_info, helper_path=self.helper,
            state_file=self.data / "self_update_status.json",
            lock_dir=self.data / "self_update.lock",
        )
        worker = subprocess.Popen(
            ["bash", str(self.helper), "--worker", "v0.9.4"],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(35):
                if ready.exists() and (self.data / "self_update.lock/pid").exists():
                    break
                time.sleep(0.05)
            self.assertTrue(ready.exists())
            current = manager._read_state()
            self.assertEqual(current["status"], "running")
            self.assertTrue(manager._lock_has_live_worker())
            self.assertEqual(manager._recover_state(current)["status"], "running")
            self.assertEqual(manager._read_state()["status"], "running")
        finally:
            worker.terminate()
            worker.wait(timeout=8)
        self.assertFalse(manager._lock_has_live_worker())
        self.assertFalse((self.data / "self_update.lock").exists())

    def test_recovery_ignores_stale_pid_reused_by_unrelated_process(self):
        manager = SelfUpdateManager(
            current_version="0.9.4",
            install_info_file=self.install_info, helper_path=self.helper,
            state_file=self.data / "self_update_status.json",
            lock_dir=self.data / "self_update.lock",
        )
        manager._write_state(
            status="running", message="installing", target_tag="v0.9.4",
            target_version_full="0.9.4",
        )
        lock = self.data / "self_update.lock"
        lock.mkdir()
        (lock / "pid").write_text(str(os.getpid()))
        self.assertFalse(manager._lock_has_live_worker())
        self.assertEqual(manager._recover_state(manager._read_state())["status"], "success")
        self.assertEqual(manager._read_state()["updated_version_full"], "0.9.4")

    def test_installer_replaces_running_helper_atomically(self):
        source = (ROOT / "scripts/install_release.sh").read_text()
        funcs = self.root / "install-functions.sh"
        funcs.write_text(source.rsplit('\nmain "$@"', 1)[0] + "\n")
        release = self.root / "release"
        release.joinpath("scripts").mkdir(parents=True)
        (release / "scripts/pv2hash-self-update").write_text("#!/bin/sh\necho new\n")
        installed = self.root / "installed-helper"
        installed.write_text("#!/bin/sh\necho old\n")
        old_fd = os.open(installed, os.O_RDONLY)
        old_inode = installed.stat().st_ino
        fake_install = self.bin / "install"
        fake_install.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "-d" ]; then exit 0; fi\n'
            'exec /usr/bin/install "$@"\n'
        )
        fake_install.chmod(0o755)
        fake_visudo = self.bin / "visudo"
        fake_visudo.write_text("#!/bin/sh\nexit 0\n")
        fake_visudo.chmod(0o755)
        try:
            command = (
                'source "$INSTALL_FUNCTIONS"; '
                'RELEASE_DIR="$MOCK_RELEASE"; '
                'SELF_UPDATE_HELPER_PATH="$MOCK_INSTALLED_HELPER"; '
                'SELF_UPDATE_SUDOERS_FILE="$MOCK_SUDOERS"; '
                'APP_USER="$USER"; '
                'install_self_update_helper'
            )
            result = subprocess.run(
                ["bash", "-c", command],
                env={
                    **self.env,
                    "INSTALL_FUNCTIONS": str(funcs),
                    "MOCK_RELEASE": str(release),
                    "MOCK_INSTALLED_HELPER": str(installed),
                    "MOCK_SUDOERS": str(self.root / "sudoers"),
                },
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(installed.stat().st_ino, old_inode)
            self.assertIn("echo new", installed.read_text())
            self.assertIn("echo old", os.read(old_fd, 100).decode())
            self.assertFalse(list(self.root.glob("installed-helper.*")))
        finally:
            os.close(old_fd)

    def test_termination_records_failure_and_cleans_lock(self):
        ready = self.root / "installer-ready"
        self.write_installer(f"touch {ready}\nsleep 4")
        first = subprocess.Popen(
            ["bash", str(self.helper), "--worker", "v0.9.4"],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(35):
                if ready.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(ready.exists())
            first.terminate()
            self.assertNotEqual(first.wait(timeout=8), 0)
            self.assertEqual(self.state()["status"], "error")
            self.assertFalse((self.data / "self_update.lock").exists())
        finally:
            if first.poll() is None:
                first.kill()
                first.wait()


if __name__ == "__main__":
    unittest.main()
