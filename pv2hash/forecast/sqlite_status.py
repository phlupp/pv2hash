"""Low-overhead SQLite storage telemetry for the System page.

One observation per DB and hour, read-only PRAGMA checks for monitored DBs.
Observations are stored in a separate tiny SQLite database, never in
DataLogger / forecast / export databases. No VACUUM is run automatically.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3

DEFAULT_HISTORY = Path('data/sqlite_status.sqlite')
HISTORY_DAYS = 90


def inspect_file(path: Path, key: str, title: str) -> dict:
    """Return real file sizes plus allocated/free SQLite pages, no writes."""
    if not path.is_file():
        return {"key": key, "name": title, "present": False, "bytes": 0,
                "wal_bytes": 0, "free_bytes": 0, "usable_bytes": 0,
                "page_count": 0, "page_size": 0}
    # An immutable URI ignores WAL, so use SQLite read-only (mode=ro), which
    # also sees the most recent committed WAL pages without checkpointing.
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True,
                                 timeout=4)) as db:
        page_size = int(db.execute('PRAGMA page_size').fetchone()[0])
        page_count = int(db.execute('PRAGMA page_count').fetchone()[0])
        freelist = int(db.execute('PRAGMA freelist_count').fetchone()[0])
        counts = {}
        if key == 'forecast':
            for column,table in (('revision_count','forecast_revisions'),
                                 ('quality_count','forecast_quality_samples')):
                try:
                    counts[column] = int(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
                except sqlite3.OperationalError as exc:
                    if 'no such table:' not in str(exc):
                        raise
                    counts[column] = None
    size = path.stat().st_size
    wal_path = Path(str(path) + '-wal')
    wal_bytes = wal_path.stat().st_size if wal_path.is_file() else 0
    return {"key": key, "name": title, "present": True, "bytes": size,
            "wal_bytes": wal_bytes, "free_bytes": freelist * page_size,
            "usable_bytes": max(0, page_count - freelist) * page_size,
            "page_count": page_count, "page_size": page_size, **counts}


class SQLiteStatus:
    def __init__(self, paths: dict[str, tuple[Path, str]],
                 *, history_db: Path = DEFAULT_HISTORY):
        self.paths = paths
        self.history_db = Path(history_db)

    @contextmanager
    def _connect(self):
        self.history_db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.history_db, timeout=8)) as db:
            with db:
                yield db

    def ensure_schema(self):
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS storage_hourly_status(
                hour_utc TEXT NOT NULL, db_key TEXT NOT NULL,
                bytes INTEGER NOT NULL, wal_bytes INTEGER NOT NULL,
                free_bytes INTEGER NOT NULL, usable_bytes INTEGER NOT NULL,
                present INTEGER NOT NULL,
                PRIMARY KEY(hour_utc,db_key))''')
            db.execute('''CREATE INDEX IF NOT EXISTS storage_status_by_time
                ON storage_hourly_status(db_key,hour_utc)''')

    def capture(self, *, now: datetime | None = None) -> dict:
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError('UTC time required')
        utc_now = instant.astimezone(UTC)
        bucket = utc_now.replace(minute=0,second=0,microsecond=0).isoformat().replace('+00:00','Z')
        limit = (utc_now - timedelta(days=HISTORY_DAYS)).isoformat().replace('+00:00','Z')
        self.ensure_schema()
        stats = [inspect_file(path,key,title) for key,(path,title) in self.paths.items()]
        with self._connect() as db:
            db.executemany('''INSERT INTO storage_hourly_status
                (hour_utc,db_key,bytes,wal_bytes,free_bytes,usable_bytes,present)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(hour_utc,db_key) DO UPDATE SET
                bytes=excluded.bytes,wal_bytes=excluded.wal_bytes,
                free_bytes=excluded.free_bytes,usable_bytes=excluded.usable_bytes,
                present=excluded.present''',
                [(bucket, s['key'], s['bytes'], s['wal_bytes'],
                  s['free_bytes'],s['usable_bytes'],int(s['present'])) for s in stats])
            db.execute('DELETE FROM storage_hourly_status WHERE hour_utc < ?', (limit,))
        return {'captured_at':utc_now.isoformat(), 'files':stats}

    def report(self, *, days: int = 30) -> dict:
        if not 1 <= days <= HISTORY_DAYS:
            raise ValueError('SQLite history days must be between 1 and 90')
        now=datetime.now(UTC)
        cutoff = (now - timedelta(days=days)).isoformat().replace('+00:00','Z')
        files=[]
        for key,(path,title) in self.paths.items():
            try:
                files.append(inspect_file(path,key,title))
            except (sqlite3.Error, OSError) as exc:
                files.append({'key':key,'name':title,'present':path.exists(),
                              'error':type(exc).__name__,'bytes':None,
                              'wal_bytes':None,'free_bytes':None,'usable_bytes':None})
        history=[]
        if self.history_db.exists():
            with self._connect() as db:
                for row in db.execute('''SELECT s.hour_utc,s.db_key,s.bytes,s.wal_bytes,
                    s.free_bytes,s.usable_bytes FROM storage_hourly_status s
                    JOIN (SELECT db_key, SUBSTR(hour_utc,1,10) as day,
                          MAX(hour_utc) AS latest
                          FROM storage_hourly_status WHERE hour_utc>=?
                          GROUP BY db_key, SUBSTR(hour_utc,1,10)) AS last
                    ON last.db_key=s.db_key AND last.latest=s.hour_utc
                    ORDER BY s.hour_utc,s.db_key''',(cutoff,)):
                    history.append({'ts':row[0],'key':row[1],'bytes':row[2],
                                    'wal_bytes':row[3], 'free_bytes':row[4],
                                    'usable_bytes':row[5]})
        return {'status':'ok','sample_interval_minutes':60,'history_days':days,
                'retention_days':365, 'sampled_files':files, 'history':history,
                'last_sample_at':history[-1]['ts'] if history else None,
                'note':'Freie SQLite-Seiten können wiederverwendet werden; WAL zählt zusätzlich zum Dateiumfang.'}
