"""Regression: GitHub's GITHUB_ENV is only visible to *later* workflow steps.

The release validator needs its tag in the same Bash step that writes
GITHUB_ENV. Execute the real extracted script with no GitHub secrets or
network calls to catch 'TAG: unbound variable' before release publication.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def validation_script() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index("      - name: Validate component versions and release identity")
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


class ReleaseWorkflowTagEnvironmentTest(unittest.TestCase):
    def run_validation(self, release_id: str, core: str = "0.999.998") -> tuple[subprocess.CompletedProcess, str]:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.update(
                INPUT_CORE=core,
                INPUT_GUI="2.0.0",
                INPUT_RELEASE_ID=release_id,
                GITHUB_ENV=str(Path(tmp) / "github_env"),
            )
            result = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", validation_script()],
                cwd=ROOT, env=env, capture_output=True, text=True,
                timeout=10,
            )
            data = Path(env["GITHUB_ENV"])
            return result, data.read_text() if data.exists() else ""

    def test_legacy_release_tag_is_available_in_same_step(self) -> None:
        result, env_file = self.run_validation("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TAG=v0.999.998\n", env_file)

    def test_package_release_id_is_available_in_same_step(self) -> None:
        result, env_file = self.run_validation("r2099.01.01.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TAG=r2099.01.01.1\n", env_file)

    def test_unknown_tag_is_rejected_before_publication(self) -> None:
        result, _ = self.run_validation("not-a-release")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
