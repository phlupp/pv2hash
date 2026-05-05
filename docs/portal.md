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

PV2Hash currently sends the instance identity and compact totals:

```json
{
  "instance": {
    "uuid": "<local instance UUID>",
    "name": "PV2Hash Node",
    "version": "0.6.x",
    "version_full": "0.6.x",
    "hostname": "testvm02",
    "status": "online"
  },
  "totals": {
    "miner_power_w": 1234,
    "hashrate_ths": 98.5,
    "grid_power_w": -350,
    "battery_soc": 82
  }
}
```

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
