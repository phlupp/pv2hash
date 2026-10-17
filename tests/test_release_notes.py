"""Release notes are curated, non-placeholder and tied to the package ID."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.release_notes import validate_release_notes


class ReleaseNotesTest(unittest.TestCase):
    def test_bridge_release_has_real_curated_notes(self):
        path = Path(__file__).resolve().parents[1] / "docs/releases/v0.9.5.md"
        body = validate_release_notes("v0.9.5", path)
        self.assertIn("## Neue Funktionen", body)
        self.assertIn("## Fehlerbehebungen", body)
        self.assertIn("## Verbesserungen", body)
        self.assertIn("AxeOS", body)
        self.assertIn("Update-Manager", body)

    def test_missing_notes_must_block_release(self):
        with TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "fehlen"):
                validate_release_notes("r2026.10.18.1", Path(d) / "r2026.10.18.1.md")

    def test_release_id_mismatch_and_placeholder_are_rejected(self):
        with TemporaryDirectory() as d:
            file = Path(d) / "r2026.10.18.1.md"
            file.write_text("# PV2Hash r2026.10.18.2\n\n## Fehlerbehebungen\n\n- Korrektur\n")
            with self.assertRaisesRegex(ValueError, "Erste Zeile"):
                validate_release_notes("r2026.10.18.1", file)
            file.write_text("# PV2Hash r2026.10.18.1\n\n## Fehlerbehebungen\n\n- TODO\n")
            with self.assertRaisesRegex(ValueError, "Platzhalter"):
                validate_release_notes("r2026.10.18.1", file)

    def test_valid_fixes_only_notes_are_allowed(self):
        with TemporaryDirectory() as d:
            file = Path(d) / "r2026.10.18.2.md"
            file.write_text("# PV2Hash r2026.10.18.2\n\n## Fehlerbehebungen\n\n- Fehler beim Treiberstart korrigiert.\n")
            self.assertIn("Treiberstart", validate_release_notes("r2026.10.18.2", file))


if __name__ == "__main__":
    unittest.main()
