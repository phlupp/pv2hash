"""Read-only observed-vs-issued forecast evaluation, independent of controller.

15/30/60/180-minute lead-time errors use the *as-issued* revision that was
actually available before the evaluated instant. Never evaluate the latest
forecast retroactively and never invent actual values for missing samples.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
import math
import sqlite3

from . import utc_iso
from .store import ForecastStore, TOTAL

HORIZONS_MINUTES = (15, 30, 60, 180)
TOLERANCE_MINUTES = {15: 8, 30: 8, 60: 10, 180: 20}
ACTUAL_TOLERANCE_SECONDS = 60
DEFAULT_HISTORY_DB = Path("data/history.sqlite")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp without timezone")
    return parsed.astimezone(UTC)


def evaluate_recent(
    store: ForecastStore, *, history_db: Path = DEFAULT_HISTORY_DB,
    now: datetime | None = None, lookback_hours: int = 48,
) -> dict:
    """Persist settled error samples; safe to run repeatedly (insert-only).

    Only samples with live PV quality and a real measurement within ±60s
    of the forecast target are eligible. Existing quality rows never change.
    Retrospective catch-up is bounded by DataLogger retention (7 days).
    """
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None or not 1 <= lookback_hours <= 168:
        raise ValueError("Forecast evaluation needs aware now and 1–168h window")
    instant = instant.astimezone(UTC)
    if not history_db.exists() or not store.path.exists():
        return {"evaluated": 0, "status": "no_history"}
    store.ensure_schema()
    start = instant - timedelta(hours=lookback_hours)
    # Avoid evaluating the end of the active interval while samples still arrive.
    upper = instant - timedelta(seconds=ACTUAL_TOLERANCE_SECONDS + 30)
    if upper <= start:
        return {"evaluated": 0, "status": "not_due"}
    with sqlite3.connect(history_db, timeout=10) as source:
        # The DataLogger stores offset ISO strings instead of Z timestamps.
        rows = source.execute("""SELECT ts,pv_power_w,pv_quality FROM history_samples
            WHERE ts>=? AND ts<=? ORDER BY ts""",
            ((start-timedelta(seconds=ACTUAL_TOLERANCE_SECONDS)).isoformat(),
             (upper+timedelta(seconds=ACTUAL_TOLERANCE_SECONDS)).isoformat())).fetchall()
    actual = []
    for ts, power, quality in rows:
        try:
            value = float(power)
            when = _parse_time(ts)
            good = (quality == "live" and math.isfinite(value) and value >= -1_000_000)
            actual.append((when, value if good else None))
        except (TypeError, ValueError, OverflowError):
            continue
    actual_times = [row[0] for row in actual]
    if not actual_times:
        return {"evaluated": 0, "status": "no_actual_samples"}
    with store._connect() as con:
        forecasts = con.execute("""SELECT r.valid_at, v.issued_at, r.power_w
            FROM forecast_revision_points AS r
            JOIN forecast_revisions AS v ON v.revision_id=r.revision_id
            WHERE r.device_id=? AND r.valid_at>=? AND r.valid_at<=?
            ORDER BY r.valid_at, v.issued_at DESC, v.revision_id DESC""",
            (TOTAL, utc_iso(start), utc_iso(upper))).fetchall()
        # The last-issued forecast before the horizon cutoff is the legitimate
        # as-of candidate; maximum allowed extra age prevents labeling a
        # several-hours-old forecast as a 15-minute-ahead prediction.
        grouped = defaultdict(list)
        for target, issued, predicted in forecasts:
            grouped[target].append((issued, predicted))
        evaluations = []
        evaluated_at = utc_iso(instant)
        for target, revisions in grouped.items():
            target_time = _parse_time(target)
            pos = bisect_left(actual_times, target_time)
            nearest = sorted((actual[j] for j in (pos-1,pos) if 0 <= j < len(actual)),
                             key=lambda row: abs((row[0]-target_time).total_seconds()))
            if not nearest or abs((nearest[0][0]-target_time).total_seconds()) > ACTUAL_TOLERANCE_SECONDS:
                continue
            observed = nearest[0][1]
            if observed is None:  # nearest sample was bad/degraded, no fallback
                continue
            for horizon in HORIZONS_MINUTES:
                cutoff = target_time - timedelta(minutes=horizon)
                acceptable_since = cutoff - timedelta(minutes=TOLERANCE_MINUTES[horizon])
                candidate = next(((_parse_time(issued), power) for issued, power in revisions
                                  if acceptable_since <= _parse_time(issued) <= cutoff), None)
                if candidate is None:
                    continue
                issued, predicted = candidate
                predicted = float(predicted)
                if not math.isfinite(predicted) or predicted < -1_000_000:
                    continue
                evaluations.append((target, horizon, utc_iso(issued), predicted,
                                    observed, round(predicted-observed, 4), evaluated_at))
        # Persist permanently for aggregate analysis after raw history expires.
        before = con.total_changes
        con.executemany("""INSERT OR IGNORE INTO forecast_quality_samples
            (valid_at,horizon_minutes,issued_at,forecast_w,actual_w,error_w,evaluated_at)
            VALUES (?,?,?,?,?,?,?)""", evaluations)
        new_samples = con.total_changes - before
    return {"evaluated": new_samples, "status": "ok", "candidate_samples": len(evaluations)}


def quality_report(store: ForecastStore, *, days: int = 7,
                   now: datetime | None = None) -> dict:
    """Errors by issue horizon, no fabricated accuracy if sample count is zero."""
    if not 1 <= days <= 90:
        raise ValueError("Zeitraum muss 1–90 Tage betragen.")
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("Current time must include a timezone")
    since = utc_iso(instant.astimezone(UTC) - timedelta(days=days))
    store.ensure_schema()
    with store._connect() as con:
        raw = con.execute("""SELECT horizon_minutes, COUNT(*),
            AVG(ABS(error_w)), AVG(error_w), AVG(error_w*error_w),
            MIN(valid_at), MAX(valid_at)
            FROM forecast_quality_samples WHERE valid_at>=?
            GROUP BY horizon_minutes ORDER BY horizon_minutes""", (since,)).fetchall()
        revisions = con.execute("""SELECT COUNT(*), MAX(issued_at)
            FROM forecast_revisions WHERE issued_at>=?""",(since,)).fetchone()
    by_horizon = {h: (n, mae, bias, mse, first, last) for h,n,mae,bias,mse,first,last in raw}
    metrics=[]
    for horizon in HORIZONS_MINUTES:
        record=by_horizon.get(horizon)
        metrics.append({"horizon_minutes":horizon,"samples":record[0] if record else 0,
          "mae_w":round(record[1],2) if record else None,
          "bias_w":round(record[2],2) if record else None,
          "rmse_w":round(math.sqrt(record[3]),2) if record else None,
          "first_valid_at":record[4] if record else None,
          "last_valid_at":record[5] if record else None})
    return {"days":days,"revision_count":revisions[0],"last_issued_at":revisions[1],
            "metrics":metrics,"note":"Nur vorhandene LIVE-Ist-Werte und zeitlich passende, vorher ausgegebene Prognoserevisionen; fehlende Stichproben werden nicht geschätzt."}
