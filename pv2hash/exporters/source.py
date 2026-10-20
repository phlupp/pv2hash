"""Read-only export contract: live snapshot, raw samples and control events."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any, Callable
from fastapi.encoders import jsonable_encoder

class ExportSource:
    schema_version = 1

    def __init__(self, db_path: str | Path, snapshot_provider: Callable[[], dict[str, Any]],
                 *, forecast_db_path: str | Path | None = None):
        self.db_path = Path(db_path)
        self.snapshot_provider = snapshot_provider
        self.forecast_db_path = Path(forecast_db_path) if forecast_db_path else None

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

    def forecast_quality(self, *, after_id: int = 0, limit: int = 120) -> dict[str, Any]:
        """Export in insert order, not by valid_at: late samples are expected."""
        limit = max(1, min(int(limit), 1000))
        path = self.forecast_db_path
        if path is None or not path.exists():
            return self._batch('forecast_quality', [], after_id, 'quality_id')
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            try:
                rows = [dict(row) for row in db.execute("""SELECT quality_id,
                    valid_at,horizon_minutes,issued_at,forecast_w,actual_w,error_w
                    FROM forecast_quality_samples WHERE quality_id > ?
                    ORDER BY quality_id LIMIT ?""",(after_id,limit))]
            except sqlite3.OperationalError as exc:
                if 'no such table: forecast_quality_samples' not in str(exc):
                    raise
                rows=[]
        return self._batch('forecast_quality',rows,after_id,'quality_id')

    def forecast_shadow_quality(self, *, after_id: int = 0, limit: int = 120) -> dict[str, Any]:
        """Append-only, paired LIVE comparisons, with monotonic export cursor.

        Never export speculative or retrospectively reconstructed forecasts.
        No source writes or network operations; independent of local learning.
        """
        limit = max(1, min(int(limit), 1000))
        path = self.forecast_db_path
        if path is None or not path.exists():
            return self._batch('forecast_shadow_quality', [], after_id, 'quality_id')
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            try:
                rows = [dict(row) for row in db.execute("""SELECT q.quality_id,
                    q.valid_at,q.horizon_minutes,q.issued_at,
                    q.baseline_revision_id,q.config_hash,q.baseline_w,q.shadow_w,
                    q.actual_w,q.evaluated_at,
                    m.model_version,m.status AS model_status,m.factor AS model_factor,
                    m.sample_count AS training_samples,m.day_count AS training_days,
                    m.plant_summary
                    FROM forecast_shadow_quality_samples AS q
                    LEFT JOIN forecast_shadow_models AS m ON m.model_id=q.model_id
                    WHERE q.quality_id>? ORDER BY q.quality_id LIMIT ?""",
                    (after_id, limit))]
            except sqlite3.OperationalError as exc:
                if 'no such table:' not in str(exc):
                    raise
                rows = []
        import json
        # Non-identifying, allowlisted aggregate plant characteristics only.
        summary_fields = ("capacity_kwp","generator_count","inverter_count",
                          "tilt_weighted_deg","loss_weighted_pct",
                          "east_kwp","south_kwp","west_kwp","north_kwp")
        for row in rows:
            try:
                summary = json.loads(row.pop('plant_summary') or '{}')
                if not isinstance(summary, dict):
                    summary = {}
            except (TypeError, ValueError):
                summary = {}
            row.update({k:summary[k] for k in summary_fields
                        if k in summary and isinstance(summary[k],(int,float))
                        and not isinstance(summary[k],bool)})
        return self._batch('forecast_shadow_quality', rows, after_id, 'quality_id')

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
