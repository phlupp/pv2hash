from __future__ import annotations

import sys
from pathlib import Path


def ensure_braiins_stubs_on_path() -> Path:
    """
    Adds the generated Braiins gRPC stub directory to sys.path if needed.

    The generated files live in:
        pv2hash/vendor/braiins_api_stubs/bos/...

    The generated imports expect "bos.*" to be importable as a top-level package,
    so we add the parent directory "braiins_api_stubs" to sys.path.
    """
    stubs_root = Path(__file__).resolve().parent / "braiins_api_stubs"
    stubs_root_str = str(stubs_root)

    if stubs_root.exists() and stubs_root_str not in sys.path:
        sys.path.insert(0, stubs_root_str)

    return stubs_root