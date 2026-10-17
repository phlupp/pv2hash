"""Persist forecasts independently from live DataLogger; never rewrite the past."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import utc_iso

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
            con.execute("""CREATE TABLE IF NOT EXISTS forecast_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            )""")

    def save_future(
        self, *, now: datetime, provider: str, timezone: str,
        surfaces: dict[str, dict[str, float]],
    ) -> dict:
        """One atomic transaction, never write any point <= now.

        All generators must provide the same valid timestamp. Writes for total
        and individual WRs occur together. Compare in UTC so DST cannot break
        the historical freeze. No explicit delete of older future points:
        if a provider returns fewer points, untouched prior forecasts remain.
        """
        self.ensure_schema()
        ZoneInfo(timezone)  # reject malformed provider zones
        moment = utc_iso(now)
        if not surfaces:
            raise ValueError("Keine PV-Generatorprognosen vorhanden.")
        coverage = set.intersection(*(set(values) for values in surfaces.values()))
        future = sorted(ts for ts in coverage if ts > moment)
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
            con.execute("""INSERT INTO forecast_meta(key,value) VALUES('timezone',?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (timezone,))
            con.execute("""INSERT INTO forecast_meta(key,value) VALUES('last_success',?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (issued,))
        return {"future_points": len({r[0] for r in new_values if r[1] == TOTAL}),
                "inverters": len(by_inverter), "timezone": timezone}

    def metadata(self) -> dict:
        self.ensure_schema()
        with self._connect() as con:
            items = dict(con.execute("SELECT key,value FROM forecast_meta"))
        return items

    def day(self, selected_day: str | None = None) -> dict:
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
              WHERE valid_at>=? AND valid_at<? AND device_id=?
              ORDER BY valid_at""",(lo,hi,TOTAL)).fetchall()
        return {
            "day": selected.isoformat(), "today": today.isoformat(),
            "timezone": zone_name, "start":lo,"end":hi,
            "forecast": [{"ts":ts,"power_w":power,"issued_at":issued}
                         for ts,power,issued in rows],
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
