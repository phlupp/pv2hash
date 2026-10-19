"""365d forecast retention, SQLite monitoring and opt-in Influx quality."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from pv2hash.forecast.store import ForecastStore,TOTAL
from pv2hash.forecast import utc_iso
from pv2hash.forecast.sqlite_status import SQLiteStatus
from pv2hash.exporters.source import ExportSource
from pv2hash.exporters.manager import ExportManager
from pv2hash.exporters.influxdb2 import InfluxDB2Destination


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=ForecastStore(Path(self.tmp.name)/'forecast.sqlite')
        self.fixed=datetime(2026,10,19,15,tzinfo=UTC)

    def add_revision(self,age_days:int,target_time=None):
        issued=self.fixed-timedelta(days=age_days)
        target=target_time or issued+timedelta(minutes=15)
        self.store.save_future(now=issued,provider='test',timezone='UTC',resolution_minutes=15,
            surfaces={'a/one':{utc_iso(target):500.0}})

    def test_strict_cutoff_only_old_revisions_and_atomic_child_delete(self):
        self.add_revision(366)
        self.add_revision(365)
        self.add_revision(364)
        self.store.ensure_schema()
        with self.store._connect() as db:
            db.execute("""INSERT INTO forecast_quality_samples
               (valid_at,horizon_minutes,issued_at,forecast_w,actual_w,error_w,evaluated_at)
               VALUES(?,?,?,?,?,?,?)""",(utc_iso(self.fixed),15,utc_iso(self.fixed),10.,11.,-1.,utc_iso(self.fixed)))
        first=self.store.prune_revisions(now=self.fixed,retention_days=365,batch_revisions=1)
        self.assertEqual(first['revisions_deleted'],1)
        self.assertEqual(first['points_deleted'],2)
        self.assertEqual(self.store.prune_revisions(now=self.fixed)['revisions_deleted'],0)
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM forecast_revisions').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM forecast_revision_points').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM forecast_quality_samples').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM forecast_points').fetchone()[0],6)
        self.assertEqual(self.store.prune_revisions(now=self.fixed+timedelta(days=1))['revisions_deleted'],1)

    def test_prune_without_database_does_not_create_one(self):
        result=self.store.prune_revisions(now=self.fixed)
        self.assertEqual(result['revisions_deleted'],0)
        self.assertFalse(self.store.path.exists())

    def test_invalid_retention_rejected(self):
        with self.assertRaises(ValueError): self.store.prune_revisions(retention_days=0)
        with self.assertRaises(ValueError): self.store.prune_revisions(batch_revisions=0)


class SQLiteTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.store=ForecastStore(self.root/'forecast.sqlite')
        self.status=SQLiteStatus({'forecast':(self.store.path,'PV-Prognose'),
            'history':(self.root/'missing.sqlite','Data Logger')},
            history_db=self.root/'monitor.sqlite')
        self.fixed=datetime(2026,10,19,15,tzinfo=UTC)

    def test_daily_history_growth_and_missing_files(self):
        self.store.ensure_schema()
        a=self.status.capture(now=self.fixed-timedelta(days=1))
        self.assertEqual(len(a['files']),2)
        self.assertFalse(a['files'][1]['present'])
        with self.store._connect() as db:
            db.execute("INSERT INTO forecast_meta(key,value) VALUES('test',?)",('X'*90000,))
        self.status.capture(now=self.fixed)
        self.status.capture(now=self.fixed+timedelta(minutes=4))
        with patch('pv2hash.forecast.sqlite_status.datetime') as clock:
            clock.now.return_value=self.fixed+timedelta(minutes=6)
            report=self.status.report(days=30)
        points=[p for p in report['history'] if p['key']=='forecast']
        self.assertEqual(len(points),2)  # daily downsampling; overwrite this hour
        self.assertLess(points[0]['bytes'],points[1]['bytes'])
        self.assertEqual(report['retention_days'],365)
        self.assertEqual(report['sample_interval_minutes'],60)
        self.assertTrue(report['sampled_files'][0]['present'])
        with self.assertRaises(ValueError):self.status.report(days=91)

    def test_free_pages_reusable_without_shrinking_database(self):
        self.store.ensure_schema()
        with self.store._connect() as db:
            db.execute("CREATE TABLE bigpayload(val TEXT)")
            db.executemany("INSERT INTO bigpayload(val) VALUES(?)",[('x'*3000,)]*400)
        before=self.status.capture(now=self.fixed)['files'][0]
        with self.store._connect() as db:
            db.execute("DELETE FROM bigpayload")
        after=self.status.capture(now=self.fixed+timedelta(hours=1))['files'][0]
        self.assertGreater(after['free_bytes'],before['free_bytes'])
        self.assertGreater(after['usable_bytes']+after['free_bytes'],0)


class ForecastQualityExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.forecast=ForecastStore(self.root/'forecast.sqlite');self.forecast.ensure_schema()
        self.history=self.root/'history.sqlite'
        with sqlite3.connect(self.history) as db:
            db.executescript('''CREATE TABLE history_samples(ts TEXT PRIMARY KEY);
                CREATE TABLE controller_events(id INTEGER PRIMARY KEY);
                CREATE TABLE miner_pool_events(id INTEGER PRIMARY KEY);
                CREATE TABLE miner_pools(miner_id TEXT);''')
        self.source=ExportSource(self.history,lambda:{},forecast_db_path=self.forecast.path)

    def add_quality(self,when='2026-10-19T10:00:00Z',horizon=60):
        with self.forecast._connect() as db:
            db.execute('''INSERT INTO forecast_quality_samples(valid_at,horizon_minutes,
                issued_at,forecast_w,actual_w,error_w,evaluated_at)
                VALUES (?,?,?,?,?,?,?)''',
                (when,horizon,'2026-10-19T09:00:00Z',1010.,900.,110.,when))

    def test_quality_export_cursor_survives_late_observation(self):
        self.add_quality(when='2026-10-19T10:00:00Z')
        batch=self.source.forecast_quality(limit=1)
        self.assertEqual(batch['count'],1)
        cursor=batch['next_cursor']
        self.add_quality(when='2026-10-19T09:00:00Z',horizon=15)
        later=self.source.forecast_quality(after_id=cursor)
        self.assertEqual(later['count'],1)
        self.assertEqual(later['items'][0]['valid_at'],'2026-10-19T09:00:00Z')

    def test_quality_opt_in_with_acknowledged_cursor_and_retry(self):
        self.add_quality()
        cfg={'exporters':[{'id':'test','type':'fake','enabled':True,'settings':{},'batch_size':120}]}
        manager=ExportManager(self.source,lambda:cfg,state_path=self.root/'exporter.sqlite')
        received=[]
        manager.register('fake',lambda _:type('Sink',(),{
            'send':lambda self,kind,items:received.append((kind,items))})())
        manager.run_once()
        self.assertFalse(any(kind=='forecast_quality' for kind,_ in received))
        cfg['exporters'][0]['settings']['forecast_quality_enabled']=True
        # Pretend that first send fails: cursor must not advance.
        attempts=[]
        class Failing:
            def send(self,kind,items):
                if kind=='forecast_quality':
                    attempts.append('fail')
                    raise RuntimeError('temporarily unavailable')
        manager.register('fake',lambda _:Failing())
        manager.run_once()
        self.assertIsNone(manager._state('test','forecast_quality')['cursor'])
        self.assertEqual(attempts,['fail'])
        # Clear retry time only (as if backoff elapsed) and then deliver.
        with manager._connect() as db:
            db.execute("UPDATE export_state SET retry_after=NULL WHERE exporter_id='test' AND stream='forecast_quality'")
        manager.register('fake',lambda _:type('Sink',(),{'send':lambda self,kind,items:received.append((kind,items))})())
        manager.run_once()
        self.assertEqual(manager._state('test','forecast_quality')['cursor'],'1')
        self.assertEqual([kind for kind,_ in received].count('forecast_quality'),1)

    def test_influx_quality_line_has_stable_tags_and_idempotent_timestamp(self):
        self.add_quality()
        item=self.source.forecast_quality()['items'][0]
        cfg={'settings':{'url':'https://example.invalid','org':'test','bucket':'test','token':'fake'},
             'instance_id':'abc','instance_name':'Node eins'}
        dest=InfluxDB2Destination(cfg)
        with patch('pv2hash.exporters.influxdb2.httpx.Client') as client:
            response=client.return_value.__enter__.return_value.post.return_value
            response.status_code=204
            dest.send('forecast_quality',[item]);dest.send('forecast_quality',[item])
            payload=client.return_value.__enter__.return_value.post.call_args_list[0].kwargs['content'].decode()
            self.assertIn('pv2hash_forecast_quality,instance_id=abc,instance_name=Node\\ eins,horizon=60m',payload)
            self.assertIn('forecast_w=1010.0,actual_w=900.0,error_w=110.0,abs_error_w=110.0',payload)
            self.assertNotIn('quality_id',payload)
            self.assertEqual(client.return_value.__enter__.return_value.post.call_args_list[0].kwargs['content'],
                             client.return_value.__enter__.return_value.post.call_args_list[1].kwargs['content'])

if __name__=='__main__':unittest.main()
