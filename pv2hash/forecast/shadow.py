"""Explainable, site-level PV shadow forecasts. No controller, miner or socket imports.

Every issued shadow value is paired with an immutable baseline revision.
Training consumes only earlier, quality-checked, config-matched comparisons.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import math
from statistics import median
from zoneinfo import ZoneInfo

from . import utc_iso

MODEL_VERSION = "site-median-v1"
MIN_POWER_W = 500.0
MIN_SAMPLES = 24
MIN_DAYS = 3
MIN_PER_DAY = 4
MAX_FACTOR_SHIFT = 0.15
RETENTION_DAYS = 365
DAYLIGHT_W = 500.0


def signature(generators: list, location: dict, provider: str, resolution: int) -> str:
    """Fingerprint of forecast physics, never API keys or instance identifiers."""
    payload = {
        "generators": sorted([{
            "inverter_id": g.inverter_id, "generator_id": g.generator_id,
            "kwp": g.kwp, "tilt": g.tilt, "azimuth": g.azimuth,
            "loss_percent": g.loss_percent,
        } for g in generators], key=lambda g: (g["inverter_id"], g["generator_id"])),
        "lat": float(location["lat"]), "lon": float(location["lon"]),
        "provider": provider, "resolution_minutes": resolution,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ensure_schema(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS forecast_shadow_models (
        model_id INTEGER PRIMARY KEY AUTOINCREMENT,
        calculated_at TEXT NOT NULL,
        model_version TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        factor REAL NOT NULL,
        sample_count INTEGER NOT NULL,
        day_count INTEGER NOT NULL,
        window_start TEXT,
        window_end TEXT,
        diagnostics TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS forecast_shadow_revisions (
        revision_id INTEGER PRIMARY KEY,
        model_id INTEGER NOT NULL,
        config_hash TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        FOREIGN KEY(revision_id) REFERENCES forecast_revisions(revision_id),
        FOREIGN KEY(model_id) REFERENCES forecast_shadow_models(model_id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS forecast_shadow_revision_points (
        revision_id INTEGER NOT NULL,
        valid_at TEXT NOT NULL,
        device_id TEXT NOT NULL,
        power_w REAL NOT NULL,
        PRIMARY KEY(revision_id, valid_at, device_id),
        FOREIGN KEY(revision_id) REFERENCES forecast_shadow_revisions(revision_id)
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS forecast_shadow_revision_times
        ON forecast_shadow_revision_points(device_id,valid_at,revision_id)""")
    con.execute("""CREATE TABLE IF NOT EXISTS forecast_shadow_points (
        valid_at TEXT PRIMARY KEY,
        power_w REAL NOT NULL,
        issued_at TEXT NOT NULL,
        baseline_revision_id INTEGER NOT NULL,
        model_id INTEGER NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS forecast_shadow_quality_samples (
        valid_at TEXT NOT NULL,
        horizon_minutes INTEGER NOT NULL,
        issued_at TEXT NOT NULL,
        baseline_revision_id INTEGER NOT NULL,
        model_id INTEGER NOT NULL,
        config_hash TEXT NOT NULL,
        baseline_w REAL NOT NULL,
        shadow_w REAL NOT NULL,
        actual_w REAL NOT NULL,
        evaluated_at TEXT NOT NULL,
        PRIMARY KEY(valid_at,horizon_minutes)
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS forecast_shadow_quality_by_config
        ON forecast_shadow_quality_samples(config_hash,valid_at)""")


def fit_model(rows: list[tuple], *, issued_at: datetime, timezone: str) -> dict:
    """Robust median of daily medians; one quality row per target timestamp.

    Requires at least 3 suitable local days and 24 daytime target samples.
    Very large outliers and unstable daily biases must not become a correction.
    """
    zone = ZoneInfo(timezone)
    selected: dict[str, tuple[int, float, float]] = {}
    priority = {60: 0, 30: 1, 180: 2, 15: 3}
    earliest = issued_at - timedelta(days=30)
    for target, horizon, predicted, actual in rows:
        try:
            target_dt = datetime.fromisoformat(target.replace("Z", "+00:00")).astimezone(UTC)
            pred, obs = float(predicted), float(actual)
            if not (earliest <= target_dt < issued_at and math.isfinite(pred)
                    and math.isfinite(obs) and pred >= MIN_POWER_W and obs >= 0):
                continue
            horizon = int(horizon)
            if horizon not in priority:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        if target not in selected or priority[horizon] < priority[selected[target][0]]:
            selected[target] = (horizon, pred, obs)
    daily: dict[str, list[float]] = defaultdict(list)
    outliers = 0
    used_targets = []
    for target, (_, predicted, actual) in selected.items():
        ratio = actual / predicted
        if not 0.5 <= ratio <= 1.5:
            outliers += 1
            continue
        day = datetime.fromisoformat(target.replace("Z", "+00:00")).astimezone(zone).date().isoformat()
        daily[day].append(ratio)
        used_targets.append(target)
    eligible = {day: values for day, values in daily.items() if len(values) >= MIN_PER_DAY}
    sample_count = sum(map(len, eligible.values()))
    day_count = len(eligible)
    base = {"factor": 1.0, "status": "learning",
            "sample_count": sample_count, "day_count": day_count,
            "window_start": min(used_targets) if used_targets else None,
            "window_end": max(used_targets) if used_targets else None,
            "outliers_excluded": outliers, "raw_targets": len(selected),
            "reason": "insufficient_days_or_samples"}
    if day_count < MIN_DAYS or sample_count < MIN_SAMPLES:
        return base
    ratios = [median(values) for values in eligible.values()]
    target_factor = median(ratios)
    spread = median(abs(v - target_factor) for v in ratios)
    if spread > 0.12:
        return {**base, "status": "unstable", "reason": "daily_variability",
                "day_median_mad": round(spread, 4)}
    factor = max(1 - MAX_FACTOR_SHIFT, min(1 + MAX_FACTOR_SHIFT, target_factor))
    return {**base, "status": "calibrated", "factor": round(factor, 5),
            "reason": "conservative_daily_median",
            "unclipped_factor": round(target_factor, 5),
            "day_median_mad": round(spread, 4)}


def _prepare_model(con, *, issued: str, timezone: str, config_hash: str) -> dict:
    history_start = utc_iso(datetime.fromisoformat(issued.replace("Z", "+00:00")) - timedelta(days=30))
    rows = con.execute("""SELECT valid_at,horizon_minutes,baseline_w,actual_w
        FROM forecast_shadow_quality_samples WHERE config_hash=?
        AND valid_at>=? AND valid_at<? AND evaluated_at<=?
        ORDER BY valid_at,horizon_minutes""",
        (config_hash, history_start, issued, issued)).fetchall()
    return fit_model(rows,
        issued_at=datetime.fromisoformat(issued.replace("Z", "+00:00")), timezone=timezone)


def publish(con, *, revision_id: int, issued: str, timezone: str,
            config_hash: str, values: list[tuple], cutoff: str,
            resolution_minutes: int | None) -> None:
    """Called within the original baseline forecast transaction."""
    model = _prepare_model(con, issued=issued, timezone=timezone, config_hash=config_hash)
    diagnostics = {k: value for k, value in model.items()
                   if k not in ("factor", "status", "sample_count", "day_count",
                                "window_start", "window_end")}
    model_id = con.execute("""INSERT INTO forecast_shadow_models
        (calculated_at,model_version,config_hash,status,factor,sample_count,
         day_count,window_start,window_end,diagnostics)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (issued, MODEL_VERSION, config_hash, model["status"], model["factor"],
         model["sample_count"], model["day_count"],
         model["window_start"], model["window_end"], json.dumps(diagnostics))).lastrowid
    con.execute("""INSERT INTO forecast_shadow_revisions
        (revision_id,model_id,config_hash,issued_at) VALUES(?,?,?,?)""",
        (revision_id, model_id, config_hash, issued))
    for ts, device, predicted, _, _ in values:
        if device != "__total__":
            continue  # No per-generator actuals -> no per-generator calibration!
        shadow_w = round(max(0.0, predicted * model["factor"]), 2)
        con.execute("""INSERT INTO forecast_shadow_revision_points
            (revision_id,valid_at,device_id,power_w) VALUES(?,?,?,?)""",
            (revision_id, ts, device, shadow_w))
        con.execute("""INSERT INTO forecast_shadow_points
            (valid_at,power_w,issued_at,baseline_revision_id,model_id)
            VALUES(?,?,?,?,?)
            ON CONFLICT(valid_at) DO UPDATE SET
                power_w=excluded.power_w, issued_at=excluded.issued_at,
                baseline_revision_id=excluded.baseline_revision_id,
                model_id=excluded.model_id
            WHERE forecast_shadow_points.valid_at>?""",
            (ts, shadow_w, issued, revision_id, model_id, cutoff))
    if resolution_minutes == 60:
        # Match baseline projection cleanup, never touch settled shadow points.
        con.execute("""DELETE FROM forecast_shadow_points WHERE valid_at>?
            AND CAST(strftime('%M',valid_at) AS INTEGER)<>0""", (cutoff,))


def read_day(con, lo: str, hi: str) -> tuple[list[dict], dict]:
    rows = con.execute("""SELECT p.valid_at,p.power_w,p.issued_at
        FROM forecast_shadow_points p WHERE p.valid_at>=? AND p.valid_at<?
        ORDER BY p.valid_at""", (lo, hi)).fetchall()
    latest = con.execute("""SELECT m.status,m.factor,m.sample_count,m.day_count,
        m.model_version,m.diagnostics
        FROM forecast_shadow_points p
        JOIN forecast_shadow_models m ON m.model_id=p.model_id
        WHERE p.valid_at>=? AND p.valid_at<? ORDER BY p.issued_at DESC,p.valid_at DESC
        LIMIT 1""", (lo, hi)).fetchone()
    status = {"status": "not_available", "factor": 1.0, "sample_count": 0,
              "day_count": 0, "model_version": MODEL_VERSION,
              "configuration_hint": None}
    if latest:
        state, factor, samples, days, version, diagnostics = latest
        hint = None
        if state == "calibrated" and abs(factor - 1.0) >= 0.06:
            hint = ("Wiederholter Leistungsunterschied auf Standortebene; "
                    "PV-Konfiguration oder Modellannahmen prüfen. Keine automatische Änderung.")
        status = {"status": state, "factor": factor, "sample_count": samples,
                  "day_count": days, "model_version": version,
                  "configuration_hint": hint}
    return ([{"ts": ts, "power_w": power, "issued_at": issued} for ts,power,issued in rows],
            status)


def evaluate_pairs(store, *, now: datetime | None = None,
                   lookback_hours: int = 48) -> dict:
    """As-issued baseline and shadow on EXACT SAME target and LIVE measurement."""
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    since = utc_iso(instant - timedelta(hours=lookback_hours))
    with store._connect() as con:
        ensure_schema(con)
        # Original quality rows are the authority for LIVE validity and lead times.
        candidates = con.execute("""SELECT q.valid_at,q.horizon_minutes,q.issued_at,
            v.revision_id, sr.model_id,sr.config_hash,q.forecast_w,
            sp.power_w,q.actual_w
            FROM forecast_quality_samples q
            JOIN forecast_revisions v ON v.issued_at=q.issued_at
            JOIN forecast_revision_points bp ON bp.revision_id=v.revision_id
                AND bp.valid_at=q.valid_at AND bp.device_id='__total__'
            JOIN forecast_shadow_revisions sr ON sr.revision_id=v.revision_id
            JOIN forecast_shadow_revision_points sp ON sp.revision_id=v.revision_id
                AND sp.valid_at=q.valid_at AND sp.device_id='__total__'
            WHERE q.valid_at>=? AND q.valid_at<?
              AND ABS(bp.power_w-q.forecast_w)<0.011
            ORDER BY q.valid_at,q.horizon_minutes,v.revision_id DESC""",
            (since, utc_iso(instant))).fetchall()
        # Legacy baseline quality has second-resolution issued_at but no
        # revision_id. If multiple same-second revisions match, there is no
        # provable unique pair. Exclude it instead of guessing a winner.
        grouped = defaultdict(list)
        for candidate in candidates:
            grouped[(candidate[0], candidate[1])].append(candidate)
        payload = []
        for matches in grouped.values():
            if len(matches) != 1:
                continue
            target, horizon, issued, rev, model_id, config, baseline, shadow, actual = matches[0]
            payload.append((target,horizon,issued,rev,model_id,config,
                            baseline,shadow,actual,utc_iso(instant)))
        before = con.total_changes
        con.executemany("""INSERT OR IGNORE INTO forecast_shadow_quality_samples
            (valid_at,horizon_minutes,issued_at,baseline_revision_id,model_id,
             config_hash,baseline_w,shadow_w,actual_w,evaluated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?)""", payload)
        inserted = con.total_changes - before
    return {"evaluated": inserted, "candidate_samples": len(payload)}


def quality_comparison(store, *, days: int = 7,
                       now: datetime | None = None) -> dict:
    if not 1 <= days <= 90:
        raise ValueError("Zeitraum muss 1–90 Tage betragen.")
    from .quality import HORIZONS_MINUTES
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    since = utc_iso(instant - timedelta(days=days))
    with store._connect() as con:
        ensure_schema(con)
        rows = con.execute("""SELECT horizon_minutes,baseline_w,shadow_w,actual_w
            FROM forecast_shadow_quality_samples WHERE valid_at>=?""", (since,)).fetchall()
    def stats(items, index):
        errors = [row[index]-row[3] for row in items]
        if not errors:
            return {"samples": 0, "mae_w": None, "bias_w": None, "rmse_w": None}
        n = len(errors)
        return {"samples": n, "mae_w": round(sum(abs(x) for x in errors)/n,2),
                "bias_w": round(sum(errors)/n,2),
                "rmse_w": round(math.sqrt(sum(x*x for x in errors)/n),2)}
    result = []
    for horizon in HORIZONS_MINUTES:
        matching = [row for row in rows if row[0] == horizon]
        for scope, selected in (
            ("all", matching),
            ("daylight", [row for row in matching
                          if row[1] >= DAYLIGHT_W or row[3] >= DAYLIGHT_W])):
            base, shadow = stats(selected,1), stats(selected,2)
            result.append({"horizon_minutes": horizon, "scope": scope,
                           "baseline": base, "shadow": shadow,
                           "mae_improvement_w": (round(base["mae_w"]-shadow["mae_w"],2)
                              if base["mae_w"] is not None else None)})
    return {"days": days, "daylight_threshold_w": DAYLIGHT_W,
            "metrics": result, "paired_only": True,
            "note": "Beide Prognosen vor der Zielzeit archiviert, identischer LIVE-Istwert; positive MAE-Verbesserung begünstigt Shadow."}


def prune_archived_pairs(con, *, revision_ids: list[int], cutoff: str) -> None:
    """Remove paired archives with their baseline in the same transaction."""
    if revision_ids:
        marks = ",".join("?" for _ in revision_ids)
        con.execute(f"DELETE FROM forecast_shadow_revision_points WHERE revision_id IN ({marks})",
                    revision_ids)
        con.execute(f"DELETE FROM forecast_shadow_revisions WHERE revision_id IN ({marks})",
                    revision_ids)
    con.execute("""DELETE FROM forecast_shadow_models WHERE calculated_at<?
        AND model_id NOT IN (SELECT model_id FROM forecast_shadow_revisions)
        AND model_id NOT IN (SELECT model_id FROM forecast_shadow_points)""",(cutoff,))
