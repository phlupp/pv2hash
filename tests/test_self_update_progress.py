"""Release-stage progress and Debian preflight; no root or network."""
from __future__ import annotations
import ast
import json
import os
import shlex
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from pv2hash.self_update import SelfUpdateManager

ROOT = Path(__file__).resolve().parents[1]

class SelfUpdateProgressTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manager = SelfUpdateManager(
            current_version="0.9.3",
            install_info_file=self.base / "install.env",
            helper_path=self.base / "helper",
            state_file=self.base / "self_update_status.json",
            lock_dir=self.base / "lock")
        self.state = {"status": "running", "target_tag": "v0.9.4",
                      "started_at": "2026-10-12T00:00:00Z"}

    def test_phase_visible_only_for_current_update(self):
        f = self.manager.state_file.with_name("self_update_phase.json")
        row = dict(target_tag="v0.9.4", started_at="2026-10-12T00:00:00Z",
                   step=5, total=7, phase="Python-Umgebung einrichten")
        f.write_text(json.dumps(row))
        data = self.manager._read_update_phase(self.state)
        self.assertEqual(data["phase_step"], 5)
        self.assertIn("Schritt 5 von 7", data["phase_text"])
        self.assertFalse(self.manager._read_update_phase({**self.state, "target_tag": "v0.9.5"}))
        self.assertFalse(self.manager._read_update_phase({**self.state, "started_at": "other"}))
        self.assertFalse(self.manager._read_update_phase({**self.state, "status": "success"}))
        row["step"] = 10
        f.write_text(json.dumps(row))
        self.assertFalse(self.manager._read_update_phase(self.state))
        f.write_text("{broken")
        self.assertFalse(self.manager._read_update_phase(self.state))

    def test_percent_counts_completed_steps_not_elapsed_time(self):
        tree = ast.parse((ROOT / "pv2hash/app.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "_update_progress_value")
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<progress>", "exec"), namespace)
        calc = namespace["_update_progress_value"]
        self.assertEqual(calc({"status": "starting"}), 0)
        self.assertIsNone(calc({"status": "running"}))
        self.assertEqual(calc({"status": "running", "phase_step": 1, "phase_total": 7}), 0)
        self.assertEqual(calc({"status": "running", "phase_step": 5, "phase_total": 7}), 57)
        self.assertEqual(calc({"status": "success"}), 100)
        self.assertIsNone(calc({"status": "error"}))

    def test_unknown_and_failed_progress_not_reported_as_complete(self):
        # Source-level regression: no JS engine required on the Debian test VM.
        script = (ROOT / "pv2hash/static/js/app.js").read_text()
        formatter = script.split("function systemSetProgress(", 1)[1].split(
            "function systemUpdateBadge(", 1)[0]
        self.assertIn("typeof value !== 'number'", formatter)
        self.assertIn("bar.removeAttribute('aria-valuenow')", formatter)
        overlay = script.split("function renderSystemUpdateOverlay(", 1)[1].split(
            "function systemUserIsEditingImportForm(", 1)[0]
        error_branch = overlay.split("if (runner?.status === 'error')", 1)[1]
        self.assertIn("systemSetProgress(fill, percent, null);", error_branch)
        self.assertNotIn("runner.progress_percent ?? 100", error_branch)
        success_branch = overlay.split("if (runner?.status === 'success')", 1)[1].split(
            "if (runner?.status === 'error')", 1)[0]
        self.assertIn("runner.progress_percent ?? 100", success_branch)

    def test_standalone_progress_page_removed_with_compat_redirect(self):
        backend = (ROOT / "pv2hash/app.py").read_text()
        js = (ROOT / "pv2hash/static/js/app.js").read_text()
        page = (ROOT / "pv2hash/templates/system.html").read_text()
        css = (ROOT / "pv2hash/static/css/app.css").read_text()
        self.assertFalse((ROOT / "pv2hash/templates/update_progress.html").exists())
        self.assertNotIn('name="update_progress.html"', backend)
        self.assertIn('return RedirectResponse(url="/system", status_code=303)', backend)
        self.assertIn('response_payload["progress_url"] = "/system"', backend)
        self.assertNotIn("/system/update-progress", js)
        self.assertNotIn("/system/update-progress", page)
        self.assertNotIn("Fortschrittsseite öffnen", js + page)
        self.assertNotIn(".update-progress-shell", css)

    def test_running_overlay_has_no_page_button_and_keeps_exit_on_error(self):
        js = (ROOT / "pv2hash/static/js/app.js").read_text()
        overlay = js.split("function renderSystemUpdateOverlay(", 1)[1].split(
            "function systemUserIsEditingImportForm(", 1)[0]
        running = overlay.split("if (running) {", 1)[1].split(
            "if (runner?.status === 'success')", 1)[0]
        self.assertIn("actionContainer.hidden = true;", running)
        self.assertIn("actionLink.hidden = true;", running)
        success = overlay.split("if (runner?.status === 'success')", 1)[1].split(
            "if (runner?.status === 'error')", 1)[0]
        failed = overlay.split("if (runner?.status === 'error')", 1)[1]
        self.assertIn("actionContainer.hidden = false;", success)
        self.assertIn("actionContainer.hidden = false;", failed)
        self.assertIn("actionLink.hidden = false;", success)
        self.assertIn("actionLink.hidden = false;", failed)
        self.assertIn("actionLink.href = '/system';", failed)

    def installer_functions(self):
        installer = (ROOT / "scripts/install_release.sh").read_text()
        self.assertEqual(installer.count('\nmain "$@"'), 1)
        f = self.base / "installer-functions.sh"
        f.write_text(installer.rsplit('\nmain "$@"', 1)[0] + "\n")
        return f

    def mock_package_tools(self, missing):
        bin_dir = self.base / "bin"
        bin_dir.mkdir(exist_ok=True)
        dpkg = bin_dir / "dpkg-query"
        dpkg.write_text('''#!/bin/sh
for arg in "$@"; do pkg="$arg"; done
if [ "$pkg" = "$MISSING_PACKAGE" ]; then exit 1; fi
printf "install ok installed"
''')
        apt = bin_dir / "apt-get"
        apt.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$APT_CALLS"\n')
        dpkg.chmod(0o755)
        apt.chmod(0o755)
        return {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                "MISSING_PACKAGE": missing, "APT_CALLS": str(self.base / "calls")}

    def test_installed_system_packages_skip_apt(self):
        env = self.mock_package_tools("")
        p = subprocess.run(["bash", "-c", f"source {shlex.quote(str(self.installer_functions()))}; install_system_packages"],
                           env={**os.environ, **env}, capture_output=True, text=True, check=True)
        self.assertIn("übersprungen", p.stdout)
        self.assertFalse((self.base / "calls").exists())

    def test_missing_system_package_uses_apt(self):
        env = self.mock_package_tools("python3-venv")
        subprocess.run(["bash", "-c", f"source {shlex.quote(str(self.installer_functions()))}; install_system_packages"],
                       env={**os.environ, **env}, capture_output=True, text=True, check=True)
        self.assertEqual((self.base / "calls").read_text().splitlines(),
                         ["update", "install -y python3-venv"])

    def test_installer_phase_is_atomic_and_tagged(self):
        script = self.installer_functions()
        phase = self.base / "self_update_phase.json"
        env = {**os.environ, "PV2HASH_UPDATE_PHASE_FILE": str(phase),
               "PV2HASH_UPDATE_PHASE_TAG": "v0.9.4",
               "PV2HASH_UPDATE_STARTED_AT": "2026-10-12T00:00:00Z"}
        subprocess.run(["bash", "-c", f"source {shlex.quote(str(script))}; report_update_phase 4 'Release-Dateien entpacken'"],
                       env=env, capture_output=True, text=True, check=True)
        doc = json.loads(phase.read_text())
        self.assertEqual((doc["step"], doc["total"], doc["target_tag"]), (4, 7, "v0.9.4"))
        self.assertEqual(doc["phase"], "Release-Dateien entpacken")
        self.assertEqual(phase.stat().st_mode & 0o777, 0o644)
        self.assertEqual(list(self.base.glob(".update-phase-*")), [])

if __name__ == "__main__":
    unittest.main()
