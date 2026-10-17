"""Running package identity is shared by the system API and historical export."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pv2hash.build_identity import read_build_identity
from pv2hash.version import APP_VERSION_FULL


class BuildIdentityTest(unittest.TestCase):
    def test_complete_package_identity_from_embedded_file(self):
        with TemporaryDirectory() as directory:
            info = Path(directory) / "build_info.json"
            info.write_text(json.dumps({
                "release_id": "r2026.10.18.1", "core_version": "0.9.5",
                "gui_version": "2.0.1",
            }))
            self.assertEqual(read_build_identity(info), {
                "release_id": "r2026.10.18.1", "core_version": "0.9.5",
                "gui_version": "2.0.1",
            })

    def test_bridge_release_identity(self):
        with TemporaryDirectory() as directory:
            info = Path(directory) / "build_info.json"
            info.write_text(json.dumps({
                "release_id": "v0.9.5", "core_version": "0.9.5",
                "gui_version": "2.0.0",
            }))
            self.assertEqual(read_build_identity(info)["release_id"], "v0.9.5")

    def test_legacy_or_corrupt_build_does_not_invent_package_id(self):
        with TemporaryDirectory() as directory:
            info = Path(directory) / "build_info.json"
            expected = {
                "release_id": None, "core_version": APP_VERSION_FULL,
                "gui_version": None,
            }
            self.assertEqual(read_build_identity(info), expected)
            for contents in ('{}', 'null', '{broken json', '[]'):
                with self.subTest(contents=contents):
                    info.write_text(contents)
                    self.assertEqual(read_build_identity(info), expected)

    def test_api_reports_the_same_identity_as_exporter(self):
        from pv2hash import app as app_module
        payload = {
            "release_id": "r2026.10.18.1",
            "core_version": "0.9.5",
            "gui_version": "2.0.1",
        }
        with patch.object(app_module, "read_build_identity", return_value=payload):
            self.assertEqual(app_module._read_build_info(), {"status": "ok", **payload})


if __name__ == "__main__":
    unittest.main()
