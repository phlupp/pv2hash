"""Regression for release validation: never pipe tar into early-closing grep -q."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def archive_check_script() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index("      - name: Validate complete package and checksums")
    begin = lines.index("        run: |", start) + 1
    script: list[str] = []
    for line in lines[begin:]:
        if line.startswith("      - name: "):
            break
        if line.startswith("          "):
            script.append(line[10:])
        elif not line.strip():
            script.append("")
        else:
            raise AssertionError(f"Unexpected workflow indentation: {line!r}")
    return "\n".join(script) + "\n"


def prepare_fixture(base: Path, *, assets: bool = True, bad_hash: bool = False) -> None:
    dist = base / "dist" / "0.9.5"
    docs = base / "docs" / "releases"
    dist.mkdir(parents=True)
    docs.mkdir(parents=True)
    notes = "# PV2Hash v0.9.5\n\n## Verbesserungen\n\n- Regressionstest.\n"
    (docs / "v0.9.5.md").write_text(notes)
    (dist / "RELEASE_NOTES.md").write_text(notes)
    (dist / "manifest.json").write_text('{"tag":"v0.9.5"}\n')
    with tarfile.open(dist / "pv2hash-0.9.5.tar.gz", "w:gz") as archive:
        def add(name: str, payload: bytes) -> None:
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            archive.addfile(entry, io.BytesIO(payload))

        add("pv2hash-0.9.5/pv2hash/gui/index.html", b"<html/>")
        if assets:
            # Larger listings reproduce the original early grep -q/SIGPIPE.
            for number in range(2500):
                add(f"pv2hash-0.9.5/pv2hash/gui/assets/{number:04d}.js", b"x")
        add("pv2hash-0.9.5/RELEASE_NOTES.md", notes.encode())

    lines = []
    for name in ("pv2hash-0.9.5.tar.gz", "manifest.json", "RELEASE_NOTES.md"):
        data = (dist / name).read_bytes()
        if bad_hash and name == "manifest.json":
            data = b"wrong-content"
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    (dist / "SHA256SUMS").write_text("\n".join(lines) + "\n")


class ArchiveValidationTest(unittest.TestCase):
    def run_validator(self, *, assets: bool = True, bad_hash: bool = False) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            prepare_fixture(base, assets=assets, bad_hash=bad_hash)
            env = dict(os.environ)
            env.update(DIST_DIR="dist/0.9.5", VERSION_SLUG="0.9.5", TAG="v0.9.5")
            return subprocess.run(
                ["bash", "-euo", "pipefail", "-c", archive_check_script()],
                cwd=base, env=env, capture_output=True, text=True, timeout=15,
            )

    def test_large_valid_archive_does_not_fail_sigpipe(self):
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pv2hash-0.9.5.tar.gz: OK", result.stdout)

    def test_missing_gui_assets_rejected(self):
        self.assertNotEqual(self.run_validator(assets=False).returncode, 0)

    def test_wrong_checksum_rejected(self):
        self.assertNotEqual(self.run_validator(bad_hash=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
