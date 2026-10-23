"""Optional, append-only Shadow quality export: compatibility, cursors and privacy."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from pv2hash.forecast import Generator
from pv2hash.forecast.store import ForecastStore
from pv2hash.forecast.shadow import plant_summary
from pv2hash.exporters.source import ExportSource
from pv2hash.exporters.manager import ExportManager
from pv2hash.exporters.influxdb2 import InfluxDB2Destination
from pv2hash.exporters.api import _has_verified_connection, fingerprint
from pv2hash.exporters.configuration import normalize


class ShadowExportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = ForecastStore(self.root / 'forecast.sqlite')
        self.store.ensure_schema()
        self.logger = self.root / 'history.sqlite'
        with sqlite3.connect(self.logger) as con:
            con.executescript("""CREATE TABLE history_samples(ts TEXT PRIMARY KEY);
                CREATE TABLE controller_events(id INTEGER PRIMARY KEY);
                CREATE TABLE miner_pool_events(id INTEGER PRIMARY KEY);
                CREATE TABLE miner_pools(miner_id TEXT);""")
        self.source = ExportSource(self.logger,lambda:{},forecast_db_path=self.store.path)

    def sample(self, ts='2026-10-19T13:00:00Z', horizon=60, actual=1600,
               model_id=1, cfg='hash_A', evaluated=None):
        issued = '2026-10-19T12:00:00Z'
        evaluated = evaluated or '2026-10-19T13:01:30Z'
        with self.store._connect() as con:
            con.execute("""INSERT OR IGNORE INTO forecast_shadow_models
                (model_id,calculated_at,model_version,config_hash,status,factor,
                 sample_count,day_count,window_start,window_end,diagnostics,plant_summary)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (model_id,issued,'site-median-v1',cfg,'calibrated',0.9,
                 100,8,'2026-10-01T00:00:00Z',issued,'{}',
                 json.dumps({'capacity_kwp':8.5,'generator_count':3,
                             'inverter_count':2,'tilt_weighted_deg':33,
                             'south_kwp':5.5,'east_kwp':3.0})))
            con.execute("""INSERT INTO forecast_shadow_quality_samples
                (valid_at,horizon_minutes,issued_at,baseline_revision_id,
                 model_id,config_hash,baseline_w,shadow_w,actual_w,evaluated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (ts,horizon,issued,7,model_id,cfg,2000,1800,actual,evaluated))

    def test_migrate_existing_shadow_quality_preserves_samples_and_cursor(self):
        with self.store._connect() as db:
            db.execute('DROP TABLE forecast_shadow_quality_samples')
            db.execute("""CREATE TABLE forecast_shadow_quality_samples (
                valid_at TEXT NOT NULL, horizon_minutes INTEGER NOT NULL,
                issued_at TEXT NOT NULL, baseline_revision_id INTEGER NOT NULL,
                model_id INTEGER NOT NULL, config_hash TEXT NOT NULL,
                baseline_w REAL NOT NULL,shadow_w REAL NOT NULL,
                actual_w REAL NOT NULL,evaluated_at TEXT NOT NULL,
                PRIMARY KEY(valid_at,horizon_minutes))""")
            db.execute("""INSERT INTO forecast_shadow_quality_samples
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ('2026-10-19T13:00:00Z',60,'2026-10-19T12:00:00Z',
                  1,1,'hash',2000.,1800.,1600.,'2026-10-19T13:01:30Z'))
        self.store.ensure_schema()
        with self.store._connect() as db:
            self.assertIn('quality_id',{r[1] for r in db.execute(
                'PRAGMA table_info(forecast_shadow_quality_samples)')})
            self.assertEqual(db.execute("""SELECT quality_id,actual_w
                FROM forecast_shadow_quality_samples""").fetchone(),(1,1600.))
        self.assertEqual(self.source.forecast_shadow_quality()['items'][0]['quality_id'],1)

    def test_source_late_samples_have_stable_cursor_after_vacuum(self):
        self.sample()
        first = self.source.forecast_shadow_quality(limit=1)
        self.assertEqual(first['items'][0]['quality_id'],1)
        self.assertEqual(first['items'][0]['model_factor'],0.9)
        self.assertEqual(first['items'][0]['capacity_kwp'],8.5)
        self.assertEqual(first['items'][0]['generator_count'],3)
        self.sample('2026-10-18T13:00:00Z',15,actual=1400,model_id=2)
        with self.store._connect() as db:
            db.execute('VACUUM')
        later = self.source.forecast_shadow_quality(after_id=first['next_cursor'])
        self.assertEqual((later['count'],later['items'][0]['valid_at'],later['next_cursor']),
                         (1,'2026-10-18T13:00:00Z',2))
        self.assertEqual(self.source.forecast_shadow_quality(after_id=2)['items'],[])
        self.assertNotIn('lat',later['items'][0])

    def test_legacy_installation_without_forecast_does_not_create_archive(self):
        empty = self.root/'not-yet.sqlite'
        src = ExportSource(self.logger,lambda:{},forecast_db_path=empty)
        self.assertEqual(src.forecast_shadow_quality()['items'],[])
        self.assertFalse(empty.exists())

    def test_opt_in_ack_retry_no_network_dependency_for_learning(self):
        self.sample()
        cfg={'exporters':[{'id':'site','type':'fake','enabled':True,
                           'settings':{'forecast_quality_enabled':False},
                           'batch_size':1}]}
        manager=ExportManager(self.source,lambda:cfg,state_path=self.root/'exporters.sqlite')
        seen = []
        class Sink:
            fail = False
            def send(self,kind,items):
                if kind == 'forecast_shadow_quality':
                    seen.append(items[0]['quality_id'])
                    if self.fail:
                        raise RuntimeError('temporary remote failure')
        sink=Sink()
        manager.register('fake',lambda _:sink)
        manager.run_once()
        self.assertNotIn('forecast_shadow_quality',manager.status()[0]['streams'])
        cfg['exporters'][0]['settings']['forecast_shadow_quality_enabled']=True
        sink.fail=True
        manager.run_once()
        self.assertEqual(seen,[1])
        self.assertIsNone(manager._state('site','forecast_shadow_quality')['cursor'])
        with manager._connect() as con:
            con.execute("""UPDATE export_state SET retry_after=NULL
                WHERE exporter_id='site' AND stream='forecast_shadow_quality'""")
        sink.fail=False
        manager.run_once()
        self.assertEqual(seen,[1,1])
        self.assertEqual(manager._state('site','forecast_shadow_quality')['cursor'],'1')
        manager.run_once()
        self.assertEqual(seen,[1,1])

    def test_influx_line_is_paired_idempotent_bounded_tags_no_identifying_geometry(self):
        self.sample()
        row=self.source.forecast_shadow_quality()['items'][0]
        dest=InfluxDB2Destination({
            'settings':{'url':'https://test.invalid','org':'org','bucket':'test','token':'xyz'},
            'instance_id':'test-instance','instance_name':'Site A',
        })
        with patch('pv2hash.exporters.influxdb2.httpx.Client') as mock:
            mock.return_value.__enter__.return_value.post.return_value.status_code=204
            dest.send('forecast_shadow_quality',[row])
            dest.send('forecast_shadow_quality',[row])
            calls=mock.return_value.__enter__.return_value.post.call_args_list
            self.assertEqual(calls[0].kwargs['content'],calls[1].kwargs['content'])
            sent=calls[0].kwargs['content'].decode()
        self.assertIn('pv2hash_forecast_shadow_quality,instance_id=test-instance,instance_name=Site\\ A,horizon=60m ',sent)
        self.assertIn('baseline_w=2000.0,shadow_w=1800.0,actual_w=1600.0',sent)
        self.assertIn('baseline_error_w=400.0',sent)
        self.assertIn('shadow_error_w=200.0',sent)
        self.assertIn('mae_improvement_w=200.0',sent)
        self.assertIn('model_factor=0.9',sent)
        self.assertIn('generator_count=3i',sent)
        self.assertIn('config_hash="hash_A"',sent)
        header=sent.split(' ',1)[0]
        self.assertNotIn('model_version=',header)
        self.assertNotIn('config_hash=',header)
        self.assertNotIn('quality_id=',sent)
        self.assertNotIn('latitude',sent)
        self.assertNotIn('longitude',sent)

    def test_negative_live_pv_offset_does_not_block_shadow_export_cursor(self):
        # Regression: real Hashpis reported -21 W / -26 W near sunset.
        # The original paired actual must survive the export without clipping.
        self.sample(ts='2026-10-23T16:00:00Z',actual=-21)
        self.sample(ts='2026-10-22T16:15:00Z',horizon=180,actual=-26,model_id=2)
        self.sample(ts='2026-10-23T16:30:00Z',horizon=30,actual=250,model_id=3)
        with self.store._connect() as db:
            db.execute("""UPDATE forecast_shadow_quality_samples
                SET baseline_w=11.03,shadow_w=11.03
                WHERE valid_at='2026-10-23T16:00:00Z'""")
        exported=[]
        dest=InfluxDB2Destination({
            'settings':{'url':'https://test.invalid','org':'org','bucket':'test','token':'xyz'},
            'instance_id':'test-instance','instance_name':'Site A',
        })
        original_send=dest.send
        def capture(kind,items):
            exported.extend(items)
            original_send(kind,items)
        dest.send=capture
        config={'exporters':[{'id':'site','type':'influxdb2','enabled':True,
                             'settings':{'forecast_shadow_quality_enabled':True,
                                         'forecast_quality_enabled':False},'batch_size':50}]}
        manager=ExportManager(self.source,lambda:config,state_path=self.root/'cursors.sqlite')
        manager.register('influxdb2',lambda _:dest)
        with patch('pv2hash.exporters.influxdb2.httpx.Client') as http:
            http.return_value.__enter__.return_value.post.return_value.status_code=204
            manager.run_once()
            sent='\n'.join(c.kwargs['content'].decode()
                           for c in http.return_value.__enter__.return_value.post.call_args_list)
        self.assertIn('actual_w=-21.0',sent)
        self.assertIn('baseline_error_w=32.03',sent)
        self.assertIn('shadow_error_w=32.03',sent)
        self.assertIn('actual_w=-26.0',sent)
        self.assertIn('actual_w=250.0',sent)
        self.assertEqual([r['quality_id'] for r in exported],[1,2,3])
        self.assertEqual(manager._state('site','forecast_shadow_quality')['cursor'],'3')
        self.assertEqual(manager._state('site','forecast_shadow_quality')['consecutive_failures'],0)
        # Forecast predictions remain non-negative and invalid payloads still fail.
        invalid={**exported[0], 'baseline_w':-10}
        with self.assertRaisesRegex(ValueError,'Invalid shadow quality'):
            dest.send('forecast_shadow_quality',[invalid])
        invalid={**exported[0], 'actual_w':float('nan')}
        with self.assertRaisesRegex(ValueError,'Invalid shadow quality'):
            dest.send('forecast_shadow_quality',[invalid])

    def test_exporter_checkbox_toggle_preserves_verified_connection(self):
        settings = {'url':'https://example.invalid:8086','org':'org','bucket':'pv2hash-dev',
                    'token':'abc','batch_size':120,'timeout_seconds':10,'precision':'ns',
                    'forecast_quality_enabled':False}
        entry={'type':'influxdb2','settings':settings.copy(),'tested_at':'2026-10-19'}
        # Fingerprint from previous release: ignores batch_size + forecast_quality.
        old = {k:v for k,v in settings.items()
               if k not in ('batch_size','forecast_quality_enabled')}
        entry['tested_fingerprint']=hashlib.sha256(
            json.dumps([entry['type'],old],sort_keys=True).encode()).hexdigest()
        new=normalize('influxdb2',dict(settings,forecast_shadow_quality_enabled=True),settings)
        entry['settings']=new
        self.assertTrue(_has_verified_connection(entry))
        self.assertEqual(entry["tested_fingerprint"],fingerprint(entry))
        self.assertTrue(new['forecast_shadow_quality_enabled'])

    def test_plant_descriptors_are_aggregates_only(self):
        gens=[
            Generator('private-wr-a','Name A','s1','Roof',5.5,40,0,14),
            Generator('private-wr-b','Name B','e1','Garage',3,20,-90,10),
        ]
        summary=plant_summary(gens)
        self.assertEqual(summary['generator_count'],2)
        self.assertEqual(summary['inverter_count'],2)
        self.assertEqual(summary['south_kwp'],5.5)
        self.assertEqual(summary['east_kwp'],3)
        self.assertEqual(summary['capacity_kwp'],8.5)
        self.assertNotIn('private-wr-a',json.dumps(summary))


if __name__ == '__main__':
    unittest.main()
