# PV2Hash Portal Integration

PV2Hash can connect a local instance to a PV2Hash Portal through a pairing code.
The local PV2Hash instance keeps its own stable instance UUID as the primary node identity.

## Configuration

Portal settings are stored under `portal` in `data/config.json`:

- `enabled`: enables automatic snapshot uploads when an API token is available
- `base_url`: portal base URL, default `https://pv2hash.xyz`
- `api_token`: bearer token returned by the portal during pairing
- `api_token_prefix`: safe display prefix for the token
- `portal_uuid`: optional portal-internal instance UUID
- `paired_at`: local pairing timestamp
- `last_success_at`: last successful snapshot upload
- `last_snapshot_at`: last successful snapshot upload timestamp
- `last_error`: last portal error message
- `upload_interval_seconds`: automatic snapshot upload interval

The API token is never shown in the UI after pairing.

## Pairing

The Settings page contains a PV2Hash Portal section.

1. Enter or confirm the Portal URL.
2. Enter the pairing code shown by the portal.
3. Click **Portal verbinden**.

PV2Hash sends:

```json
{
  "pairing_code": "XXXX-XXXX",
  "instance": {
    "uuid": "<local instance UUID>",
    "name": "PV2Hash Node",
    "version": "0.6.x",
    "version_full": "0.6.x",
    "hostname": "testvm02"
  }
}
```

The returned `api_token` is stored locally and only the token prefix is displayed.

## Snapshots

Snapshots are sent to:

```text
POST /api/v1/snapshots/
Authorization: Bearer <api_token>
```

PV2Hash sends the internal runtime snapshot as a JSON-safe portal payload. The top-level structure includes the same operational sections that are exposed by the local status snapshot:

```json
{
  "schema_version": 1,
  "status": "ok",
  "timestamp": "2026-05-08T12:00:00+00:00",
  "instance": {
    "id": "<local instance UUID>",
    "uuid": "<local instance UUID>",
    "name": "PV2Hash Node",
    "version": "0.7.x",
    "version_full": "0.7.x",
    "hostname": "testvm02",
    "status": "online"
  },
  "host": {},
  "controller": {},
  "source": {},
  "battery": {},
  "miners": [],
  "sockets": [],
  "settings": {
    "schema_version": 1,
    "app": {},
    "system": {},
    "control": {},
    "datalogger": {},
    "portal": {
      "enabled": true,
      "base_url": "https://pv2hash.xyz",
      "connected": true,
      "api_token_prefix": "pv2_...",
      "upload_interval_seconds": 60,
      "token_present": true
    }
  },
  "totals": {
    "miner_power_w": 1234,
    "miner_hashrate_ghs": 98500,
    "hashrate_ths": 98.5,
    "grid_power_w": -350,
    "battery_soc": 82
  }
}
```

`host`, `controller`, `source`, `battery`, `miners`, `sockets`, `settings` and `totals` are intentionally sent to the portal so the portal can build dashboards without needing separate local API calls. The compact compatibility fields `instance.uuid`, `instance.status`, `totals.hashrate_ths`, `totals.grid_power_w` and `totals.battery_soc` remain available for older portal consumers.

The `settings` section contains instance-level settings only: `app`, `system`, `control`, `datalogger` and safe portal metadata. Device-specific configuration for `source`, `battery`, `miners` and `sockets` is intentionally not included yet. Portal secrets are never sent; `portal.api_token` is omitted and only `token_present` plus the safe `api_token_prefix` are included.

A manual **Test-Snapshot senden** button is available in the Portal settings card.
When `portal.enabled` is active and a token is present, PV2Hash also sends snapshots periodically.

## Disconnect

**Verbindung trennen** clears the local token and portal UUID and disables automatic uploads. It does not delete the instance in the portal.


## Logging und Diagnose

Portal-Verbindungsprobleme werden zusätzlich zum UI-Feld `portal.last_error` in das normale PV2Hash-Log geschrieben. Das betrifft insbesondere:

- fehlgeschlagenes Pairing mit HTTP-Status und Portal-Fehlercode
- fehlgeschlagene Snapshot-Uploads mit HTTP-Status und Portal-Fehlercode
- Verbindungsfehler, Timeouts und ungültige JSON-Antworten
- erfolgreiche Pairings, Test-Snapshots und lokale Trennung

Der API-Token wird dabei nicht im Klartext geloggt. In Logs erscheint höchstens der vom Portal gelieferte Token-Prefix. Pairing-Code und Snapshot-Payload werden nicht geloggt.
