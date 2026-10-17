"""Validate the human-written release notes used by the release pipeline.

The same Markdown is delivered as the GitHub release body, as a release
asset, and in the install archive. Keep it short and factual; no auto-
generated commit dump and no placeholder notes in published releases.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pv2hash.release_identity import parse_release_id

SECTIONS = {"Neue Funktionen", "Fehlerbehebungen", "Verbesserungen"}
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|FIXME|PLATZHALTER)\b", re.IGNORECASE)


def validate_release_notes(tag: str, path: Path) -> str:
    identity = parse_release_id(tag)
    if path.name != f"{identity.tag}.md":
        raise ValueError(f"Release Notes müssen nach der Release-ID benannt sein: {identity.tag}.md")
    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"Release Notes fehlen: {path}") from exc
    lines = body.splitlines()
    if not lines or lines[0].strip() != f"# PV2Hash {identity.tag}":
        raise ValueError(f"Erste Zeile muss '# PV2Hash {identity.tag}' sein.")
    if PLACEHOLDER.search(body):
        raise ValueError("Release Notes enthalten noch Platzhalter.")
    sections: dict[str, int] = {}
    current = None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            if current in sections:
                raise ValueError(f"Release-Notes-Abschnitt doppelt: {current}")
            sections[current] = 0
        elif stripped.startswith(("- ", "* ")) and current:
            sections[current] += 1
    if not (SECTIONS & sections.keys()):
        raise ValueError("Es fehlen Abschnitte für Funktionen, Fehlerbehebungen oder Verbesserungen.")
    for name in SECTIONS & sections.keys():
        if sections[name] < 1:
            raise ValueError(f"Abschnitt '{name}' benötigt mindestens einen Stichpunkt.")
    if sum(sections[k] for k in SECTIONS & sections.keys()) < 1:
        raise ValueError("Release Notes enthalten keine relevanten Stichpunkte.")
    return body + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Aufruf: python3 scripts/release_notes.py TAG docs/releases/TAG.md")
    try:
        text = validate_release_notes(sys.argv[1], Path(sys.argv[2]))
    except ValueError as exc:
        raise SystemExit(f"Fehler in Release Notes: {exc}") from exc
    print(f"Release Notes gültig: {sys.argv[1]} ({len(text.splitlines())} Zeilen)")
