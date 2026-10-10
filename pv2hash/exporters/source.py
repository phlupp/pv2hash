"""Read-only export contract: live snapshot, raw samples and control events."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any, Callable
from fastapi.encoders import jsonable_encoder

class ExportSource:
    schema_version = 1

    def __init__(self, db_path: str | Path, snapshot_provider: Callable[[], dict[str, Any]]):
        self.db_path = Path(db_path)
        self.snapshot_provider = snapshot_provider

    def snapshot(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "kind": "snapshot",
                "data": jsonable_encoder(self.snapshot_provider())}

    def _rows(self, sql: str, params: tuple) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def samples(self, *, after: str | None = None, limit: int = 120) -> dict[str, Any]:
        """Export original DataLogger samples; cursor is exclusive UTC ISO timestamp."""
        limit = max(1, min(int(limit), 1000))
        rows = self._rows("SELECT * FROM history_samples WHERE ts > ? ORDER BY ts LIMIT ?", (after or "", limit))
        miners = self._rows("SELECT * FROM history_miner_samples WHERE ts BETWEEN ? AND ? ORDER BY ts, miner_id",
                            (rows[0]["ts"], rows[-1]["ts"])) if rows else []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for miner in miners:
            grouped.setdefault(miner["ts"], []).append(miner)
        for row in rows:
            row["miners"] = grouped.get(row["ts"], [])
        return self._batch("samples", rows, after, "ts")

    def controller_events(self, *, after_id: int = 0, limit: int = 120) -> dict[str, Any]:
        """Read controller event log; cursor is exclusive monotonically increasing ID."""
        limit = max(1, min(int(limit), 1000))
        rows = self._rows("SELECT * FROM controller_events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit))
        return self._batch("controller_events", rows, after_id, "id")

    def pool_state(self) -> dict[str, Any]:
        """Current last-known pool identities (small, complete inventory).

        A periodic inventory also bootstraps an exporter added after some pool
        events have expired under the DataLogger's history retention policy.
        There is no password in either SQLite pool table.
        """
        rows = self._rows(
            "SELECT instance_id, miner_id, pool_slot, host, port, username, "
            "is_active, first_seen_at, last_seen_at "
            "FROM miner_pools ORDER BY miner_id, pool_slot", (),
        )
        return {"schema_version": self.schema_version, "kind": "pool_state",
                "items": rows, "count": len(rows)}

    def pool_events(self, *, after_id: int = 0, limit: int = 120) -> dict[str, Any]:
        """Durable change-event stream; cursor is exclusive numeric event ID."""
        limit = max(1, min(int(limit), 1000))
        rows = self._rows(
            "SELECT id, ts, instance_id, miner_id, pool_slot, event_type, "
            "host, port, username, is_active "
            "FROM miner_pool_events WHERE id > ? ORDER BY id LIMIT ?",
            (after_id, limit),
        )
        return self._batch("pool_events", rows, after_id, "id")

    def _batch(self, kind: str, rows: list[dict[str, Any]], after: Any, field: str) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "kind": kind, "items": rows,
                "count": len(rows), "next_cursor": rows[-1][field] if rows else after}
