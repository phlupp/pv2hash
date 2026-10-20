"""PV2Hash shadow-mode regression tests. All databases are temporary."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pv2hash.forecast import utc_iso
from pv2hash.forecast.store import ForecastStore
from pv2hash.forecast.quality import evaluate_recent
from pv2hash.forecast.shadow import (
    fit_model, evaluate_pairs, quality_comparison, signature,
)


def time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class ShadowModelTests(unittest.TestCase):
    def test_three_days_stable_bias_is_conservative(self):
        rows = []
        for day in range(4):
            for hour in range(8, 16):
                target = time("2026-10-10T00:00:00Z") + timedelta(days=day, hours=hour)
                rows.append((utc_iso(target),60,2000,1600))
                rows.append((utc_iso(target),30,2000,1600))  # no duplicate targets
        model = fit_model(rows,issued_at=time("2026-10-15T08:00:00Z"),timezone="Europe/Berlin")
        self.assertEqual((model["status"],model["factor"],model["sample_count"],model["day_count"]),
                         ("calibrated",0.85,32,4))

    def test_anomaly_is_not_learned_and_disagreeing_days_trigger_fallback(self):
        rows = []
        for day in range(4):
            for hour in range(8, 16):
                target = time("2026-10-10T00:00:00Z") + timedelta(days=day,hours=hour)
                actual = 200 if (day == 3 and hour == 15) else 2000
                rows.append((utc_iso(target),60,2000,actual))
        m=fit_model(rows,issued_at=time("2026-10-15T08:00:00Z"),timezone="UTC")
        self.assertEqual(m["status"],"calibrated")
        self.assertEqual(m["factor"],1.0)
        self.assertEqual(m["outliers_excluded"],1)
        differing = [(ts,h,p,1200 if i < 16 else 2800) for i,(ts,h,p,_) in enumerate(rows)]
        self.assertEqual(fit_model(differing,issued_at=time("2026-10-15T08:00:00Z"),
                                   timezone="UTC")["status"],"unstable")

    def test_unknown_and_future_measurements_never_train(self):
        rows = [(utc_iso(time("2026-10-13T12:00:00Z")+timedelta(minutes=i*15)),
                 60,1000,100) for i in range(24)]
        model=fit_model(rows,issued_at=time("2026-10-13T11:00:00Z"),timezone="UTC")
        self.assertEqual((model["status"],model["factor"]),("learning",1.0))

    def test_fingerprint_changes_with_plant_parameter_not_device_order(self):
        from pv2hash.forecast import Generator
        x=Generator("a","A","south","South",5,30,0,14)
        y=Generator("b","B","west","West",3,20,90,10)
        a=signature([x,y],{"lat":51,"lon":9},"open_meteo",15)
        self.assertEqual(a,signature([y,x],{"lat":51,"lon":9},"open_meteo",15))
        self.assertNotEqual(a,signature([x,y],{"lat":51,"lon":9},"open_meteo",60))


class ShadowStoreTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store=ForecastStore(Path(tmp.name)/"forecast.sqlite")
        self.history=Path(tmp.name)/"history.sqlite"
        self.signature="a"*64

    def issue(self, issued, target, power=1000, hash=None):
        return self.store.save_future(
            now=time(issued), provider="open_meteo", timezone="UTC",
            resolution_minutes=15, shadow_config_hash=self.signature if hash is None else hash,
            surfaces={"wr-a/roof1":{target:power*.7},
                      "wr-a/roof2":{target:power*.2},
                      "wr-b/roof3":{target:power*.1}})

    def test_atomic_pairing_freezes_past_and_only_site_shadow(self):
        t="2026-10-10T14:00:00Z"
        self.issue("2026-10-10T13:00:00Z",t)
        self.issue("2026-10-10T13:30:00Z",t,1200)
        with self.store._connect() as con:
            base=con.execute("SELECT count(*) FROM forecast_revisions").fetchone()[0]
            rev=con.execute("SELECT count(*) FROM forecast_shadow_revisions").fetchone()[0]
            points=con.execute("SELECT device_id FROM forecast_shadow_revision_points").fetchall()
            latest=con.execute("SELECT power_w FROM forecast_shadow_points WHERE valid_at=?",
                               (t,)).fetchone()[0]
        self.assertEqual((base,rev,points,latest),(2,2,[("__total__",),("__total__",)],1200))
        with self.store._connect() as con:
            frozen=con.execute("SELECT power_w FROM forecast_shadow_points WHERE valid_at=?",(t,)).fetchone()
        self.issue("2026-10-10T14:30:00Z","2026-10-10T15:00:00Z",4000)
        with self.store._connect() as con:
            self.assertEqual(con.execute("SELECT power_w FROM forecast_shadow_points WHERE valid_at=?",
                                         (t,)).fetchone(),frozen)
        day=self.store.day("2026-10-10")
        self.assertTrue(day["shadow"])
        self.assertEqual(day["shadow_status"]["status"],"learning")

    def test_paired_evaluation_reuses_live_baseline_and_survives_source_deletion(self):
        target="2026-10-10T14:00:00Z"
        self.issue("2026-10-10T13:00:00Z",target,1800)
        self.issue("2026-10-10T13:30:00Z",target,1400)
        with sqlite3.connect(self.history) as con:
            con.execute("CREATE TABLE history_samples(ts TEXT PRIMARY KEY,pv_power_w REAL,pv_quality TEXT)")
            con.execute("INSERT INTO history_samples VALUES (?,?,?)",
                        ("2026-10-10T14:00:05+00:00",1100,"live"))
        evaluated=time("2026-10-10T14:03:00Z")
        self.assertEqual(evaluate_recent(self.store,history_db=self.history,
            now=evaluated,lookback_hours=3)["evaluated"],2)
        self.assertEqual(evaluate_pairs(self.store,now=evaluated,lookback_hours=3)["evaluated"],2)
        self.assertEqual(evaluate_pairs(self.store,now=evaluated,lookback_hours=3)["evaluated"],0)
        metrics=quality_comparison(self.store,days=1,now=evaluated)["metrics"]
        by_horizon={x["horizon_minutes"]:x for x in metrics if x["scope"]=="daylight"}
        self.assertEqual(by_horizon[60]["baseline"]["mae_w"],700)
        self.assertEqual(by_horizon[60]["shadow"]["mae_w"],700)  # baseline fallback
        self.assertEqual(by_horizon[30]["shadow"]["mae_w"],300)
        self.history.unlink()
        self.assertEqual(quality_comparison(self.store,days=1,now=evaluated)["metrics"][0]["baseline"]["samples"],0)

    def test_learner_uses_only_previously_evaluated_matching_configuration(self):
        self.store.ensure_schema()
        with self.store._connect() as con:
            for day in range(4):
                for hour in range(8,16):
                    ts=utc_iso(time("2026-10-10T00:00:00Z")+timedelta(days=day,hours=hour))
                    con.execute("""INSERT INTO forecast_shadow_quality_samples
                        (valid_at,horizon_minutes,issued_at,baseline_revision_id,model_id,
                         config_hash,baseline_w,shadow_w,actual_w,evaluated_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (ts,60,ts,1,1,self.signature,2000,2000,1600,ts))
        self.issue("2026-10-14T12:00:00Z","2026-10-14T13:00:00Z",2000)
        with self.store._connect() as con:
            state=con.execute("SELECT status,factor,sample_count,day_count FROM forecast_shadow_models").fetchone()
            power=con.execute("SELECT power_w FROM forecast_shadow_points").fetchone()[0]
        self.assertEqual(state,("calibrated",.85,32,4))
        self.assertEqual(power,1700)

    def test_trained_shadow_has_measurable_paired_improvement_and_config_reset(self):
        self.store.ensure_schema()
        with self.store._connect() as con:
            for day in range(4):
                for hour in range(8,16):
                    ts=utc_iso(time("2026-10-05T00:00:00Z")+timedelta(days=day,hours=hour))
                    con.execute("""INSERT INTO forecast_shadow_quality_samples
                        (valid_at,horizon_minutes,issued_at,baseline_revision_id,model_id,
                         config_hash,baseline_w,shadow_w,actual_w,evaluated_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (ts,60,ts,1,1,self.signature,2000,2000,1600,ts))
        target="2026-10-14T14:00:00Z"
        self.issue("2026-10-14T13:00:00Z",target,2000)
        self.issue("2026-10-14T13:30:00Z","2026-10-14T14:30:00Z",2000,hash="b"*64)
        with self.store._connect() as con:
            calibrated=con.execute("SELECT power_w FROM forecast_shadow_points WHERE valid_at=?",
                                   (target,)).fetchone()[0]
            cold=con.execute("SELECT power_w FROM forecast_shadow_points WHERE valid_at=?",
                              ("2026-10-14T14:30:00Z",)).fetchone()[0]
        self.assertEqual((calibrated,cold),(1700,2000))
        with sqlite3.connect(self.history) as con:
            con.execute("CREATE TABLE history_samples(ts TEXT PRIMARY KEY,pv_power_w REAL,pv_quality TEXT)")
            con.execute("INSERT INTO history_samples VALUES (?,?,?)",
                        ("2026-10-14T14:00:03+00:00",1600,"live"))
        done=time("2026-10-14T14:03:00Z")
        self.assertEqual(evaluate_recent(self.store,history_db=self.history,
                         now=done,lookback_hours=4)["evaluated"],1)
        self.assertEqual(evaluate_pairs(self.store,now=done,lookback_hours=4)["evaluated"],1)
        report=quality_comparison(self.store,days=1,now=done)["metrics"]
        m=next(x for x in report if x["horizon_minutes"]==60 and x["scope"]=="daylight")
        self.assertEqual(m["baseline"]["mae_w"],400)
        self.assertEqual(m["shadow"]["mae_w"],100)
        self.assertEqual(m["mae_improvement_w"],300)

    def test_retention_only_prunes_archived_pairs_not_historical_curves(self):
        target="2025-01-01T15:00:00Z"
        self.issue("2025-01-01T14:00:00Z",target)
        self.store.prune_revisions(now=time("2026-10-15T00:00:00Z"),retention_days=365)
        with self.store._connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM forecast_shadow_revisions").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT count(*) FROM forecast_shadow_revision_points").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT count(*) FROM forecast_shadow_points").fetchone()[0],1)


if __name__ == "__main__":
    unittest.main()
