from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

INSTANCE_PATH = Path("data/instance.json")


@dataclass(frozen=True)
class InstanceIdentity:
    id: str
    created_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "created_at": self.created_at,
        }


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _new_identity() -> InstanceIdentity:
    return InstanceIdentity(id=str(uuid4()), created_at=_utc_now_iso())


def load_instance_identity() -> InstanceIdentity:
    """Load or create the stable identity for this PV2Hash installation.

    The identity is stored outside the normal configuration so config imports or
    exports do not accidentally clone or overwrite the installation identity.
    """
    INSTANCE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if INSTANCE_PATH.exists():
        try:
            with INSTANCE_PATH.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            identity_id = str(raw.get("id") or "").strip()
            created_at = str(raw.get("created_at") or "").strip()
            if identity_id and created_at:
                return InstanceIdentity(id=identity_id, created_at=created_at)
        except Exception:
            # Fall through and recreate below. A broken identity file should not
            # prevent PV2Hash from starting.
            pass

    identity = _new_identity()
    save_instance_identity(identity)
    return identity


def save_instance_identity(identity: InstanceIdentity) -> None:
    INSTANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INSTANCE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(identity.as_dict(), handle, indent=2, ensure_ascii=False)
        handle.write("\n")
