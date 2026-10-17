"""PV2Hash package release identity.

The 0.9.5 bridge remains a traditional Core SemVer release (v0.9.5).
Following releases use opaque package IDs rYYYY.MM.DD.N, independent
from their Core and GUI component versions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

LEGACY = re.compile(r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-build\.(?P<build>\d+))?$")
PACKAGE = re.compile(r"^r(?P<year>20\d{2})\.(?P<month>0?[1-9]|1[012])\.(?P<day>0?[1-9]|[12]\d|3[01])\.(?P<seq>[1-9]\d*)$")


@dataclass(frozen=True)
class ReleaseId:
    tag: str
    kind: str
    release_id: str
    version: str | None
    order: tuple[int, ...]

    @property
    def slug(self) -> str:
        return self.tag[1:]


def parse_release_id(raw: str) -> ReleaseId:
    tag = str(raw or "").strip()
    m = LEGACY.fullmatch(tag)
    if m:
        version = ".".join((m["major"], m["minor"], m["patch"]))
        return ReleaseId(
            tag=tag if tag.startswith("v") else "v"+tag,
            kind="legacy", release_id="v"+version, version=version,
            order=(0,int(m["major"]),int(m["minor"]),int(m["patch"]),int(m["build"] or 0)),
        )
    m = PACKAGE.fullmatch(tag)
    if m:
        from datetime import date
        year, month, day, seq = (int(m[key]) for key in ("year","month","day","seq"))
        date(year, month, day)  # Reject impossible calendar dates.
        canonical = f"r{year:04d}.{month:02d}.{day:02d}.{seq}"
        if canonical != tag:
            raise ValueError(f"Nicht kanonische Paket-ID: {tag}")
        return ReleaseId(canonical,"package",canonical,None,(1,year,month,day,seq))
    raise ValueError(f"Unbekannte Release-ID: {raw!r}")


def is_newer_release(remote_tag: str, *, installed_release: str | None, current_core: str) -> bool:
    remote=parse_release_id(remote_tag)
    if installed_release:
        local=parse_release_id(installed_release)
        return remote.order>local.order
    if remote.kind=="package":
        # A 0.9.5 installation without persisted ID may still find the first
        # package; the bridge's installer will persist its ID on future updates.
        return True
    local=parse_release_id(current_core)
    return remote.order>local.order


def installed_release_id(path: Path) -> str | None:
    try:
        text=path.read_text(encoding="utf-8")
    except OSError:
        return None
    for row in text.splitlines():
        key, sep, value=row.strip().partition("=")
        if sep and key=="PV2HASH_RELEASE_ID":
            try:
                return parse_release_id(value.strip().strip('"').strip("'")).release_id
            except ValueError:
                return None
    return None
