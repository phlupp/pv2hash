"""PV2Hash legacy-to-package migration and safe update-console reader."""
from __future__ import annotations
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pv2hash.release_identity import parse_release_id, is_newer_release, installed_release_id
from pv2hash.self_update import SelfUpdateManager
from pv2hash.update_check import _parse_release_tag
from pv2hash.update_log import log_chunk, redact_line


class ReleaseIdentityTest(unittest.TestCase):
    def test_bridge_and_first_package(self):
        self.assertEqual(parse_release_id("v0.9.5").version, "0.9.5")
        self.assertEqual(parse_release_id("r2026.10.18.1").kind, "package")
        self.assertTrue(is_newer_release("v0.9.5", installed_release=None, current_core="0.9.4"))
        self.assertTrue(is_newer_release("r2026.10.18.1", installed_release="v0.9.5",current_core="0.9.5"))
        self.assertTrue(is_newer_release("r2026.10.18.2",installed_release="r2026.10.18.1",current_core="0.9.5"))
        self.assertFalse(is_newer_release("r2026.10.18.1",installed_release="r2026.10.18.1",current_core="0.9.5"))
        self.assertFalse(is_newer_release("v0.9.5",installed_release="r2026.10.18.1",current_core="0.9.5"))
        self.assertEqual(_parse_release_tag("r2026.10.18.1")["release_id"],"r2026.10.18.1")

    def test_rejects_bad_dates_and_formats(self):
        for raw in ["r2026.02.31.1","r2026.10.18.0","r2026.10.18.01","r2026.10.18.1/../etc",
                    "v0.9.5-malicious", "r2026.1.1.1", "r2026.13.10.1"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):parse_release_id(raw)

    def test_installed_release_id_comes_only_from_install_info(self):
        with TemporaryDirectory() as d:
            f=Path(d)/"install.env"
            self.assertIsNone(installed_release_id(f))
            f.write_text("PV2HASH_VERSION=0.9.5\nPV2HASH_RELEASE_ID=v0.9.5\n")
            self.assertEqual(installed_release_id(f),"v0.9.5")
            f.write_text("PV2HASH_RELEASE_ID=r2026.10.18.1\n")
            self.assertEqual(installed_release_id(f),"r2026.10.18.1")

    def test_recovery_requires_matching_package_not_unchanged_core(self):
        with TemporaryDirectory() as d:
            base=Path(d)
            install=base/"install.env"
            install.write_text("PV2HASH_INSTALL_MODE=release\nPV2HASH_RELEASE_ID=v0.9.5\n")
            manager=SelfUpdateManager(
                current_version="0.9.5",install_info_file=install,
                helper_path=base/"helper",state_file=base/"status.json",lock_dir=base/"lock")
            self.assertEqual(manager._parse_release_tag("r2026.10.18.1"),
                             ("r2026.10.18.1","r2026.10.18.1"))
            manager._write_state(status="running",message="Läuft",target_tag="r2026.10.18.1",
                                 target_version_full="0.9.5",started_at=manager._now_iso(),
                                 finished_at=None,last_error=None,updated_version_full=None)
            (base/"lock").mkdir()
            state=manager._recover_state(manager._read_state())
            self.assertEqual(state["status"],"running") # unchanged core is not success
            install.write_text("PV2HASH_INSTALL_MODE=release\nPV2HASH_RELEASE_ID=r2026.10.18.1\n")
            state=manager._recover_state(manager._read_state())
            self.assertEqual(state["status"],"success")


class InstallLogTest(unittest.TestCase):
    def test_cursor_append_and_rotation(self):
        with TemporaryDirectory() as d:
            f=Path(d)/"self_update.log"
            self.assertFalse(log_chunk(f)["available"])
            f.write_text("Step one\nStep two\n")
            x=log_chunk(f)
            self.assertEqual(x["lines"],["Step one","Step two"])
            with f.open("a") as out:out.write("Step three\n")
            y=log_chunk(f,x["offset"],x["file_id"])
            self.assertEqual(y["lines"],["Step three"])
            self.assertFalse(y["reset"])
            f.unlink();f.write_text("New run\n")
            z=log_chunk(f,y["offset"],y["file_id"])
            self.assertEqual(z["lines"],["New run"])
            self.assertTrue(z["reset"])

    def test_secret_masking(self):
        secret="THIS_IS_A_SECRET_VALUE"
        for line in [f"token={secret}",f"PASSWORD: {secret}",f"Authorization: Bearer {secret}"]:
            self.assertNotIn(secret,redact_line(line))


if __name__=="__main__":unittest.main()
