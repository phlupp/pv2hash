"""Independent exporter runner with durable, per-destination delivery cursors.

A cursor advances only after a whole batch is acknowledged by the adapter.
The receiver must be idempotent: a process crash after delivery but before
checkpointing can result in replay of the same batch.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .source import ExportSource


class Destination(Protocol):
    def send(self, kind: str, items: list[dict[str, Any]]) -> None:
        """Return on success; raise an exception for any failed batch."""


class ExportManager:
    def __init__(self, source: ExportSource, config_provider: Callable[[], dict[str, Any]],
                 state_path: str | Path = 'data/exporters.sqlite'):
        self.source = source
        self.config_provider = config_provider
        self.state_path = Path(state_path)
        self.adapters: dict[str, Callable[[dict[str, Any]], Destination]] = {}
        self._setup()

    def register(self, kind: str, factory: Callable[[dict[str, Any]], Destination]) -> None:
        self.adapters[kind] = factory

    def _connect(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.state_path, timeout=15)
        con.execute('PRAGMA busy_timeout=15000')
        return con

    def _setup(self):
        with self._connect() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS export_state (
              exporter_id TEXT NOT NULL, stream TEXT NOT NULL,
              cursor TEXT, last_success_at TEXT, last_error TEXT,
              consecutive_failures INTEGER NOT NULL DEFAULT 0,
              retry_after TEXT, PRIMARY KEY(exporter_id, stream))''')

    def _configured(self) -> list[dict[str, Any]]:
        exporters = self.config_provider().get('exporters', [])
        return exporters if isinstance(exporters, list) else []

    def _state(self, exporter_id: str, stream: str) -> dict[str, Any]:
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            row = con.execute('SELECT * FROM export_state WHERE exporter_id=? AND stream=?',
                              (exporter_id, stream)).fetchone()
        return dict(row) if row else {'cursor': None, 'last_success_at': None,
                                       'last_error': None, 'consecutive_failures': 0,
                                       'retry_after': None}

    def status(self) -> list[dict[str, Any]]:
        result = []
        # A small read-only query, once for all destinations.
        with sqlite3.connect(self.source.db_path) as con:
            latest_row = con.execute('SELECT MAX(ts) FROM history_samples').fetchone()
        latest_sample = latest_row[0] if latest_row else None
        for cfg in self._configured():
            key = str(cfg.get('id') or '')
            if not key:
                continue
            streams = {name: self._state(key, name) for name in ('samples', 'controller_events')}
            cursor = streams['samples']['cursor']
            backlog_seconds = None
            if latest_sample and cursor:
                try:
                    newest = datetime.fromisoformat(latest_sample.replace('Z', '+00:00'))
                    exported = datetime.fromisoformat(cursor.replace('Z', '+00:00'))
                    backlog_seconds = max(0, int((newest - exported).total_seconds()))
                except (ValueError, TypeError):
                    pass
            result.append({'id': key, 'type': cfg.get('type'), 'enabled': bool(cfg.get('enabled', False)),
                           'adapter_available': cfg.get('type') in self.adapters,
                           'latest_sample_at': latest_sample, 'exported_sample_at': cursor,
                           'backlog_seconds': backlog_seconds,
                           'synced': backlog_seconds is not None and backlog_seconds <= 30,
                           'streams': streams})
        return result

    def run_once(self) -> None:
        """Attempt one batch per stream for each enabled, supported destination."""
        for cfg in self._configured():
            key = str(cfg.get('id') or '')
            if not key or not cfg.get('enabled') or cfg.get('type') not in self.adapters:
                continue
            # Never allow two destinations to share the same persisted identity.
            if sum(str(x.get('id') or '') == key for x in self._configured()) != 1:
                continue
            try:
                adapter = self.adapters[cfg['type']](cfg)
            except Exception as exc:
                for stream in ('samples', 'controller_events'):
                    self._failure(key, stream, exc)
                continue
            for stream in ('samples', 'controller_events'):
                state = self._state(key, stream)
                now = datetime.now(timezone.utc)
                if state['retry_after'] and datetime.fromisoformat(state['retry_after']) > now:
                    continue
                try:
                    limit = max(1, min(int(cfg.get('batch_size', 120)), 1000))
                    batch = (self.source.samples(after=state['cursor'], limit=limit)
                             if stream == 'samples' else self.source.controller_events(
                                 after_id=int(state['cursor'] or 0), limit=limit))
                    if not batch['items']:
                        continue
                    adapter.send(stream, batch['items'])
                    self._success(key, stream, str(batch['next_cursor']))
                except Exception as exc:
                    self._failure(key, stream, exc)

    def _success(self, key: str, stream: str, cursor: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute('''INSERT INTO export_state(exporter_id,stream,cursor,last_success_at)
              VALUES(?,?,?,?) ON CONFLICT(exporter_id,stream) DO UPDATE SET
              cursor=excluded.cursor,last_success_at=excluded.last_success_at,
              consecutive_failures=0,last_error=NULL,retry_after=NULL''', (key, stream, cursor, now))

    def _failure(self, key: str, stream: str, error: Exception):
        from datetime import timedelta
        state = self._state(key, stream)
        failures = int(state['consecutive_failures']) + 1
        delay = min(3600, 15 * 2 ** min(failures - 1, 8))
        retry = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        with self._connect() as con:
            con.execute('''INSERT INTO export_state(exporter_id,stream,cursor,last_error,consecutive_failures,retry_after)
              VALUES(?,?,?,?,?,?) ON CONFLICT(exporter_id,stream) DO UPDATE SET
              last_error=excluded.last_error,consecutive_failures=excluded.consecutive_failures,
              retry_after=excluded.retry_after''', (key, stream, state['cursor'], str(error)[:500], failures, retry))

    async def run(self):
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                import logging
                logging.getLogger('pv2hash.exporters').exception('Unexpected exporter manager error')
            await asyncio.sleep(10)
