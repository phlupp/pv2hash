from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

LOGGER_DB_PATH = Path("data/history.sqlite")
_ALLOWED_INTERVAL_SECONDS = {10, 30, 60}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _int_bool(value: Any) -> int:
    return 1 if bool(value) else 0



def _parse_range_seconds(value: str | None) -> tuple[str, int]:
    raw = str(value or "24h").strip().lower()
    allowed = {
        "1h": 3600,
        "6h": 6 * 3600,
        "12h": 12 * 3600,
        "24h": 24 * 3600,
        "7d": 7 * 24 * 3600,
    }
    if raw not in allowed:
        raw = "24h"
    return raw, allowed[raw]


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _avg(values: list[float | None]) -> float | None:
    cleaned = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not cleaned:
        return None
    return sum(cleaned) / len(cleaned)


def _last_text(values: list[Any]) -> str | None:
    for value in reversed(values):
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return None

def normalize_datalogger_config(config: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(config or {})
    enabled = bool(raw.get("enabled", True))

    try:
        interval_seconds = int(raw.get("interval_seconds", 10))
    except Exception:
        interval_seconds = 10
    if interval_seconds not in _ALLOWED_INTERVAL_SECONDS:
        interval_seconds = 10 if interval_seconds < 30 else 30 if interval_seconds < 60 else 60
        if interval_seconds not in _ALLOWED_INTERVAL_SECONDS:
            interval_seconds = 10

    try:
        retention_days = int(raw.get("retention_days", 7))
    except Exception:
        retention_days = 7
    retention_days = max(1, min(30, retention_days))

    return {
        "enabled": enabled,
        "interval_seconds": interval_seconds,
        "retention_days": retention_days,
    }


@dataclass
class DataLoggerStatus:
    enabled: bool
    interval_seconds: int
    retention_days: int
    database_path: str
    database_size_bytes: int
    sample_count: int
    miner_sample_count: int
    event_count: int
    oldest_sample_at: str | None
    newest_sample_at: str | None
    last_sample_at: str | None
    last_error: str | None


class DataLogger:
    def __init__(
        self,
        *,
        config_provider: Callable[[], dict[str, Any]],
        snapshot_provider: Callable[[], dict[str, Any]],
        db_path: Path = LOGGER_DB_PATH,
    ) -> None:
        self._config_provider = config_provider
        self._snapshot_provider = snapshot_provider
        self._db_path = db_path
        self._last_sample_at: str | None = None
        self._last_error: str | None = None
        self._last_retention_at: datetime | None = None
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _config(self) -> dict[str, Any]:
        config = self._config_provider() or {}
        return normalize_datalogger_config(config.get("datalogger", {}))

    async def run(self) -> None:
        await asyncio.to_thread(self._ensure_schema)
        while not self._stop_event.is_set():
            cfg = self._config()
            if cfg["enabled"]:
                try:
                    snapshot = self._snapshot_provider()
                    await asyncio.to_thread(self._write_snapshot, snapshot, cfg)
                    self._last_sample_at = _now_iso()
                    self._last_error = None
                except Exception as exc:  # pragma: no cover - logged by caller too
                    self._last_error = str(exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=float(cfg["interval_seconds"]))
            except asyncio.TimeoutError:
                pass

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self._db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=3000")
        return con

    def _ensure_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS history_samples (
                    ts TEXT PRIMARY KEY,
                    instance_id TEXT,
                    grid_power_w REAL,
                    source_quality TEXT,
                    battery_quality TEXT,
                    battery_soc_pct REAL,
                    battery_charge_power_w REAL,
                    battery_discharge_power_w REAL,
                    battery_is_charging INTEGER,
                    battery_is_discharging INTEGER,
                    miner_power_w_total REAL,
                    miner_hashrate_ghs_total REAL,
                    control_enabled_miner_count INTEGER,
                    monitor_enabled_miner_count INTEGER,
                    reachable_miner_count INTEGER,
                    controller_summary TEXT,
                    controller_last_decision TEXT,
                    host_cpu_percent REAL,
                    host_memory_percent REAL,
                    host_disk_percent REAL,
                    host_uptime_seconds REAL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS history_miner_samples (
                    ts TEXT,
                    instance_id TEXT,
                    miner_id TEXT,
                    miner_key TEXT,
                    name TEXT,
                    driver TEXT,
                    profile TEXT,
                    power_w REAL,
                    hashrate_ghs REAL,
                    reachable INTEGER,
                    monitor_enabled INTEGER,
                    control_enabled INTEGER,
                    runtime_state TEXT,
                    PRIMARY KEY (ts, miner_id)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS history_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    level TEXT NOT NULL,
                    type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    object_type TEXT,
                    object_id TEXT,
                    payload_json TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_history_miner_samples_ts ON history_miner_samples(ts)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_history_events_ts ON history_events(ts)")

    def _write_snapshot(self, snapshot: dict[str, Any], cfg: dict[str, Any]) -> None:
        self._ensure_schema()
        ts = _to_iso(snapshot.get("timestamp")) or _now_iso()
        instance = snapshot.get("instance") or {}
        host = snapshot.get("host") or {}
        source = snapshot.get("source") or {}
        battery = snapshot.get("battery") or {}
        controller = snapshot.get("controller") or {}
        totals = snapshot.get("totals") or {}
        instance_id = str(instance.get("id") or "")

        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO history_samples (
                    ts, instance_id, grid_power_w, source_quality, battery_quality,
                    battery_soc_pct, battery_charge_power_w, battery_discharge_power_w,
                    battery_is_charging, battery_is_discharging,
                    miner_power_w_total, miner_hashrate_ghs_total,
                    control_enabled_miner_count, monitor_enabled_miner_count, reachable_miner_count,
                    controller_summary, controller_last_decision,
                    host_cpu_percent, host_memory_percent, host_disk_percent, host_uptime_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    instance_id,
                    _float_or_none(source.get("grid_power_w")),
                    source.get("quality"),
                    battery.get("quality"),
                    _float_or_none(battery.get("soc_pct")),
                    _float_or_none(battery.get("charge_power_w")),
                    _float_or_none(battery.get("discharge_power_w")),
                    _int_bool(battery.get("is_charging")),
                    _int_bool(battery.get("is_discharging")),
                    _float_or_none(totals.get("miner_power_w")),
                    _float_or_none(totals.get("miner_hashrate_ghs")),
                    int(totals.get("control_enabled_miner_count") or 0),
                    int(totals.get("monitor_enabled_miner_count") or 0),
                    int(totals.get("reachable_miner_count") or 0),
                    controller.get("summary"),
                    controller.get("last_decision"),
                    _float_or_none(host.get("cpu_percent")),
                    _float_or_none(host.get("memory_percent")),
                    _float_or_none(host.get("disk_percent")),
                    _float_or_none(host.get("uptime_seconds")),
                ),
            )

            for miner in snapshot.get("miners") or []:
                miner_id = str(miner.get("id") or miner.get("key") or "")
                if not miner_id:
                    continue
                con.execute(
                    """
                    INSERT OR REPLACE INTO history_miner_samples (
                        ts, instance_id, miner_id, miner_key, name, driver, profile,
                        power_w, hashrate_ghs, reachable, monitor_enabled,
                        control_enabled, runtime_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        instance_id,
                        miner_id,
                        miner.get("key"),
                        miner.get("name"),
                        miner.get("driver"),
                        miner.get("profile"),
                        _float_or_none(miner.get("power_w")),
                        _float_or_none(miner.get("hashrate_ghs")),
                        _int_bool(miner.get("reachable")),
                        _int_bool(miner.get("monitor_enabled")),
                        _int_bool(miner.get("control_enabled")),
                        miner.get("runtime_state"),
                    ),
                )

            self._apply_retention(con, cfg)

    def _apply_retention(self, con: sqlite3.Connection, cfg: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        if self._last_retention_at and (now - self._last_retention_at) < timedelta(minutes=10):
            return
        self._last_retention_at = now
        cutoff = (now - timedelta(days=int(cfg["retention_days"]))).isoformat()
        con.execute("DELETE FROM history_samples WHERE ts < ?", (cutoff,))
        con.execute("DELETE FROM history_miner_samples WHERE ts < ?", (cutoff,))
        con.execute("DELETE FROM history_events WHERE ts < ?", (cutoff,))

    def status(self) -> dict[str, Any]:
        cfg = self._config()
        self._ensure_schema()
        database_size = self._db_path.stat().st_size if self._db_path.exists() else 0
        with self._connect() as con:
            sample_count = int(con.execute("SELECT COUNT(*) FROM history_samples").fetchone()[0] or 0)
            miner_sample_count = int(con.execute("SELECT COUNT(*) FROM history_miner_samples").fetchone()[0] or 0)
            event_count = int(con.execute("SELECT COUNT(*) FROM history_events").fetchone()[0] or 0)
            oldest_sample_at = con.execute("SELECT MIN(ts) FROM history_samples").fetchone()[0]
            newest_sample_at = con.execute("SELECT MAX(ts) FROM history_samples").fetchone()[0]
        return {
            "enabled": cfg["enabled"],
            "interval_seconds": cfg["interval_seconds"],
            "retention_days": cfg["retention_days"],
            "database_path": str(self._db_path),
            "database_size_bytes": database_size,
            "sample_count": sample_count,
            "miner_sample_count": miner_sample_count,
            "event_count": event_count,
            "oldest_sample_at": oldest_sample_at,
            "newest_sample_at": newest_sample_at,
            "last_sample_at": self._last_sample_at,
            "last_error": self._last_error,
        }

    def series(self, *, range_name: str = "24h", max_points: int = 720) -> dict[str, Any]:
        self._ensure_schema()
        selected_range, range_seconds = _parse_range_seconds(range_name)
        max_points = max(120, min(1200, int(max_points or 720)))
        end = datetime.now(UTC)
        start = end - timedelta(seconds=range_seconds)
        start_iso = start.isoformat()
        end_iso = end.isoformat()

        columns = (
            "ts",
            "grid_power_w",
            "source_quality",
            "battery_quality",
            "battery_soc_pct",
            "battery_charge_power_w",
            "battery_discharge_power_w",
            "miner_power_w_total",
            "miner_hashrate_ghs_total",
            "control_enabled_miner_count",
            "monitor_enabled_miner_count",
            "reachable_miner_count",
            "controller_summary",
            "controller_last_decision",
            "host_cpu_percent",
            "host_memory_percent",
            "host_disk_percent",
        )
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                f"""
                SELECT {', '.join(columns)}
                FROM history_samples
                WHERE ts >= ? AND ts <= ?
                ORDER BY ts ASC
                """,
                (start_iso, end_iso),
            ).fetchall()

        raw_rows = [dict(row) for row in rows]
        points = self._downsample_rows(raw_rows, max_points=max_points, start=start, end=end)
        return {
            "range": selected_range,
            "range_seconds": range_seconds,
            "start": start_iso,
            "end": end_iso,
            "raw_count": len(raw_rows),
            "point_count": len(points),
            "max_points": max_points,
            "points": points,
        }

    def _downsample_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        max_points: int,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        if len(rows) <= max_points:
            return [self._normalize_series_point(row) for row in rows]

        total_seconds = max(1.0, (end - start).total_seconds())
        bucket_seconds = max(1.0, total_seconds / float(max_points))
        buckets: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            ts_dt = _parse_iso_datetime(row.get("ts"))
            if ts_dt is None:
                continue
            bucket = int(max(0, min(max_points - 1, (ts_dt - start).total_seconds() // bucket_seconds)))
            buckets.setdefault(bucket, []).append(row)

        points: list[dict[str, Any]] = []
        for bucket in sorted(buckets):
            bucket_rows = buckets[bucket]
            if not bucket_rows:
                continue
            last = bucket_rows[-1]
            points.append({
                "ts": last.get("ts"),
                "grid_power_w": _avg([_float_or_none(row.get("grid_power_w")) for row in bucket_rows]),
                "battery_soc_pct": _avg([_float_or_none(row.get("battery_soc_pct")) for row in bucket_rows]),
                "battery_charge_power_w": _avg([_float_or_none(row.get("battery_charge_power_w")) for row in bucket_rows]),
                "battery_discharge_power_w": _avg([_float_or_none(row.get("battery_discharge_power_w")) for row in bucket_rows]),
                "miner_power_w_total": _avg([_float_or_none(row.get("miner_power_w_total")) for row in bucket_rows]),
                "miner_hashrate_ghs_total": _avg([_float_or_none(row.get("miner_hashrate_ghs_total")) for row in bucket_rows]),
                "host_cpu_percent": _avg([_float_or_none(row.get("host_cpu_percent")) for row in bucket_rows]),
                "host_memory_percent": _avg([_float_or_none(row.get("host_memory_percent")) for row in bucket_rows]),
                "host_disk_percent": _avg([_float_or_none(row.get("host_disk_percent")) for row in bucket_rows]),
                "source_quality": _last_text([row.get("source_quality") for row in bucket_rows]),
                "battery_quality": _last_text([row.get("battery_quality") for row in bucket_rows]),
                "control_enabled_miner_count": int(last.get("control_enabled_miner_count") or 0),
                "monitor_enabled_miner_count": int(last.get("monitor_enabled_miner_count") or 0),
                "reachable_miner_count": int(last.get("reachable_miner_count") or 0),
                "controller_summary": last.get("controller_summary"),
                "controller_last_decision": last.get("controller_last_decision"),
            })
        return points

    def _normalize_series_point(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "ts": row.get("ts"),
            "grid_power_w": _float_or_none(row.get("grid_power_w")),
            "battery_soc_pct": _float_or_none(row.get("battery_soc_pct")),
            "battery_charge_power_w": _float_or_none(row.get("battery_charge_power_w")),
            "battery_discharge_power_w": _float_or_none(row.get("battery_discharge_power_w")),
            "miner_power_w_total": _float_or_none(row.get("miner_power_w_total")),
            "miner_hashrate_ghs_total": _float_or_none(row.get("miner_hashrate_ghs_total")),
            "host_cpu_percent": _float_or_none(row.get("host_cpu_percent")),
            "host_memory_percent": _float_or_none(row.get("host_memory_percent")),
            "host_disk_percent": _float_or_none(row.get("host_disk_percent")),
            "source_quality": row.get("source_quality"),
            "battery_quality": row.get("battery_quality"),
            "control_enabled_miner_count": int(row.get("control_enabled_miner_count") or 0),
            "monitor_enabled_miner_count": int(row.get("monitor_enabled_miner_count") or 0),
            "reachable_miner_count": int(row.get("reachable_miner_count") or 0),
            "controller_summary": row.get("controller_summary"),
            "controller_last_decision": row.get("controller_last_decision"),
        }
