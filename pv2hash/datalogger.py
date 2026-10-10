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
    raw = str(value or "1h").strip().lower()
    allowed = {
        "1h": 3600,
        "3h": 3 * 3600,
        "6h": 6 * 3600,
        "12h": 12 * 3600,
        "24h": 24 * 3600,
        "7d": 7 * 24 * 3600,
    }
    if raw not in allowed:
        raw = "1h"
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



_PROFILE_ORDER = {"off": 0, "p0": 1, "p1": 2, "p2": 3, "p3": 4, "p4": 5}


def _profile_rank(value: Any) -> int | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in _PROFILE_ORDER:
        return _PROFILE_ORDER[text]
    if text.startswith("p"):
        try:
            return int(text[1:]) + 1
        except Exception:
            return None
    return None


def _profile_change_direction(old_profile: Any, new_profile: Any) -> str:
    old_rank = _profile_rank(old_profile)
    new_rank = _profile_rank(new_profile)
    if old_rank is None or new_rank is None or old_rank == new_rank:
        return "neutral"
    return "up" if new_rank > old_rank else "down"


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

def _max(values: list[float | None]) -> float | None:
    cleaned = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return max(cleaned) if cleaned else None


def _min(values: list[float | None]) -> float | None:
    cleaned = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return min(cleaned) if cleaned else None


def _parse_id_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).split(',')
    seen: set[str] = set()
    result: list[str] = []
    for item in raw_values:
        text = str(item or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result

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


def _controller_debug_retention_hours(config: dict[str, Any] | None) -> int:
    raw = dict(config or {})
    try:
        value = int(raw.get("controller_debug_retention_hours", 48))
    except Exception:
        value = 48
    return max(1, min(168, value))


def _controller_debug_throttle_seconds(config: dict[str, Any] | None) -> int:
    raw = dict(config or {})
    try:
        value = int(raw.get("controller_debug_throttle_seconds", 60))
    except Exception:
        value = 60
    return max(5, min(3600, value))


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
            self._ensure_column(con, "history_miner_samples", "temp_c", "REAL")
            self._ensure_column(con, "history_miner_samples", "temp_asic_min_c", "REAL")
            self._ensure_column(con, "history_miner_samples", "temp_asic_max_c", "REAL")
            con.execute("CREATE INDEX IF NOT EXISTS idx_history_miner_samples_ts ON history_miner_samples(ts)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_history_miner_samples_miner_ts ON history_miner_samples(miner_id, ts)")
            # Last successfully observed Stratum pools. Unavailable device data
            # does not remove rows; a confirmed empty list does.
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS miner_pools (
                    miner_id TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    pool_slot INTEGER NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER,
                    username TEXT NOT NULL,
                    is_active INTEGER,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (miner_id, pool_slot)
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_miner_pools_username ON miner_pools(username)")
            # Append only on configuration/active-slot changes, never per sample.
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS miner_pool_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    miner_id TEXT NOT NULL,
                    pool_slot INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER,
                    username TEXT NOT NULL,
                    is_active INTEGER
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_miner_pool_events_miner_ts ON miner_pool_events(miner_id, ts)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_history_events_ts ON history_events(ts)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS controller_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL DEFAULT 'applied',
                    miner_id TEXT,
                    miner_key TEXT,
                    miner_name TEXT,
                    old_profile TEXT,
                    requested_profile TEXT,
                    new_profile TEXT,
                    reason_code TEXT NOT NULL,
                    reason_text TEXT,
                    flags_json TEXT,
                    grid_power_w REAL,
                    battery_soc_pct REAL,
                    battery_direction TEXT,
                    battery_charge_power_w REAL,
                    battery_discharge_power_w REAL,
                    miner_power_w REAL,
                    policy_mode TEXT,
                    distribution_mode TEXT,
                    decision_context_json TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_controller_events_ts ON controller_events(ts)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS controller_debug_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    miner_id TEXT,
                    miner_key TEXT,
                    miner_name TEXT,
                    current_profile TEXT,
                    requested_profile TEXT,
                    effective_profile TEXT,
                    reason_code TEXT NOT NULL,
                    reason_text TEXT,
                    flags_json TEXT,
                    grid_power_w REAL,
                    battery_soc_pct REAL,
                    battery_direction TEXT,
                    battery_charge_power_w REAL,
                    battery_discharge_power_w REAL,
                    miner_power_w REAL,
                    policy_mode TEXT,
                    distribution_mode TEXT,
                    min_switch_remaining_s REAL,
                    decision_context_json TEXT
                    dedupe_key TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_controller_debug_events_ts ON controller_debug_events(ts)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_controller_debug_events_dedupe ON controller_debug_events(dedupe_key, ts)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            con.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                ("datalogger_schema_version", "5"),
            )

    @staticmethod
    def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row[1]) for row in rows}

    def _ensure_column(self, con: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        if column not in self._table_columns(con, table):
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _sync_miner_pools(
        con: sqlite3.Connection,
        *,
        instance_id: str,
        miner_id: str,
        pools: list[dict[str, Any]] | None,
        ts: str,
    ) -> None:
        """Upsert current pools; record only changes to pool identity/status.

        None means no reliable readback (e.g. offline miner or unsupported
        driver) and MUST NOT delete prior observations. An explicit [] means
        the device was read successfully and reports no configured pools.
        """
        if pools is None:
            return

        normalized: dict[int, tuple[str, int | None, str, int | None]] = {}
        for pool in pools:
            slot = int(pool["slot"])
            if slot < 0 or slot in normalized:
                raise ValueError(f"Invalid/duplicate Stratum pool slot: {slot}")
            host = str(pool.get("host") or "").strip()
            if not host:
                raise ValueError(f"Missing Stratum pool host in slot {slot}")
            port = pool.get("port")
            port = int(port) if port not in (None, "") else None
            if port is not None and not 1 <= port <= 65535:
                raise ValueError(f"Invalid Stratum port in slot {slot}")
            username = str(pool.get("username") or "").strip()
            active = pool.get("is_active")
            is_active = None if active is None else int(bool(active))
            normalized[slot] = (host, port, username, is_active)

        old_rows = con.execute(
            "SELECT pool_slot, host, port, username, is_active, last_seen_at "
            "FROM miner_pools WHERE miner_id = ?",
            (miner_id,),
        ).fetchall()
        existing = {int(row[0]): row for row in old_rows}

        def event(slot: int, action: str, values: tuple) -> None:
            con.execute(
                "INSERT INTO miner_pool_events "
                "(ts, instance_id, miner_id, pool_slot, event_type, host, port, username, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, instance_id, miner_id, slot, action, *values),
            )

        for slot, values in normalized.items():
            old = existing.get(slot)
            if old is None:
                con.execute(
                    "INSERT INTO miner_pools "
                    "(miner_id, instance_id, pool_slot, host, port, username, is_active, "
                    "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (miner_id, instance_id, slot, *values, ts, ts),
                )
                event(slot, "added", values)
            elif tuple(old[1:5]) != values:
                con.execute(
                    "UPDATE miner_pools SET instance_id=?, host=?, port=?, username=?, "
                    "is_active=?, last_seen_at=? WHERE miner_id=? AND pool_slot=?",
                    (instance_id, *values, ts, miner_id, slot),
                )
                event(slot, "changed", values)
            else:
                # Keep a heartbeat without rewriting the entire table every 10s.
                prior = _parse_iso_datetime(old[5])
                current = _parse_iso_datetime(ts)
                if prior is None or current is None or (current - prior).total_seconds() >= 60:
                    con.execute(
                        "UPDATE miner_pools SET last_seen_at=? "
                        "WHERE miner_id=? AND pool_slot=?",
                        (ts, miner_id, slot),
                    )

        for slot, old in existing.items():
            if slot not in normalized:
                event(slot, "removed", tuple(old[1:5]))
                con.execute(
                    "DELETE FROM miner_pools WHERE miner_id=? AND pool_slot=?",
                    (miner_id, slot),
                )

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
                        power_w, hashrate_ghs, temp_c, temp_asic_min_c, temp_asic_max_c,
                        reachable, monitor_enabled, control_enabled, runtime_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        _float_or_none(miner.get("temp_c")),
                        _float_or_none(miner.get("temp_asic_min_c")),
                        _float_or_none(miner.get("temp_asic_max_c")),
                        _int_bool(miner.get("reachable")),
                        _int_bool(miner.get("monitor_enabled")),
                        _int_bool(miner.get("control_enabled")),
                        miner.get("runtime_state"),
                    ),
                )
                self._sync_miner_pools(
                    con, instance_id=instance_id, miner_id=miner_id,
                    pools=miner.get("pools"), ts=ts,
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
        con.execute("DELETE FROM miner_pool_events WHERE ts < ?", (cutoff,))
        con.execute("DELETE FROM history_events WHERE ts < ?", (cutoff,))
        con.execute("DELETE FROM controller_events WHERE ts < ?", (cutoff,))
        debug_retention_hours = _controller_debug_retention_hours((self._config_provider() or {}).get("datalogger", {}))
        debug_cutoff = (now - timedelta(hours=debug_retention_hours)).isoformat()
        con.execute("DELETE FROM controller_debug_events WHERE ts < ?", (debug_cutoff,))

    def record_controller_event(self, event: dict[str, Any]) -> int | None:
        """Persist one controller event best-effort.

        This method is intentionally independent from the controller loop. Callers may
        run it in a background thread/task; failures should be logged by the caller and
        must never influence controller decisions.
        """
        self._ensure_schema()
        ts = _to_iso(event.get("ts")) or _now_iso()
        flags = event.get("flags") if isinstance(event.get("flags"), list) else []
        context = event.get("decision_context") if isinstance(event.get("decision_context"), dict) else {}
        with self._connect() as con:
            cur = con.execute(
                """
                INSERT INTO controller_events (
                    ts, event_type, miner_id, miner_key, miner_name,
                    old_profile, requested_profile, new_profile,
                    reason_code, reason_text, flags_json,
                    grid_power_w, battery_soc_pct, battery_direction,
                    battery_charge_power_w, battery_discharge_power_w, miner_power_w,
                    policy_mode, distribution_mode, decision_context_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    str(event.get("event_type") or "applied"),
                    event.get("miner_id"),
                    event.get("miner_key"),
                    event.get("miner_name"),
                    event.get("old_profile"),
                    event.get("requested_profile"),
                    event.get("new_profile"),
                    str(event.get("reason_code") or "unknown"),
                    event.get("reason_text"),
                    json.dumps(flags, ensure_ascii=False, separators=(",", ":")),
                    _float_or_none(event.get("grid_power_w")),
                    _float_or_none(event.get("battery_soc_pct")),
                    event.get("battery_direction"),
                    _float_or_none(event.get("battery_charge_power_w")),
                    _float_or_none(event.get("battery_discharge_power_w")),
                    _float_or_none(event.get("miner_power_w")),
                    event.get("policy_mode"),
                    event.get("distribution_mode"),
                    json.dumps(context, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            return int(cur.lastrowid) if cur.lastrowid is not None else None

    def latest_controller_event(self, *, event_type: str = "applied") -> dict[str, Any] | None:
        """Return the newest controller event as a compact local API dict."""
        self._ensure_schema()
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                """
                SELECT *
                FROM controller_events
                WHERE event_type = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (str(event_type or "applied"),),
            ).fetchone()
        if row is None:
            return None
        return self._controller_event_row_to_item(dict(row))

    @staticmethod
    def _controller_event_row_to_item(row: dict[str, Any]) -> dict[str, Any]:
        try:
            flags = json.loads(row.get("flags_json") or "[]")
        except Exception:
            flags = []
        if not isinstance(flags, list):
            flags = []
        return {
            "event_id": int(row.get("id") or 0),
            "at": row.get("ts"),
            "event_type": row.get("event_type") or "applied",
            "miner_id": row.get("miner_id"),
            "miner_key": row.get("miner_key"),
            "miner_name": row.get("miner_name"),
            "old_profile": row.get("old_profile"),
            "requested_profile": row.get("requested_profile"),
            "new_profile": row.get("new_profile"),
            "reason_code": row.get("reason_code"),
            "reason_text": row.get("reason_text"),
            "flags": [str(flag) for flag in flags],
            "grid_power_w": row.get("grid_power_w"),
            "battery_soc_pct": row.get("battery_soc_pct"),
            "battery_direction": row.get("battery_direction"),
            "battery_charge_power_w": row.get("battery_charge_power_w"),
            "battery_discharge_power_w": row.get("battery_discharge_power_w"),
            "miner_power_w": row.get("miner_power_w"),
            "policy_mode": row.get("policy_mode"),
            "distribution_mode": row.get("distribution_mode"),
        }

    def record_controller_debug_event(self, event: dict[str, Any]) -> int | None:
        """Persist one local controller debug event best-effort with throttling.

        Debug events are local diagnostic data, throttled to limit disk use.
        """
        self._ensure_schema()
        cfg = (self._config_provider() or {}).get("datalogger", {})
        throttle_seconds = _controller_debug_throttle_seconds(cfg)
        ts = _to_iso(event.get("ts")) or _now_iso()
        flags = event.get("flags") if isinstance(event.get("flags"), list) else []
        context = event.get("decision_context") if isinstance(event.get("decision_context"), dict) else {}
        event_type = str(event.get("event_type") or "hold")
        dedupe_key = str(event.get("dedupe_key") or "").strip()
        if not dedupe_key:
            dedupe_parts = [
                event_type,
                str(event.get("miner_id") or event.get("miner_key") or "global"),
                str(event.get("current_profile") or ""),
                str(event.get("requested_profile") or ""),
                str(event.get("effective_profile") or ""),
                str(event.get("reason_code") or "unknown"),
                ",".join(sorted(str(flag) for flag in flags)),
            ]
            dedupe_key = "|".join(dedupe_parts)
        throttle_cutoff = (datetime.now(UTC) - timedelta(seconds=throttle_seconds)).isoformat()
        with self._connect() as con:
            existing = con.execute(
                """
                SELECT id
                FROM controller_debug_events
                WHERE dedupe_key = ? AND ts >= ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (dedupe_key, throttle_cutoff),
            ).fetchone()
            if existing is not None:
                return None
            cur = con.execute(
                """
                INSERT INTO controller_debug_events (
                    ts, event_type, miner_id, miner_key, miner_name,
                    current_profile, requested_profile, effective_profile,
                    reason_code, reason_text, flags_json,
                    grid_power_w, battery_soc_pct, battery_direction,
                    battery_charge_power_w, battery_discharge_power_w, miner_power_w,
                    policy_mode, distribution_mode, min_switch_remaining_s,
                    decision_context_json, dedupe_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    event_type,
                    event.get("miner_id"),
                    event.get("miner_key"),
                    event.get("miner_name"),
                    event.get("current_profile"),
                    event.get("requested_profile"),
                    event.get("effective_profile"),
                    str(event.get("reason_code") or "unknown"),
                    event.get("reason_text"),
                    json.dumps(flags, ensure_ascii=False, separators=(",", ":")),
                    _float_or_none(event.get("grid_power_w")),
                    _float_or_none(event.get("battery_soc_pct")),
                    event.get("battery_direction"),
                    _float_or_none(event.get("battery_charge_power_w")),
                    _float_or_none(event.get("battery_discharge_power_w")),
                    _float_or_none(event.get("miner_power_w")),
                    event.get("policy_mode"),
                    event.get("distribution_mode"),
                    _float_or_none(event.get("min_switch_remaining_s")),
                    json.dumps(context, ensure_ascii=False, separators=(",", ":")),
                    dedupe_key,
                ),
            )
            return int(cur.lastrowid) if cur.lastrowid is not None else None

    def controller_debug_events(
        self,
        *,
        range_name: str = "1h",
        limit: int = 200,
        miner_ids: Any = None,
        end_iso: str | None = None,
    ) -> dict[str, Any]:
        """Return local controller debug events for the selected time range."""
        self._ensure_schema()
        selected_range, range_seconds = _parse_range_seconds(range_name)
        limit = max(1, min(1000, int(limit or 200)))
        selected_miner_ids = _parse_id_csv(miner_ids)
        now = datetime.now(UTC)
        requested_end = _parse_iso_datetime(end_iso)
        end = requested_end if requested_end is not None else now
        if end > now:
            end = now
        start = end - timedelta(seconds=range_seconds)
        start_iso = start.isoformat()
        end_iso_out = end.isoformat()
        cfg = (self._config_provider() or {}).get("datalogger", {})
        retention_hours = _controller_debug_retention_hours(cfg)
        self._apply_controller_debug_retention(retention_hours=retention_hours)

        where = "ts >= ? AND ts <= ?"
        params: list[Any] = [start_iso, end_iso_out]
        if selected_miner_ids:
            placeholders = ','.join('?' for _ in selected_miner_ids)
            where += f" AND (miner_id IN ({placeholders}) OR miner_key IN ({placeholders}) OR miner_id IS NULL)"
            params.extend(selected_miner_ids)
            params.extend(selected_miner_ids)

        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                f"""
                SELECT *
                FROM controller_debug_events
                WHERE {where}
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        items = [self._controller_debug_event_row_to_item(dict(row)) for row in rows]
        return {
            "range": selected_range,
            "range_seconds": range_seconds,
            "start": start_iso,
            "end": end_iso_out,
            "is_live": requested_end is None,
            "retention_hours": retention_hours,
            "limit": limit,
            "selected_miner_ids": selected_miner_ids,
            "events": items,
            "event_count": len(items),
        }

    def _apply_controller_debug_retention(self, *, retention_hours: int | None = None) -> None:
        self._ensure_schema()
        hours = retention_hours if retention_hours is not None else _controller_debug_retention_hours((self._config_provider() or {}).get("datalogger", {}))
        cutoff = (datetime.now(UTC) - timedelta(hours=int(hours))).isoformat()
        with self._connect() as con:
            con.execute("DELETE FROM controller_debug_events WHERE ts < ?", (cutoff,))

    @staticmethod
    def _controller_debug_event_row_to_item(row: dict[str, Any]) -> dict[str, Any]:
        try:
            flags = json.loads(row.get("flags_json") or "[]")
        except Exception:
            flags = []
        if not isinstance(flags, list):
            flags = []
        return {
            "event_id": int(row.get("id") or 0),
            "at": row.get("ts"),
            "event_type": row.get("event_type") or "hold",
            "miner_id": row.get("miner_id"),
            "miner_key": row.get("miner_key"),
            "miner_name": row.get("miner_name"),
            "current_profile": row.get("current_profile"),
            "requested_profile": row.get("requested_profile"),
            "effective_profile": row.get("effective_profile"),
            "reason_code": row.get("reason_code"),
            "reason_text": row.get("reason_text"),
            "flags": [str(flag) for flag in flags],
            "grid_power_w": row.get("grid_power_w"),
            "battery_soc_pct": row.get("battery_soc_pct"),
            "battery_direction": row.get("battery_direction"),
            "battery_charge_power_w": row.get("battery_charge_power_w"),
            "battery_discharge_power_w": row.get("battery_discharge_power_w"),
            "miner_power_w": row.get("miner_power_w"),
            "policy_mode": row.get("policy_mode"),
            "distribution_mode": row.get("distribution_mode"),
            "min_switch_remaining_s": row.get("min_switch_remaining_s"),
        }

    def status(self) -> dict[str, Any]:
        cfg = self._config()
        self._ensure_schema()
        database_size = self._db_path.stat().st_size if self._db_path.exists() else 0
        with self._connect() as con:
            sample_count = int(con.execute("SELECT COUNT(*) FROM history_samples").fetchone()[0] or 0)
            miner_sample_count = int(con.execute("SELECT COUNT(*) FROM history_miner_samples").fetchone()[0] or 0)
            event_count = int(con.execute("SELECT COUNT(*) FROM history_events").fetchone()[0] or 0)
            controller_event_count = int(con.execute("SELECT COUNT(*) FROM controller_events").fetchone()[0] or 0)
            controller_debug_event_count = int(con.execute("SELECT COUNT(*) FROM controller_debug_events").fetchone()[0] or 0)
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
            "controller_event_count": controller_event_count,
            "controller_debug_event_count": controller_debug_event_count,
            "controller_debug_retention_hours": _controller_debug_retention_hours((self._config_provider() or {}).get("datalogger", {})),
            "oldest_sample_at": oldest_sample_at,
            "newest_sample_at": newest_sample_at,
            "last_sample_at": self._last_sample_at,
            "last_error": self._last_error,
        }

    def series(self, *, range_name: str = "1h", max_points: int = 720, miner_ids: Any = None, end_iso: str | None = None) -> dict[str, Any]:
        self._ensure_schema()
        selected_range, range_seconds = _parse_range_seconds(range_name)
        max_points = max(120, min(1200, int(max_points or 720)))
        selected_miner_ids = _parse_id_csv(miner_ids)
        now = datetime.now(UTC)
        requested_end = _parse_iso_datetime(end_iso)
        end = requested_end if requested_end is not None else now
        if end > now:
            end = now
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
        available_miners = self._available_miners(start_iso=start_iso, end_iso=end_iso)
        miner_aggregates = self._miner_aggregates_by_ts(
            start_iso=start_iso,
            end_iso=end_iso,
            miner_ids=selected_miner_ids if selected_miner_ids else None,
        )
        raw_rows = self._merge_miner_aggregates(raw_rows, miner_aggregates, override_totals=bool(selected_miner_ids))
        points = self._downsample_rows(raw_rows, max_points=max_points, start=start, end=end)
        markers = self._controller_event_markers(start_iso=start_iso, end_iso=end_iso, miner_ids=selected_miner_ids if selected_miner_ids else None)
        return {
            "range": selected_range,
            "range_seconds": range_seconds,
            "start": start_iso,
            "end": end_iso,
            "is_live": requested_end is None,
            "raw_count": len(raw_rows),
            "point_count": len(points),
            "marker_count": len(markers),
            "max_points": max_points,
            "selected_miner_ids": selected_miner_ids,
            "miners": available_miners,
            "points": points,
            "markers": markers,
        }

    def _available_miners(self, *, start_iso: str, end_iso: str) -> list[dict[str, Any]]:
        self._ensure_schema()
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT miner_id, miner_key, name, driver, MAX(ts) AS last_sample_at, COUNT(*) AS sample_count
                FROM history_miner_samples
                WHERE ts >= ? AND ts <= ?
                GROUP BY miner_id, miner_key, name, driver
                ORDER BY LOWER(COALESCE(name, miner_key, miner_id)) ASC
                """,
                (start_iso, end_iso),
            ).fetchall()
        return [
            {
                "id": row["miner_id"],
                "key": row["miner_key"],
                "name": row["name"] or row["miner_key"] or row["miner_id"],
                "driver": row["driver"],
                "last_sample_at": row["last_sample_at"],
                "sample_count": int(row["sample_count"] or 0),
            }
            for row in rows
        ]

    def _miner_aggregates_by_ts(self, *, start_iso: str, end_iso: str, miner_ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
        self._ensure_schema()
        selected = _parse_id_csv(miner_ids)
        where = "ts >= ? AND ts <= ?"
        params: list[Any] = [start_iso, end_iso]
        if selected:
            where += f" AND miner_id IN ({','.join('?' for _ in selected)})"
            params.extend(selected)
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                f"""
                SELECT ts, miner_id, power_w, hashrate_ghs, temp_c, temp_asic_min_c, temp_asic_max_c
                FROM history_miner_samples
                WHERE {where}
                ORDER BY ts ASC
                """,
                params,
            ).fetchall()

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row_obj in rows:
            row = dict(row_obj)
            ts = str(row.get("ts") or "")
            if not ts:
                continue
            grouped.setdefault(ts, []).append(row)

        aggregates: dict[str, dict[str, Any]] = {}
        for ts, items in grouped.items():
            aggregates[ts] = {
                "miner_power_w_total": sum(float(item.get("power_w") or 0.0) for item in items),
                "miner_hashrate_ghs_total": sum(float(item.get("hashrate_ghs") or 0.0) for item in items),
                "miner_temp_c": _max([_float_or_none(item.get("temp_c")) for item in items]),
                "miner_temp_asic_min_c": _min([_float_or_none(item.get("temp_asic_min_c")) for item in items]),
                "miner_temp_asic_max_c": _max([_float_or_none(item.get("temp_asic_max_c")) for item in items]),
                "selected_miner_count": len({str(item.get("miner_id")) for item in items if item.get("miner_id")}),
            }
        return aggregates

    def _merge_miner_aggregates(
        self,
        rows: list[dict[str, Any]],
        aggregates: dict[str, dict[str, Any]],
        *,
        override_totals: bool,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            aggregate = aggregates.get(str(item.get("ts") or ""))
            if aggregate:
                item["miner_temp_c"] = aggregate.get("miner_temp_c")
                item["miner_temp_asic_min_c"] = aggregate.get("miner_temp_asic_min_c")
                item["miner_temp_asic_max_c"] = aggregate.get("miner_temp_asic_max_c")
                if override_totals:
                    item["miner_power_w_total"] = aggregate.get("miner_power_w_total")
                    item["miner_hashrate_ghs_total"] = aggregate.get("miner_hashrate_ghs_total")
                    item["monitor_enabled_miner_count"] = aggregate.get("selected_miner_count")
                    item["reachable_miner_count"] = aggregate.get("selected_miner_count")
            else:
                item.setdefault("miner_temp_c", None)
                item.setdefault("miner_temp_asic_min_c", None)
                item.setdefault("miner_temp_asic_max_c", None)
                if override_totals:
                    item["miner_power_w_total"] = 0.0
                    item["miner_hashrate_ghs_total"] = 0.0
            merged.append(item)
        return merged

    def _controller_event_markers(self, *, start_iso: str, end_iso: str, miner_ids: list[str] | None = None, max_markers: int = 500) -> list[dict[str, Any]]:
        """Return applied controller events for the selected time range as chart/timeline markers."""
        self._ensure_schema()
        selected = _parse_id_csv(miner_ids)
        where = "event_type = 'applied' AND ts >= ? AND ts <= ?"
        params: list[Any] = [start_iso, end_iso]
        if selected:
            placeholders = ','.join('?' for _ in selected)
            where += f" AND (miner_id IN ({placeholders}) OR miner_key IN ({placeholders}))"
            params.extend(selected)
            params.extend(selected)

        limit = max(1, min(1000, int(max_markers or 500)))
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                f"""
                SELECT *
                FROM controller_events
                WHERE {where}
                ORDER BY ts ASC, id ASC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        markers: list[dict[str, Any]] = []
        for row_obj in rows:
            row = dict(row_obj)
            item = self._controller_event_row_to_item(row)
            old_profile = str(item.get("old_profile") or "").strip()
            new_profile = str(item.get("new_profile") or "").strip()
            miner_name = str(item.get("miner_name") or item.get("miner_key") or item.get("miner_id") or "Miner")
            direction = _profile_change_direction(old_profile, new_profile)
            reason_text = str(item.get("reason_text") or item.get("reason_code") or "Reglerentscheidung")
            label = f"{miner_name}: {old_profile or '?'} -> {new_profile or '?'}"
            markers.append({
                **item,
                "ts": item.get("at"),
                "type": "controller_event",
                "direction": direction,
                "label": label,
                "tooltip": f"{label} · {reason_text}",
            })
        return markers


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
                "miner_temp_c": _max([_float_or_none(row.get("miner_temp_c")) for row in bucket_rows]),
                "miner_temp_asic_min_c": _min([_float_or_none(row.get("miner_temp_asic_min_c")) for row in bucket_rows]),
                "miner_temp_asic_max_c": _max([_float_or_none(row.get("miner_temp_asic_max_c")) for row in bucket_rows]),
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
            "miner_temp_c": _float_or_none(row.get("miner_temp_c")),
            "miner_temp_asic_min_c": _float_or_none(row.get("miner_temp_asic_min_c")),
            "miner_temp_asic_max_c": _float_or_none(row.get("miner_temp_asic_max_c")),
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
