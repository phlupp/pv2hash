"""Offline rollback/health-gate tests for the release installer. No root needed."""
from __future__ import annotations

import json
import shlex
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def shell_functions(folder: Path) -> Path:
    src = (ROOT / "scripts/install_release.sh").read_text()
    assert src.endswith('main "$@"\n')
    p = folder / "functions.sh"
    p.write_text(src.rsplit('main "$@"', 1)[0])
    return p


class InstallerTransactionTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.fn = shell_functions(self.base)

    def run_shell(self, script: str, *, expect_success=True):
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        if expect_success:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def test_rollback_restores_release_and_mutated_files(self):
        base = shlex.quote(str(self.base))
        source = shlex.quote(str(self.fn))
        script = f"""
set -euo pipefail
source {source}
BASE={base}
APP_USER="$(id -un)"
APP_GROUP="$(id -gn)"
APP_ROOT="$BASE/opt"
RELEASES_DIR="$APP_ROOT/releases"
CURRENT_LINK="$APP_ROOT/current"
RELEASE_DIR="$RELEASES_DIR/new-package"
TMP_DIR="$BASE/tmp"
CONFIG_DIR="$BASE/etc"
INSTALL_INFO_FILE="$CONFIG_DIR/install.env"
SERVICE_FILE="$CONFIG_DIR/pv2hash.service"
SELF_UPDATE_HELPER_PATH="$CONFIG_DIR/pv2hash-self-update"
SELF_UPDATE_SUDOERS_FILE="$CONFIG_DIR/pv2hash.sudoers"
SERVICE_NAME=pv2hash-mock
mkdir -p "$RELEASES_DIR/old" "$RELEASE_DIR" "$CONFIG_DIR" "$TMP_DIR"
STAGED_RELEASE_CREATED=1
printf 'old config\n' > "$INSTALL_INFO_FILE"
printf 'old unit\n' > "$SERVICE_FILE"
printf 'old helper\n' > "$SELF_UPDATE_HELPER_PATH"
printf 'old sudoers\n' > "$SELF_UPDATE_SUDOERS_FILE"
ln -s "$RELEASES_DIR/old" "$CURRENT_LINK"
systemctl() {{ echo "MOCK systemctl $*" >> "$BASE/commands"; }}
backup_current_install
printf 'broken config\n' > "$INSTALL_INFO_FILE"
printf 'broken unit\n' > "$SERVICE_FILE"
printf 'broken helper\n' > "$SELF_UPDATE_HELPER_PATH"
printf 'broken sudoers\n' > "$SELF_UPDATE_SUDOERS_FILE"
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK.new"
mv -Tf "$CURRENT_LINK.new" "$CURRENT_LINK"
rollback_current_install
test "$(readlink "$CURRENT_LINK")" = "$RELEASES_DIR/old"
test "$(cat "$INSTALL_INFO_FILE")" = "old config"
test "$(cat "$SERVICE_FILE")" = "old unit"
test "$(cat "$SELF_UPDATE_HELPER_PATH")" = "old helper"
test "$(cat "$SELF_UPDATE_SUDOERS_FILE")" = "old sudoers"
test ! -e "$RELEASE_DIR"
grep -q 'restart pv2hash-mock.service' "$BASE/commands"
"""
        self.run_shell(script)

    def test_health_gate_rejects_same_core_wrong_package_id(self):
        source = shlex.quote(str(self.fn))
        script = f"""
set -euo pipefail
source {source}
TAG_NAME=r2026.10.18.1
FULL_VERSION=0.9.5
GUI_VERSION=2.0.1
PORT=18766
curl() {{ printf '%s' '{{"status":"ok","release_id":"v0.9.5","core_version":"0.9.5","gui_version":"2.0.0"}}'; }}
sleep() {{ :; }}
if verify_running_release; then
    echo "BAD identity was accepted" >&2; exit 1
fi
curl() {{ printf '%s' '{{"status":"ok","release_id":"r2026.10.18.1","core_version":"0.9.5","gui_version":"2.0.1"}}'; }}
verify_running_release
"""
        self.run_shell(script)


if __name__ == "__main__":
    unittest.main()
