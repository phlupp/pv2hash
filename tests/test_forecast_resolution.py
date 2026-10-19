"""Separate forecast cadence and request schedule, with immutable history."""
from __future__ import annotations

import sqlite3
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from pv2hash.config.defaults import DEFAULT_CONFIG
from pv2hash.forecast import generators_from_config, utc_iso
from pv2hash.forecast.open_meteo import OpenMeteoDriver
from pv2hash.forecast.service import apply_settings, settings_model
from pv2hash.forecast.store import ForecastStore, TOTAL
from tests.test_forecast_foundation import config as sample_config


class SettingsTests(unittest.TestCase):
    def test_defaults_and_form_fields_are_independent(self):
        defaults = DEFAULT_CONFIG['forecast']['settings']
        self.assertEqual(defaults['resolution_minutes'], 15)
        self.assertEqual(defaults['refresh_minutes'], 30)
        model = settings_model(deepcopy(DEFAULT_CONFIG))
        self.assertEqual(model['settings']['resolution_minutes'], 15)
        self.assertEqual(model['settings']['refresh_minutes'], 30)
        fields = {item['name']:item for item in model['fields']}
        self.assertEqual([item['value'] for item in fields['resolution_minutes']['options']], ['15','60'])
        self.assertEqual(fields['refresh_minutes']['min'], 15)
        self.assertEqual(fields['refresh_minutes']['max'], 360)
        self.assertNotIn('api_key',model['settings'])

    def test_legacy_config_preserves_existing_refresh_and_uses_15m_cadence(self):
        cfg = sample_config()  # simulates existing configured instance
        new = apply_settings(cfg, {'settings': {'resolution_minutes': '15'}})
        self.assertEqual(new['forecast']['settings']['refresh_minutes'], 60)
        self.assertEqual(new['forecast']['settings']['resolution_minutes'], 15)
        self.assertEqual(cfg['forecast']['settings']['refresh_minutes'], 60)
        # Old installs have no resolution key at all.
        self.assertEqual(settings_model(cfg)['settings']['resolution_minutes'], 15)

    def test_independent_settings_persist_and_reject_invalid_without_side_effects(self):
        cfg = sample_config()
        cfg['miners']=[{'id':'unchanged'}]
        choices=[('15', '30', 15, 30), ('60', '15', 60, 15), ('15', '360', 15, 360)]
        for resolution, refresh, expected_resolution, expected_refresh in choices:
            with self.subTest(resolution=resolution, refresh=refresh):
                new=apply_settings(cfg,{'settings':{
                    'resolution_minutes':resolution,'refresh_minutes':refresh,
                }})
                settings=new['forecast']['settings']
                self.assertEqual((settings['resolution_minutes'],settings['refresh_minutes']),
                                 (expected_resolution,expected_refresh))
                self.assertEqual(new['miners'],cfg['miners'])
                self.assertEqual(settings_model(new)['settings']['resolution_minutes'],expected_resolution)
        for value in ('0', '10', '30', '61', '15.5', 'bogus', None):
            with self.subTest(bad_resolution=value):
                with self.assertRaisesRegex(ValueError,'Prognoseauflösung'):
                    apply_settings(cfg,{'settings':{'resolution_minutes':value}})
        for value in ('1', '361', 'invalid'):
            with self.subTest(bad_refresh=value):
                with self.assertRaises(ValueError):
                    apply_settings(cfg,{'settings':{'refresh_minutes':value}})
        self.assertNotIn('resolution_minutes',cfg['forecast']['settings'])


class OpenMeteoCadenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_15_minute_api_field_and_power_values_with_utc_timestamps(self):
        generator=generators_from_config(sample_config())[0]
        base=int(datetime.now(UTC).replace(minute=0,second=0,microsecond=0).timestamp())
        class Reply:
            def raise_for_status(self): pass
            def json(self):
                return {'timezone':'Europe/Berlin','minutely_15':{
                    'time':[base,base+900,base+1800,base+2700],
                    'global_tilted_irradiance_instant':[0,500,700,None],
                }}
        class Client:
            async def get(self,endpoint,params):
                self.endpoint=endpoint;self.params=params
                return Reply()
        driver=OpenMeteoDriver(mode='free',resolution_minutes=15)
        client=Client()
        zone,values=await driver._fetch_one(client,51.5,9.9,generator)
        self.assertEqual(zone,'Europe/Berlin')
        self.assertIn('minutely_15',client.params)
        self.assertNotIn('hourly',client.params)
        self.assertEqual(client.params['minutely_15'],'global_tilted_irradiance_instant')
        self.assertEqual((client.params['tilt'],client.params['azimuth']),(35,0))
        self.assertNotIn('apikey',client.params)
        self.assertEqual(len(values),3)
        self.assertEqual(list(values.values()),[0,2150.0,3010.0])
        times=[datetime.fromisoformat(x.replace('Z','+00:00')) for x in values]
        self.assertTrue(all((b-a).total_seconds()==900 for a,b in zip(times,times[1:])))

    async def test_60_minute_legacy_mode_and_missing_15m_must_not_fallback(self):
        generator=generators_from_config(sample_config())[0]
        base=int(datetime.now(UTC).replace(minute=0,second=0,microsecond=0).timestamp())
        class Reply:
            def raise_for_status(self): pass
            def json(self):
                return {'timezone':'Europe/Berlin','hourly':{
                    'time':[base,base+3600],
                    'global_tilted_irradiance_instant':[350,450],
                }}
        class Client:
            async def get(self,endpoint,params):
                self.params=params
                return Reply()
        client=Client()
        zone,points=await OpenMeteoDriver(resolution_minutes=60)._fetch_one(client,51,9,generator)
        self.assertEqual(zone,'Europe/Berlin')
        self.assertEqual(len(points),2)
        self.assertIn('hourly',client.params)
        self.assertNotIn('minutely_15',client.params)
        with self.assertRaisesRegex(RuntimeError,'gewählten Auflösung'):
            await OpenMeteoDriver(resolution_minutes=15)._fetch_one(client,51,9,generator)

    async def test_bad_15_minute_timestamps_are_rejected(self):
        generator=generators_from_config(sample_config())[0]
        base=int(datetime.now(UTC).replace(minute=0,second=0,microsecond=0).timestamp())
        for times in ([base,base+1200],[base,base],[base+900,base]):
            class Reply:
                def raise_for_status(self): pass
                def json(self):
                    return {'timezone':'Europe/Berlin','minutely_15':{
                        'time':times, 'global_tilted_irradiance_instant':[500]*len(times),
                    }}
            class Client:
                async def get(self,endpoint,params):return Reply()
            with self.subTest(times=times), self.assertRaisesRegex(RuntimeError,'Datenformat'):
                await OpenMeteoDriver(resolution_minutes=15)._fetch_one(Client(),51,9,generator)
        for invalid in (0,30,61,True):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                OpenMeteoDriver(resolution_minutes=invalid)


class ResolutionStoreTests(unittest.TestCase):
    def test_switch_15_to_60_only_removes_future_off_grid_points(self):
        with TemporaryDirectory() as directory:
            store=ForecastStore(Path(directory)/'forecast.sqlite')
            now=datetime.now(UTC)
            base=now.replace(minute=0,second=0,microsecond=0)+timedelta(hours=2)
            old=utc_iso(now-timedelta(hours=2))
            future=[utc_iso(base+timedelta(minutes=15*i)) for i in range(9)]
            store.save_future(now=now,provider='open_meteo',timezone='Europe/Berlin',
              surfaces={'wr1/east':{t:100 for t in future},
                        'wr2/west':{t:200 for t in future}}, resolution_minutes=15)
            with sqlite3.connect(store.path) as con:
                before=con.execute("SELECT COUNT(*) FROM forecast_points WHERE device_id=?",(TOTAL,)).fetchone()[0]
                self.assertEqual(before,9)
                con.execute("INSERT INTO forecast_points VALUES (?,?,?,?,?)",
                            (old,TOTAL,999,utc_iso(now-timedelta(days=1)),'open_meteo'))
            hourly={t:400 for t in (future[0],future[4],future[8])}
            store.save_future(now=now,provider='open_meteo',timezone='Europe/Berlin',
              surfaces={'wr1/east':hourly, 'wr2/west':hourly},resolution_minutes=60)
            with sqlite3.connect(store.path) as con:
                rows=con.execute('SELECT valid_at,device_id,power_w FROM forecast_points ORDER BY valid_at,device_id').fetchall()
            self.assertEqual(len(rows),10)  # 3*3 future + 1 immutable historic
            self.assertEqual([row[0] for row in rows if row[1]==TOTAL],
                             [old,future[0],future[4],future[8]])
            self.assertEqual([row[2] for row in rows if row[1]==TOTAL], [999,800,800,800])

    def test_new_future_points_must_match_selected_resolution(self):
        with TemporaryDirectory() as directory:
            store=ForecastStore(Path(directory)/'forecast.sqlite')
            now=datetime.now(UTC)
            next_hour=now.replace(minute=0,second=0,microsecond=0)+timedelta(hours=2)
            not_quarter=utc_iso(next_hour+timedelta(minutes=7))
            with self.assertRaisesRegex(ValueError,'Prognose-Zeitpunkte'):
                store.save_future(now=now,provider='test',timezone='Europe/Berlin',
                                  surfaces={'one/gen':{not_quarter:100}},resolution_minutes=15)
            with sqlite3.connect(store.path) as con:
                self.assertEqual(con.execute('SELECT count(*) FROM forecast_points').fetchone()[0],0)

    def test_hourly_points_are_not_treated_as_complete_15m_today(self):
        from zoneinfo import ZoneInfo
        with TemporaryDirectory() as directory:
            store=ForecastStore(Path(directory)/'forecast.sqlite')
            store.ensure_schema()
            local_now=datetime.now(ZoneInfo('Europe/Berlin'))
            selected=local_now.date().isoformat()
            a=datetime.combine(local_now.date(), datetime.min.time(),
                               tzinfo=ZoneInfo('Europe/Berlin')).astimezone(UTC)
            with store._connect() as con:
                con.execute("INSERT INTO forecast_meta VALUES('timezone','Europe/Berlin')")
                con.executemany('INSERT INTO forecast_points VALUES (?,?,?,?,?)', [
                    (utc_iso(a+timedelta(hours=i)),TOTAL,1000,utc_iso(a),'test')
                    for i in range(4)
                ])
            d=store.day(selected,resolution_minutes=15)
            self.assertIsNone(d['energy']['forecast']['kwh'])
            self.assertFalse(d['energy']['forecast']['complete'])
            self.assertEqual(len(d['forecast']),4)

    def test_15m_daily_energy_respects_gaps_and_23_25_hour_days(self):
        with TemporaryDirectory() as directory:
            store=ForecastStore(Path(directory)/'forecast.sqlite')
            store.ensure_schema()
            with store._connect() as con:
                con.execute("INSERT INTO forecast_meta VALUES('timezone','Europe/Berlin')")
                for start,end in (
                    ('2025-03-29T23:00:00Z','2025-03-30T22:00:00Z'),
                    ('2025-10-25T22:00:00Z','2025-10-26T23:00:00Z'),
                ):
                    a=datetime.fromisoformat(start.replace('Z','+00:00'))
                    b=datetime.fromisoformat(end.replace('Z','+00:00'))
                    items=[]
                    for i in range(int((b-a).total_seconds()/900)+1):
                        t=utc_iso(a+timedelta(minutes=15*i))
                        items.append((t,TOTAL,1000,t,'test'))
                    con.executemany('INSERT INTO forecast_points VALUES (?,?,?,?,?)',items)
            for day,hours in [('2025-03-30',23),('2025-10-26',25)]:
                d=store.day(day)
                self.assertEqual(len(d['forecast']),hours*4)
                self.assertEqual(d['energy']['forecast']['kwh'],hours)
                self.assertTrue(d['energy']['forecast']['complete'])
            with store._connect() as con:
                # Remove exactly one 15m point in a historical day.
                con.execute("DELETE FROM forecast_points WHERE valid_at=? AND device_id=?",
                            ('2025-03-30T10:15:00Z',TOTAL))
            d=store.day('2025-03-30')
            self.assertFalse(d['energy']['forecast']['complete'])
            self.assertLess(d['energy']['forecast']['kwh'],23)
            self.assertEqual(len(d['forecast']),23*4-1)


if __name__=='__main__':unittest.main()
