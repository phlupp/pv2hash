from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER_DB_PATH = Path("data/history.sqlite")
PORTAL_AGGREGATION_SECONDS = 60
PORTAL_BUCKET_LIMIT = 120
RAW_SAMPLE_FETCH_LIMIT = 5000

_NUMERIC_FIELDS = [
    "grid_power_w",
    "battery_soc_pct",
    "battery_charge_power_w",
    "battery_discharge_power_w",
    "miner_power_w_total",
    "miner_hashrate_ghs_total",
    "control_enabled_miner_count",
    "monitor_enabled_miner_count",
    "reachable_miner_count",
    "host_cpu_percent",
    "host_memory_percent",
    "host_disk_percent",
    "host_uptime_seconds",
]

_MINER_NUMERIC_FIELDS = [
    "power_w",
    "hashrate_ghs",
    "temp_c",
    "temp_asic_min_c",
    "temp_asic_max_c",
]

_MINER_BOOLEAN_FIELDS = ["reachable", "monitor_enabled", "control_enabled"]


def _connect_logger_db() -> sqlite3.Connection:
    LOGGER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(LOGGER_DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=3000")
    return con


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return set()
    return {str(row[1]) for row in rows}


def _ensure_datalogger_portal_schema(con: sqlite3.Connection) -> None:
    columns = _table_columns(con, "history_samples")
    if not columns:
        return
    if "portal_sent_at" not in columns:
        con.execute("ALTER TABLE history_samples ADD COLUMN portal_sent_at TEXT")
    if "upload_attempts" not in columns:
        con.execute("ALTER TABLE history_samples ADD COLUMN upload_attempts INTEGER NOT NULL DEFAULT 0")
    if "last_upload_error" not in columns:
        con.execute("ALTER TABLE history_samples ADD COLUMN last_upload_error TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_history_samples_portal ON history_samples(portal_sent_at, ts)")


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _avg(values: list[Any]) -> float | None:
    nums = [_float_or_none(value) for value in values]
    nums = [value for value in nums if value is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _majority_bool(values: list[Any]) -> bool:
    if not values:
        return False
    yes = sum(1 for value in values if bool(value))
    return yes >= (len(values) / 2)


def _last_non_empty(values: list[Any]) -> Any:
    for value in reversed(values):
        if value not in (None, ""):
            return value
    return None


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _bucket_start(dt: datetime) -> datetime:
    epoch = int(dt.timestamp())
    bucket_epoch = epoch - (epoch % PORTAL_AGGREGATION_SECONDS)
    return datetime.fromtimestamp(bucket_epoch, UTC)


def _current_bucket_start() -> datetime:
    return _bucket_start(datetime.now(UTC))


def _valid_battery_soc(value: Any) -> float | None:
    number = _float_or_none(value)
    if number is None:
        return None
    if number < 0.0 or number > 150.0:
        return None
    return number


def _row_get(row: sqlite3.Row, name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except Exception:
        return default


def _aggregate_miners(raw_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    by_miner: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in raw_rows:
        miner_id = str(_row_get(row, "miner_id", ""))
        if miner_id:
            by_miner[miner_id].append(row)

    miners: list[dict[str, Any]] = []
    for miner_id, rows in sorted(by_miner.items(), key=lambda item: item[0]):
        first = rows[0]
        last = rows[-1]
        item = {
            "id": miner_id,
            "key": _last_non_empty([_row_get(row, "miner_key") for row in rows]),
            "name": _last_non_empty([_row_get(row, "name") for row in rows]),
            "driver": _last_non_empty([_row_get(row, "driver") for row in rows]),
            "profile": _last_non_empty([_row_get(row, "profile") for row in rows]),
            "runtime_state": _last_non_empty([_row_get(row, "runtime_state") for row in rows]),
            "sample_count": len(rows),
        }
        for field in _MINER_NUMERIC_FIELDS:
            item[field] = _avg([_row_get(row, field) for row in rows])
        for field in _MINER_BOOLEAN_FIELDS:
            item[field] = _majority_bool([_row_get(row, field) for row in rows])
        miners.append(item)
    return miners


def _aggregate_bucket(bucket_start: datetime, rows: list[sqlite3.Row], miner_rows: list[sqlite3.Row]) -> dict[str, Any]:
    bucket_end = datetime.fromtimestamp(bucket_start.timestamp() + PORTAL_AGGREGATION_SECONDS, UTC)
    sample_ids = [str(row["ts"]) for row in rows]
    payload: dict[str, Any] = {
        "sample_id": f"{bucket_start.isoformat()}/{bucket_end.isoformat()}",
        "sample_ids": sample_ids,
        "ts": bucket_start.isoformat(),
        "bucket_start": bucket_start.isoformat(),
        "bucket_end": bucket_end.isoformat(),
        "aggregation_seconds": PORTAL_AGGREGATION_SECONDS,
        "aggregation": "avg_1m",
        "raw_sample_count": len(rows),
        "instance_id": _last_non_empty([row["instance_id"] for row in rows]),
        "source_quality": _last_non_empty([row["source_quality"] for row in rows]),
        "battery_quality": _last_non_empty([row["battery_quality"] for row in rows]),
        "battery_is_charging": _majority_bool([row["battery_is_charging"] for row in rows]),
        "battery_is_discharging": _majority_bool([row["battery_is_discharging"] for row in rows]),
        "controller_summary": _last_non_empty([row["controller_summary"] for row in rows]),
        "controller_last_decision": _last_non_empty([row["controller_last_decision"] for row in rows]),
        "miners": _aggregate_miners(miner_rows),
    }
    for field in _NUMERIC_FIELDS:
        if field == "battery_soc_pct":
            payload[field] = _avg([_valid_battery_soc(row[field]) for row in rows])
        else:
            payload[field] = _avg([row[field] for row in rows])
    return payload


def unsent_samples_for_portal(limit: int = PORTAL_BUCKET_LIMIT) -> list[dict[str, Any]]:
    limit = max(1, min(500, int(limit or PORTAL_BUCKET_LIMIT)))
    if not LOGGER_DB_PATH.exists():
        return []

    current_bucket = _current_bucket_start()
    with _connect_logger_db() as con:
        con.row_factory = sqlite3.Row
        _ensure_datalogger_portal_schema(con)
        if "portal_sent_at" not in _table_columns(con, "history_samples"):
            return []
        rows = con.execute(
            """
            SELECT *
            FROM history_samples
            WHERE portal_sent_at IS NULL
            ORDER BY ts ASC
            LIMIT ?
            """,
            (RAW_SAMPLE_FETCH_LIMIT,),
        ).fetchall()

        buckets: dict[datetime, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            parsed = _parse_ts(row["ts"])
            if parsed is None:
                continue
            bucket = _bucket_start(parsed)
            if bucket >= current_bucket:
                # Keep the currently active minute local until it is complete.
                continue
            buckets[bucket].append(row)

        selected_buckets = sorted(buckets.keys())[:limit]
        if not selected_buckets:
            return []

        selected_ts: list[str] = []
        for bucket in selected_buckets:
            selected_ts.extend(str(row["ts"]) for row in buckets[bucket])
        placeholders = ",".join("?" for _ in selected_ts)
        miner_rows = con.execute(
            f"""
            SELECT *
            FROM history_miner_samples
            WHERE ts IN ({placeholders})
            ORDER BY ts ASC, miner_id ASC
            """,
            selected_ts,
        ).fetchall()

    miners_by_bucket: dict[datetime, list[sqlite3.Row]] = defaultdict(list)
    for row in miner_rows:
        parsed = _parse_ts(row["ts"])
        if parsed is None:
            continue
        bucket = _bucket_start(parsed)
        if bucket in selected_buckets:
            miners_by_bucket[bucket].append(row)

    return [_aggregate_bucket(bucket, buckets[bucket], miners_by_bucket.get(bucket, [])) for bucket in selected_buckets]


def _raw_ids_from_sample_ids(sample_ids: list[str]) -> list[str]:
    raw_ids: list[str] = []
    for item in sample_ids:
        if not item:
            continue
        if "/" in str(item):
            # Aggregate IDs are informational. The raw ids are carried in
            # sample["sample_ids"] and are passed by the wrapper below.
            continue
        raw_ids.append(str(item))
    return raw_ids


def mark_samples_uploaded(sample_ids: list[str]) -> None:
    ids = _raw_ids_from_sample_ids(sample_ids)
    if not ids:
        return
    now = datetime.now(UTC).isoformat()
    with _connect_logger_db() as con:
        _ensure_datalogger_portal_schema(con)
        con.executemany(
            "UPDATE history_samples SET portal_sent_at = ?, last_upload_error = NULL WHERE ts = ?",
            [(now, item) for item in ids],
        )


def mark_samples_failed(sample_ids: list[str], error: str) -> None:
    ids = _raw_ids_from_sample_ids(sample_ids)
    if not ids:
        return
    message = str(error or "Portal upload failed")[:500]
    with _connect_logger_db() as con:
        _ensure_datalogger_portal_schema(con)
        con.executemany(
            "UPDATE history_samples SET upload_attempts = upload_attempts + 1, last_upload_error = ? WHERE ts = ?",
            [(message, item) for item in ids],
        )


def has_more_unsent(limit: int = PORTAL_BUCKET_LIMIT) -> bool:
    limit = max(1, min(500, int(limit or PORTAL_BUCKET_LIMIT)))
    if not LOGGER_DB_PATH.exists():
        return False
    current_bucket = _current_bucket_start()
    with _connect_logger_db() as con:
        con.row_factory = sqlite3.Row
        _ensure_datalogger_portal_schema(con)
        if "portal_sent_at" not in _table_columns(con, "history_samples"):
            return False
        rows = con.execute(
            """
            SELECT ts
            FROM history_samples
            WHERE portal_sent_at IS NULL
            ORDER BY ts ASC
            LIMIT ?
            """,
            (RAW_SAMPLE_FETCH_LIMIT,),
        ).fetchall()
    buckets: set[datetime] = set()
    for row in rows:
        parsed = _parse_ts(row["ts"])
        if parsed is None:
            continue
        bucket = _bucket_start(parsed)
        if bucket < current_bucket:
            buckets.add(bucket)
    return len(buckets) > limit


def install() -> None:
    from pv2hash import local_instance_extensions as ext
    from pv2hash.datalogger import DataLogger

    ext._unsent_datalogger_samples_for_portal = unsent_samples_for_portal
    ext._mark_datalogger_samples_uploaded = mark_samples_uploaded
    ext._mark_datalogger_samples_failed = mark_samples_failed
    ext._datalogger_has_more_unsent = has_more_unsent

    DataLogger.unsent_samples_for_portal = staticmethod(unsent_samples_for_portal)
    DataLogger.mark_samples_uploaded = staticmethod(mark_samples_uploaded)
    DataLogger.mark_samples_upload_failed = staticmethod(mark_samples_failed)
