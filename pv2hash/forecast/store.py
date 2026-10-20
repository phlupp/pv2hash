"""Persist forecasts independently from live DataLogger; never rewrite the past."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import utc_iso
from .energy import energy_summary
from . import shadow

DB_FILE = Path("data/forecast.sqlite")
TOTAL = "__total__"


class ForecastStore:
    def __init__(self, path: Path = DB_FILE):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.path, timeout=15.0)

    def ensure_schema(self):
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS forecast_points (
                valid_at TEXT NOT NULL,
                device_id TEXT NOT NULL,
                power_w REAL NOT NULL,
                issued_at TEXT NOT NULL,
                provider TEXT NOT NULL,
                PRIMARY KEY (valid_at,device_id)
            )""")
            con.execute("CREATE INDEX IF NOT EXISTS forecast_time_idx ON forecast_points (valid_at)")
            # Append-only record of every completed provider round. Existing
            # forecast_points remain the current/fast projection for the GUI.
            con.execute("""CREATE TABLE IF NOT EXISTS forecast_revisions (
                revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                issued_at TEXT NOT NULL,
                provider TEXT NOT NULL,
                timezone TEXT NOT NULL,
                resolution_minutes INTEGER,
                point_count INTEGER NOT NULL
            )""")
            con.execute("""CREATE INDEX IF NOT EXISTS forecast_revisions_by_issue
                ON forecast_revisions(issued_at,revision_id)""")
            con.execute("""CREATE TABLE IF NOT EXISTS forecast_revision_points (
                revision_id INTEGER NOT NULL,
                valid_at TEXT NOT NULL,
                device_id TEXT NOT NULL,
                power_w REAL NOT NULL,
                PRIMARY KEY(revision_id, valid_at, device_id),
                FOREIGN KEY(revision_id) REFERENCES forecast_revisions(revision_id)
            )""")
            con.execute("""CREATE INDEX IF NOT EXISTS forecast_revisions_by_time
                ON forecast_revision_points(device_id, valid_at, revision_id)""")
            # Permanent error samples survive the short-lived DataLogger history.
            con.execute("""CREATE TABLE IF NOT EXISTS forecast_quality_samples (
                quality_id INTEGER PRIMARY KEY AUTOINCREMENT,
                valid_at TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                issued_at TEXT NOT NULL,
                forecast_w REAL NOT NULL,
                actual_w REAL NOT NULL,
                error_w REAL NOT NULL,
                evaluated_at TEXT NOT NULL,
                UNIQUE(valid_at, horizon_minutes)
            )""")
            con.execute("""CREATE INDEX IF NOT EXISTS forecast_quality_by_time
                ON forecast_quality_samples(valid_at)""")
            con.execute("""CREATE TABLE IF NOT EXISTS forecast_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            )""")
            shadow.ensure_schema(con)

    def save_future(
        self, *, now: datetime, provider: str, timezone: str,
        surfaces: dict[str, dict[str, float]],
        resolution_minutes: int | None = None,
        shadow_config_hash: str | None = None,
    ) -> dict:
        """One atomic transaction, never write any point <= now.

        All generators must provide the same valid timestamp. Writes for total
        and individual WRs occur together. Compare in UTC so DST cannot break
        the historical freeze. No explicit delete of older future points:
        if a provider returns fewer points, untouched prior forecasts remain.
        """
        if resolution_minutes is not None and resolution_minutes not in (15, 60):
            raise ValueError("Prognoseauflösung muss 15 oder 60 Minuten betragen.")
        self.ensure_schema()
        ZoneInfo(timezone)  # reject malformed provider zones
        moment = utc_iso(now)
        if not surfaces:
            raise ValueError("Keine PV-Generatorprognosen vorhanden.")
        coverage = set.intersection(*(set(values) for values in surfaces.values()))
        future = sorted(ts for ts in coverage if ts > moment)
        if resolution_minutes is not None and any(
            int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            % (resolution_minutes * 60) != 0 for ts in future
        ):
            raise ValueError("Prognose-Zeitpunkte passen nicht zur gewählten Auflösung.")
        if not future:
            raise ValueError("Keine zukünftigen gemeinsamen Prognosezeitpunkte.")
        issued = utc_iso(now)
        by_inverter: dict[str, dict[str, float]] = {}
        for key, points in surfaces.items():
            inverter, _ = key.split("/", 1)
            target = by_inverter.setdefault(inverter, {})
            for ts in future:
                target[ts] = round(target.get(ts, 0) + points[ts], 2)
        values = [
            (ts, inverter, power, issued, provider)
            for inverter, points in by_inverter.items()
            for ts, power in points.items()
        ]
        values += [
            (ts, TOTAL, round(sum(x[ts] for x in by_inverter.values()), 2), issued, provider)
            for ts in future
        ]
        # A second time check immediately before DB write prevents a forecast
        # point slipping into the past during a long API roundtrip.
        with self._connect() as con:
            cutoff = max(moment, utc_iso(datetime.now(UTC)) if abs((datetime.now(UTC)-now).total_seconds()) < 3600 else moment)
            new_values = [row for row in values if row[0] > cutoff]
            con.executemany("""INSERT INTO forecast_points
               (valid_at,device_id,power_w,issued_at,provider) VALUES (?,?,?,?,?)
               ON CONFLICT(valid_at,device_id) DO UPDATE SET
               power_w=excluded.power_w,
               issued_at=excluded.issued_at,
               provider=excluded.provider
               WHERE forecast_points.valid_at > ?""",
               [(*row,cutoff) for row in new_values])
            if resolution_minutes == 60:
                # If a user switches 15 -> 60 minutes, old quarter-hour
                # *future* points must not remain mixed into the hourly chart.
                # Past and present are immutable, even during a resolution
                # switch. Keep valid on-grid fallback timestamps untouched.
                obsolete = [
                    (ts,) for (ts,) in con.execute(
                        "SELECT DISTINCT valid_at FROM forecast_points WHERE valid_at > ?",
                        (cutoff,),
                    )
                    if datetime.fromisoformat(ts.replace("Z", "+00:00")).minute != 0
                ]
                if obsolete:
                    con.executemany("DELETE FROM forecast_points WHERE valid_at=?", obsolete)
            # Only successfully persisted, still-future points are archived.
            # The revision and current projection commit in the same transaction.
            # Never create archival rows for the discarded old/past points.
            if new_values:
                cursor = con.execute("""INSERT INTO forecast_revisions
                    (issued_at, provider, timezone, resolution_minutes, point_count)
                    VALUES (?, ?, ?, ?, ?)""",
                    (issued, provider, timezone, resolution_minutes, len(new_values)))
                con.executemany("""INSERT INTO forecast_revision_points
                    (revision_id, valid_at, device_id, power_w) VALUES (?, ?, ?, ?)""",
                    [(cursor.lastrowid, ts, name, power)
                     for ts, name, power, _, _ in new_values])
                if shadow_config_hash:
                    shadow.publish(
                        con, revision_id=cursor.lastrowid, issued=issued,
                        timezone=timezone, config_hash=shadow_config_hash,
                        values=new_values, cutoff=cutoff,
                        resolution_minutes=resolution_minutes,
                    )
            con.execute("""INSERT INTO forecast_meta(key,value) VALUES('timezone',?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (timezone,))
            con.execute("""INSERT INTO forecast_meta(key,value) VALUES('last_success',?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (issued,))
            con.execute("DELETE FROM forecast_meta WHERE key IN ('last_failure','last_error')")
        return {"future_points": len({r[0] for r in new_values if r[1] == TOTAL}),
                "inverters": len(by_inverter), "timezone": timezone}

    def prune_revisions(self, *, now: datetime | None = None,
                        retention_days: int = 365,
                        batch_revisions: int = 12) -> dict:
        """Delete issued revisions older than retention, in bounded transactions.

        Only the append-only revision archive is pruned. The live projection,
        permanent error samples, and raw DataLogger remain untouched.
        Each small transaction deletes children and parent together. Retried
        runs are idempotent. SQLite can reuse freelist pages without VACUUM.
        """
        if not 1 <= retention_days <= 3650 or not 1 <= batch_revisions <= 100:
            raise ValueError("Invalid forecast archive retention settings")
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError("Retention now must be timezone-aware")
        cutoff = utc_iso(instant.astimezone(UTC) - timedelta(days=retention_days))
        if not self.path.exists():
            return {"revisions_deleted": 0, "points_deleted": 0, "retention_days": retention_days}
        self.ensure_schema()
        count = 0
        points = 0
        while True:
            with self._connect() as con:
                ids = [row[0] for row in con.execute(
                    "SELECT revision_id FROM forecast_revisions "
                    "WHERE issued_at < ? ORDER BY issued_at,revision_id LIMIT ?",
                    (cutoff, batch_revisions)).fetchall()]
                if not ids:
                    break
                placeholders = ','.join('?' for _ in ids)
                points += con.execute(
                    f"SELECT COUNT(*) FROM forecast_revision_points "
                    f"WHERE revision_id IN ({placeholders})", ids).fetchone()[0]
                # Prune paired shadow revisions in the same bounded transaction.
                shadow.prune_archived_pairs(con, revision_ids=ids, cutoff=cutoff)
                con.execute(f"DELETE FROM forecast_revision_points WHERE revision_id IN ({placeholders})", ids)
                con.execute(f"DELETE FROM forecast_revisions WHERE revision_id IN ({placeholders})", ids)
                count += len(ids)
        return {"revisions_deleted": count, "points_deleted": points,
                "retention_days": retention_days}

    def record_failure(self, message: str) -> None:
        """Persist a safe, user-visible provider failure until the next success."""
        self.ensure_schema()
        reason = (message or "Prognoseaktualisierung fehlgeschlagen.").strip()[:240]
        with self._connect() as con:
            con.executemany(
                """INSERT INTO forecast_meta(key,value) VALUES(?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                [('last_failure', utc_iso(datetime.now(UTC))), ('last_error', reason)],
            )

    def metadata(self) -> dict:
        self.ensure_schema()
        with self._connect() as con:
            items = dict(con.execute("SELECT key,value FROM forecast_meta"))
        return items

    def day(self, selected_day: str | None = None, *,
            resolution_minutes: int | None = None) -> dict:
        self.ensure_schema()
        zone_name = self.metadata().get("timezone", "Europe/Berlin")
        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            zone_name, zone = "UTC", ZoneInfo("UTC")
        today = datetime.now(zone).date()
        from datetime import date
        selected = date.fromisoformat(selected_day) if selected_day else today
        if selected > today + timedelta(days=1):
            raise ValueError("Maximal ein Tag in der Zukunft auswählbar.")
        start = datetime.combine(selected, datetime.min.time(), tzinfo=zone).astimezone(UTC)
        end = datetime.combine(selected + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(UTC)
        lo, hi = utc_iso(start), utc_iso(end)
        with self._connect() as con:
            rows = con.execute("""SELECT valid_at,power_w,issued_at
              FROM forecast_points
              WHERE valid_at>=? AND valid_at<=? AND device_id=?
              ORDER BY valid_at""",(lo,hi,TOTAL)).fetchall()
            shadow_points, shadow_status = shadow.read_day(con, lo, hi)
        points = [
            (datetime.fromisoformat(ts.replace("Z", "+00:00")), float(power))
            for ts, power, _ in rows
        ]
        # Past days may have a different point cadence than today's setting;
        # infer it from actually persisted times to preserve historic energy.
        # During the current/future day require the explicitly selected cadence
        # so a missing 15-minute point never becomes an invented half-hour.
        intervals = [
            (b[0] - a[0]).total_seconds()
            for a, b in zip(points, points[1:]) if b[0] > a[0]
        ]
        inferred_step = min(intervals, default=3600)
        expected_step = (
            resolution_minutes * 60 if selected >= today and
            resolution_minutes in (15, 60) else inferred_step
        )
        energy = energy_summary(
            points, start=start, end=end,
            max_gap_seconds=min(5400, expected_step * 1.5),
        )
        return {
            "day": selected.isoformat(), "today": today.isoformat(),
            "timezone": zone_name, "start":lo,"end":hi,
            "forecast": [{"ts":ts,"power_w":power,"issued_at":issued}
                         for ts,power,issued in rows if ts < hi],
            "shadow": shadow_points, "shadow_status": shadow_status,
            "energy": {"forecast": energy},
        }

    def individual_day(self, selected_day: str) -> list[dict]:
        """Optional diagnosis/internal audit; not part of chart response."""
        day = self.day(selected_day)
        with self._connect() as con:
            rows = con.execute("""SELECT valid_at,device_id,power_w,issued_at
                FROM forecast_points WHERE valid_at>=? AND valid_at<?
                AND device_id<>? ORDER BY valid_at,device_id""",
                (day["start"],day["end"],TOTAL)).fetchall()
        return [{"ts":r[0],"inverter_id":r[1],"power_w":r[2],"issued_at":r[3]} for r in rows]
