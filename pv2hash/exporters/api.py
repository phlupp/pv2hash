"""Exporter CRUD endpoints registered on the existing FastAPI app."""
from __future__ import annotations
from uuid import uuid4
from datetime import datetime, timezone
import asyncio
import hashlib
import json
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pv2hash.exporters.schema import DEFINITIONS
from pv2hash.exporters.configuration import normalize
from pv2hash.exporters.influxdb2 import InfluxDB2Destination


def fingerprint(entry):
    # Bind test approval to actual credentials and connection settings.
    settings = entry.get('settings', {})
    payload = {k:v for k,v in settings.items() if k != 'batch_size'}
    return hashlib.sha256(json.dumps([entry.get('type'),payload], sort_keys=True).encode()).hexdigest()


def install(app, state, save_config, manager):
    def public_entries():
        result = []
        for entry in state.config.get('exporters', []):
            item = {key: entry.get(key) for key in ('id', 'type', 'name', 'enabled')}
            definition = DEFINITIONS.get(item['type'])
            item['schema'] = definition.render(entry.get('settings', {})) if definition else None
            item['configured'] = bool(entry.get('settings', {}).get('token'))
            item['test_ok'] = bool(entry.get('tested_fingerprint') == fingerprint(entry) and entry.get('tested_at'))
            item['tested_at'] = entry.get('tested_at') if item['test_ok'] else None
            result.append(item)
        return result

    @app.get('/api/exporters/config')
    async def get_exporters():
        return JSONResponse(jsonable_encoder({'status': 'ok', 'exporters': public_entries(), 'types': [d.render() for d in DEFINITIONS.values()]}))

    @app.post('/api/exporters/config')
    async def put_exporter(request: Request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError('Ungültige Konfiguration')
            kind = str(payload.get('type') or '')
            name = str(payload.get('name') or '').strip()
            if not name or len(name) > 80:
                raise ValueError('Ein Name mit maximal 80 Zeichen ist erforderlich')
            eid = str(payload.get('id') or '')
            entries = state.config.setdefault('exporters', [])
            old = next((e for e in entries if e.get('id') == eid), None) if eid else None
            if eid and old is None:
                raise ValueError('Exportziel nicht gefunden')
            if old and old.get('type') != kind:
                raise ValueError('Exportertyp kann nicht nachträglich geändert werden')
            settings = normalize(kind, payload.get('settings') if isinstance(payload.get('settings'), dict) else {}, old.get('settings') if old else None)
            entry = {'id': eid if old else str(uuid4()), 'type': kind, 'name': name,
                     'enabled': False, 'settings': settings,
                     'batch_size': settings.get('batch_size', 120)}
            if old and old.get('tested_fingerprint') == fingerprint(entry):
                entry['tested_fingerprint'] = old['tested_fingerprint']
                entry['tested_at'] = old.get('tested_at')
            requested_enabled = bool(payload.get('enabled', False))
            if requested_enabled and entry.get('tested_fingerprint') != fingerprint(entry):
                raise ValueError('Vor dem Aktivieren muss die gespeicherte Verbindung erfolgreich getestet werden.')
            entry['enabled'] = requested_enabled
            if old:
                entries[entries.index(old)] = entry
            else:
                entries.append(entry)
            save_config(state.config)
            return JSONResponse(jsonable_encoder({'status':'ok','exporters':public_entries()}))
        except ValueError as exc:
            return JSONResponse({'status':'error','message':str(exc)}, status_code=400)

    @app.post('/api/exporters/config/{exporter_id}/test')
    async def test_exporter(exporter_id: str):
        entry = next((e for e in state.config.get('exporters', []) if e.get('id') == exporter_id), None)
        if entry is None:
            return JSONResponse({'status':'error','message':'Exportziel nicht gefunden'}, status_code=404)
        adapter_factory = manager.adapters.get(entry.get('type'))
        if adapter_factory is None:
            return JSONResponse({'status':'error','message':'Kein Test für diesen Exportertyp verfügbar'}, status_code=400)
        current_fingerprint = fingerprint(entry)
        try:
            adapter = adapter_factory(entry)
            message = await asyncio.to_thread(adapter.test_connection)
        except Exception as exc:
            # Do not reveal URL, credentials or raw HTTP body in error responses.
            code = str(exc)
            message = code if code.startswith('InfluxDB-Schreibtest fehlgeschlagen (HTTP ') else 'Verbindungstest fehlgeschlagen. Server oder Netzwerk prüfen.'
            return JSONResponse({'status':'error','message':message}, status_code=400)
        if fingerprint(entry) != current_fingerprint:
            return JSONResponse({'status':'error','message':'Konfiguration wurde während des Tests verändert.'}, status_code=409)
        entry['tested_fingerprint'] = current_fingerprint
        entry['tested_at'] = datetime.now(timezone.utc).isoformat()
        save_config(state.config)
        return JSONResponse({'status':'ok','message':message,'exporters':public_entries()})

    @app.delete('/api/exporters/config/{exporter_id}')
    async def delete_exporter(exporter_id: str):
        entries = state.config.setdefault('exporters', [])
        old = next((e for e in entries if e.get('id') == exporter_id), None)
        if old is None:
            return JSONResponse({'status':'error','message':'Exportziel nicht gefunden'},status_code=404)
        entries.remove(old)
        save_config(state.config)
        return JSONResponse(jsonable_encoder({'status':'ok','exporters':public_entries()}))
