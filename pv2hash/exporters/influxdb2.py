"""InfluxDB 2.x line-protocol destination for PV2Hash historical records."""
from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import urlencode
import math
import httpx


def esc_tag(value):
    return str(value).replace('\\', '\\\\').replace(' ', '\\ ').replace(',', '\\,').replace('=', '\\=')


def esc_measurement(value):
    return str(value).replace('\\', '\\\\').replace(' ', '\\ ').replace(',', '\\,')


def field_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value) + 'i'
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else None
    if isinstance(value, str) and value:
        return '"' + value.replace('\\','\\\\').replace('"','\\"').replace('\n',' ') + '"'
    return None


def timestamp_ns(value):
    # integer arithmetic preserves exact millisecond / microsecond timestamps
    dt = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    epoch = datetime(1970,1,1,tzinfo=timezone.utc)
    delta = dt - epoch
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def line(measurement, tags, fields, ts, *, text_fields=()):
    """Write numeric fields and only explicitly allowlisted text columns."""
    selected = {k: v for k, v in fields.items()
                if isinstance(v, (int, float, bool)) and v is not None}
    for key in text_fields:
        value = fields.get(key)
        if isinstance(value, str) and value:
            selected[key] = value
    encoded = [(esc_tag(k),field_value(v)) for k,v in selected.items()]
    encoded = [(k,v) for k,v in encoded if v is not None]
    if not encoded:
        return None
    tag_text = ''.join(',' + esc_tag(k) + '=' + esc_tag(v) for k,v in tags.items() if v not in (None,''))
    return esc_measurement(measurement)+tag_text+' '+','.join(k+'='+v for k,v in encoded)+' '+str(timestamp_ns(ts))


def _running(miner):
    """Portal parity: only runtime_state=running means active mining."""
    return str(miner.get('runtime_state') or '').strip().lower() == 'running'


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    result = float(value)
    return result if math.isfinite(result) else 0.0


def mining_totals(miners):
    """Never include stale hashrate reported by paused or stopped miners."""
    running = [miner for miner in miners if _running(miner)]
    hashrate_ghs = sum((_number(miner.get('hashrate_ghs')) for miner in running), 0.0)
    return {
        'registered_miner_count': len(miners),
        'running_miner_count': len(running),
        'running_miner_power_w': sum((_number(miner.get('power_w')) for miner in running), 0.0),
        'running_hashrate_ghs': hashrate_ghs,
        'running_hashrate_ths': hashrate_ghs / 1000.0,
    }


def pool_active_state(value):
    """SQLite tri-state -> a stable InfluxDB string field.

    Missing / NULL is not inactive. Never write an InfluxDB field as null.
    """
    if value is None:
        return 'unknown'
    if value is True or value == 1:
        return 'active'
    if value is False or value == 0:
        return 'inactive'
    raise ValueError('Invalid pool activity status; expected NULL, 0, or 1')


class InfluxDB2Destination:
    def __init__(self, config):
        settings = config.get('settings', {})
        self.url = str(settings['url']).rstrip('/')
        self.org = str(settings['org'])
        self.bucket = str(settings['bucket'])
        self.token = str(settings['token'])
        self.timeout = int(settings.get('timeout_seconds', 10))
        self.precision = str(settings.get('precision','ns'))
        self.instance = str(config.get('instance_id') or 'pv2hash')
        self.instance_name = str(config.get('instance_name') or '')
        self.miner_names = config.get('miner_names') or {}

    def send(self, kind, items):
        lines = []
        for item in items:
            ts = item.get('ts')
            if kind == 'samples':
                tags = {'instance_id':item.get('instance_id') or self.instance, 'instance_name':self.instance_name}
                miners = item.get('miners') or []
                fields = {k:v for k,v in item.items() if k not in ('miners','ts','instance_id')}
                result = line('pv2hash_system',tags,fields,ts,
                              text_fields=('source_quality', 'battery_quality'))
                if result: lines.append(result)
                # A dedicated measurement keeps portal-compatible mining stats
                # isolated from any legacy field types in pv2hash_system.
                result = line('pv2hash_mining', tags, mining_totals(miners), ts)
                if result: lines.append(result)
                for miner in miners:
                    tags = {'instance_id':miner.get('instance_id') or item.get('instance_id') or self.instance,
                            'instance_name':self.instance_name,
                            'miner_id':miner.get('miner_id') or miner.get('miner_key'),
                            'miner_name':miner.get('name') or self.miner_names.get(str(miner.get('miner_id') or ''))}
                    miner_fields = dict(miner)
                    miner_fields['mining_active'] = int(_running(miner))
                    result = line('pv2hash_miner',tags,miner_fields,ts,
                                  text_fields=('runtime_state', 'profile', 'driver'))
                    if result: lines.append(result)
            elif kind == 'controller_events':
                tags = {'instance_id':self.instance, 'instance_name':self.instance_name,
                        'miner_id':item.get('miner_id') or item.get('miner_key'),
                        'miner_name':item.get('miner_name') or self.miner_names.get(str(item.get('miner_id') or '')),
                        'reason_code':item.get('reason_code')}
                fields = {k:v for k,v in item.items() if k not in ('ts','id')}
                fields['event_id'] = int(item['id'])
                result = line('pv2hash_controller_event',tags,fields,ts,
                              text_fields=('event_type', 'old_profile',
                                           'requested_profile', 'new_profile',
                                           'policy_mode', 'distribution_mode'))
                if result: lines.append(result)
            elif kind in ('pool_state', 'pool_events'):
                # Stable/low-cardinality series identity. Full BTC addresses,
                # complete Stratum usernames and pool hosts are FIELDS,
                # NEVER tags (also avoids exposing them in series labels).
                tags = {
                    'instance_id': item.get('instance_id') or self.instance,
                    'miner_id': item.get('miner_id'),
                    'pool_slot': item.get('pool_slot'),
                }
                if not tags['miner_id'] or tags['pool_slot'] is None:
                    raise ValueError('Pool export requires miner_id and pool_slot')
                fields = {
                    'host': item.get('host'),
                    'username': item.get('username'),
                    'active_state': pool_active_state(item.get('is_active')),
                }
                if item.get('port') is not None:
                    fields['port'] = int(item['port'])
                if kind == 'pool_state':
                    ts = item['last_seen_at']
                    result = line('pv2hash_pool', tags, fields, ts,
                                  text_fields=('host', 'username', 'active_state'))
                else:
                    ts = item['ts']
                    fields['event_id'] = int(item['id'])
                    fields['event_type'] = str(item['event_type'])
                    result = line('pv2hash_pool_event', tags, fields, ts,
                                  text_fields=('host', 'username', 'active_state',
                                               'event_type'))
                if result: lines.append(result)
            else:
                raise ValueError('Unsupported export stream: ' + str(kind))
        if not lines:
            return
        params = urlencode({'org':self.org,'bucket':self.bucket,'precision':'ns'})
        with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
            response = client.post(self.url+'/api/v2/write?'+params,
                                   content=('\n'.join(lines)+'\n').encode('utf-8'),
                                   headers={'Authorization':'Token '+self.token,'Content-Type':'text/plain; charset=utf-8'})
            if response.status_code != 204:
                raise RuntimeError(f'InfluxDB write failed (HTTP {response.status_code})')

    def test_connection(self):
        """Prove token can write to configured bucket (no real sample data)."""
        from uuid import uuid4
        marker = uuid4().hex
        payload = f'pv2hash_connection_test,test_id={marker} success=1i {timestamp_ns(datetime.now(timezone.utc).isoformat())}\n'
        params = urlencode({'org': self.org, 'bucket': self.bucket, 'precision': 'ns'})
        with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
            response = client.post(self.url+'/api/v2/write?'+params,
                                   content=payload.encode('utf-8'),
                                   headers={'Authorization': 'Token '+self.token,
                                            'Content-Type': 'text/plain; charset=utf-8'})
            if response.status_code != 204:
                raise RuntimeError(f'InfluxDB-Schreibtest fehlgeschlagen (HTTP {response.status_code})')
        return 'InfluxDB-Schreibtest erfolgreich (HTTP 204).'
