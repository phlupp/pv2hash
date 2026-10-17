"""Bounded, read-only access to the PV2Hash installer console.

Never accept a filesystem path from the browser. The download endpoint
uses only the dedicated installer log in the configured data root.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

LOG_LIMIT = 48 * 1024
_SECRET = [
    re.compile(r"(?i)((?:token|password|passwd|api[_-]?key|secret)\s*[:=]\s*)([^\s&;]+)"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)([^\s]+)"),
]


def redact_line(text: str) -> str:
    for pattern in _SECRET:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


def update_log_path(data_dir: Path) -> Path:
    return data_dir.parent / "logs" / "self_update.log"


def log_chunk(path: Path, offset: int = 0, file_id: str = "") -> dict:
    """Incremental UTF-8 text; read at most LOG_LIMIT bytes per request.

    First load returns the tail; next loads use the byte cursor. A rotated or
    truncated logfile instructs clients to replace their console contents.
    """
    if offset < 0:
        raise ValueError("Ungültiger Log-Offset")
    try:
        info=path.stat()
        if not path.is_file() or path.is_symlink():
            raise OSError("Logdatei muss eine reguläre Datei sein")
    except FileNotFoundError:
        return {"status":"ok","available":False,"lines":[],"offset":0,"file_id":"","reset":True,"truncated":False}
    identifier=f"{info.st_dev}:{info.st_ino}"
    reset=(file_id and file_id!=identifier) or offset>info.st_size or offset==0
    if reset:
        start=max(0,info.st_size-LOG_LIMIT)
    else:
        start=offset
        if info.st_size-start>LOG_LIMIT:
            start=info.st_size-LOG_LIMIT
            reset=True
    truncated=start>0 and (reset or offset==0)
    with path.open("rb") as f:
        f.seek(start)
        if start and reset and truncated:
            f.readline() # Drop partial opening line only on a fresh tail.
        lines=f.read(LOG_LIMIT).decode("utf-8",errors="replace").splitlines()
        end=f.tell()
    return {
        "status":"ok","available":True,
        "lines":[redact_line(line) for line in lines],
        "offset":end,"file_id":identifier,
        "reset":bool(reset),"truncated":truncated,
    }
