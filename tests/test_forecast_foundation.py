"""Forecast foundation contract: history freezes, future changes, generators per WR."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pv2hash.forecast import estimate_power_w, generators_from_config, utc_iso
from pv2hash.forecast.open_meteo import OpenMeteoDriver
from pv2hash.forecast.service import apply_settings, settings_model, update, actual_day
from pv2hash.forecast.store import ForecastStore, TOTAL


def config():
    return {
        "location": {"lat": 51.0, "lon": 9.0},
        "pv": {"enabled": True, "devices": [
            {"id": "wr1", "name": "Hausdach", "enabled": False,
             "generators": [
                 {"id": "south", "name": "Süd", "enabled": True,
                  "kwp": 5, "tilt": 35, "azimuth": 0, "loss_percent": 14},
                 {"id": "west", "name": "West", "enabled": True,
                  "kwp": 3, "tilt": 20, "azimuth": 90, "loss_percent": 10},
             ]},
            {"id": "wr2", "name": "Garage", "enabled": True,
             "generators": [
                 {"id": "east", "name": "Ost", "enabled": True,
                  "kwp": 2, "tilt": 30, "azimuth": -90, "loss_percent": 10},
             ]},
        ]},
        "forecast": {"enabled": True, "driver": "open_meteo",
                     "settings": {"mode": "free", "api_key": "", "refresh_minutes": 60}},
    }


class ForecastHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ForecastStore(Path(self.temp.name) / "forecast.sqlite")

    def test_future_points_update_while_past_is_never_written_or_overwritten(self):
        now = datetime.now(UTC).replace(microsecond=0)
        old = utc_iso(now - timedelta(hours=1))
        future = utc_iso(now + timedelta(hours=2))
        more_future = utc_iso(now + timedelta(hours=3))
        first = {
            "wr1/south": {old: 999, future: 400, more_future: 600},
            "wr1/west": {old: 999, future: 100, more_future: 200},
            "wr2/east": {old: 999, future: 200, more_future: 100},
        }
        result = self.store.save_future(now=now,provider="open_meteo",timezone="Europe/Berlin",surfaces=first)
        self.assertEqual(result["future_points"],2)
        with sqlite3.connect(self.store.path) as con:
            self.assertIsNone(con.execute("SELECT power_w FROM forecast_points WHERE valid_at=?",(old,)).fetchone())
            self.assertEqual(con.execute("SELECT power_w FROM forecast_points WHERE valid_at=? AND device_id=?",
                                         (future,TOTAL)).fetchone()[0],700)
            self.assertEqual(con.execute("SELECT power_w FROM forecast_points WHERE valid_at=? AND device_id='wr1'",
                                         (future,)).fetchone()[0],500)
        updated = {k:{future:float(v[future])+300,more_future:float(v[more_future])+400}
                   for k,v in first.items()}
        self.store.save_future(now=now,provider="open_meteo",timezone="Europe/Berlin",surfaces=updated)
        with sqlite3.connect(self.store.path) as con:
            self.assertEqual(con.execute("SELECT power_w FROM forecast_points WHERE valid_at=? AND device_id=?",
                                         (future,TOTAL)).fetchone()[0],1600)
        # The logical clock passed the first slot. It is permanently frozen.
        later = now + timedelta(hours=2, minutes=1)
        self.store.save_future(now=later,provider="open_meteo",timezone="Europe/Berlin",surfaces={
            k:{future:0, more_future:5000} for k in first
        })
        with sqlite3.connect(self.store.path) as con:
            self.assertEqual(con.execute("SELECT power_w FROM forecast_points WHERE valid_at=? AND device_id=?",
                                         (future,TOTAL)).fetchone()[0],1600)
            self.assertEqual(con.execute("SELECT power_w FROM forecast_points WHERE valid_at=? AND device_id=?",
                                         (more_future,TOTAL)).fetchone()[0],15000)

    def test_day_boundaries_are_dst_safe_and_tomorrow_maximum(self):
        self.store.ensure_schema()
        with self.store._connect() as con:
            con.execute("INSERT INTO forecast_meta VALUES('timezone','Europe/Berlin')")
        spring = self.store.day("2025-03-30")
        self.assertEqual((datetime.fromisoformat(spring["end"].replace("Z","+00:00")) -
                          datetime.fromisoformat(spring["start"].replace("Z","+00:00"))).total_seconds(),23*3600)
        fall = self.store.day("2025-10-26")
        self.assertEqual((datetime.fromisoformat(fall["end"].replace("Z","+00:00")) -
                          datetime.fromisoformat(fall["start"].replace("Z","+00:00"))).total_seconds(),25*3600)
        with self.assertRaises(ValueError):
            self.store.day("2999-10-01")

    def test_generator_surface_per_wr_is_independent_of_telemetry_status(self):
        generators = generators_from_config(config())
        self.assertEqual(len(generators),3)
        self.assertEqual([g.inverter_id for g in generators],["wr1","wr1","wr2"])
        south, west, east = generators
        self.assertEqual(estimate_power_w(south,1000),4300)
        self.assertEqual(estimate_power_w(west,0),0)
        self.assertEqual(estimate_power_w(east,10000),2000)
        invalid = config()
        invalid["pv"]["devices"][0]["generators"][0]["azimuth"] = 200
        with self.assertRaises(ValueError):
            generators_from_config(invalid)

    def test_forecast_config_save_keeps_miner_and_source_unchanged(self):
        old = config()
        old["miners"] = [{"id":"m1","secret":"unchanged"}]
        old["control"] = {"target":"unchanged"}
        old["source"] = {"host":"unchanged"}
        new = apply_settings(old,{
            "enabled":True,"driver":"open_meteo",
            "settings":{"mode":"free","refresh_minutes":30},
            "inverters":[{"id":d["id"],"generators":d["generators"]} for d in old["pv"]["devices"]],
        })
        self.assertEqual(new["miners"],old["miners"])
        self.assertEqual(new["control"],old["control"])
        self.assertEqual(new["source"],old["source"])
        self.assertEqual(new["pv"]["devices"][0]["generators"],old["pv"]["devices"][0]["generators"])

    def test_api_key_is_masked_and_paid_credentials_required(self):
        source = config()
        source["forecast"]["settings"]["api_key"] = "PRIVATE-KEY-123"
        public = settings_model(source)
        self.assertNotIn("PRIVATE-KEY-123",str(public))
        self.assertTrue(public["settings"]["api_key_configured"])
        with self.assertRaises(ValueError):
            OpenMeteoDriver(mode="customer",api_key="")

    def test_actual_line_excludes_partial_measurements(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp)/"history.sqlite"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE history_samples(ts TEXT, pv_power_w REAL, pv_quality TEXT)")
                for ts,p,q in [
                    ("2026-10-17T11:00:00+00:00",200,"live"),
                    ("2026-10-17T11:01:00+00:00",300,"live"),
                    ("2026-10-17T11:02:00+00:00",999,"degraded"),
                ]:
                    con.execute("INSERT INTO history_samples VALUES(?,?,?)",(ts,p,q))
            actual = actual_day("2026-10-17T10:00:00Z","2026-10-17T12:00:00Z",db)
            self.assertEqual(len(actual),1)
            self.assertEqual(actual[0]["power_w"],250.0)


class ForecastProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_meteo_adapter_reads_unixtime_and_orientation(self):
        gen = generators_from_config(config())[0]
        request_time = int((datetime.now(UTC)+timedelta(hours=2)).timestamp())
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"timezone":"Europe/Berlin","hourly":{
                    "time":[request_time],
                    "global_tilted_irradiance_instant":[500],
                }}
        class FakeClient:
            async def get(self,endpoint,params):
                self.last_params = params
                self.last_endpoint = endpoint
                return Response()
        fake=FakeClient()
        driver=OpenMeteoDriver()
        zone, points = await driver._fetch_one(fake,51,9,gen)
        self.assertEqual(zone,"Europe/Berlin")
        self.assertEqual(list(points.values()),[2150.0])
        self.assertEqual(fake.last_params["tilt"],35)
        self.assertEqual(fake.last_params["azimuth"],0)
        self.assertEqual(fake.last_params["timeformat"],"unixtime")
        self.assertNotIn("apikey",fake.last_params)

    async def test_all_provider_results_stored_in_single_completed_round(self):
        now = datetime.now(UTC)
        future = utc_iso(now+timedelta(hours=3))
        data = config()
        with tempfile.TemporaryDirectory() as temp:
            store=ForecastStore(Path(temp)/"forecast.sqlite")
            async def stub(self, lat, lon, generators):
                return "Europe/Berlin",{
                    g.inverter_id+"/"+g.generator_id:{future:float(idx*100)}
                    for idx,g in enumerate(generators,1)
                }
            with patch.object(OpenMeteoDriver,"fetch",stub):
                result=await update(data,store)
            self.assertEqual(result["inverters"],2)
            with sqlite3.connect(store.path) as con:
                count=con.execute("SELECT COUNT(*) FROM forecast_points").fetchone()[0]
                self.assertEqual(count,3)
                total=con.execute("SELECT power_w FROM forecast_points WHERE device_id=?",(TOTAL,)).fetchone()[0]
                self.assertEqual(total,600)
