# PV2Hash Identity Foundation

PV2Hash uses stable identifiers as a foundation for local history, future portal integration and future device types such as sockets.

## Instance identity

Each PV2Hash installation has one stable instance UUID. It is created automatically on first start and stored outside the normal configuration:

```text
/var/lib/pv2hash/data/instance.json
```

When running from the project directory during development, the relative path is:

```text
data/instance.json
```

Example:

```json
{
  "id": "6a5f0b84-3c81-4d3a-9c93-91a7d7a5e1c4",
  "created_at": "2026-04-29T12:34:00Z"
}
```

The instance UUID is intentionally stored outside `config.json` so configuration imports or exports do not accidentally clone or overwrite the identity of a running installation.

The human readable instance name remains in the normal configuration under `system.instance_name` and can be changed in the GUI.

## Device UUIDs

Miners, the grid source and the battery source receive stable UUID fields in the normal configuration. These UUIDs are generated automatically when missing and then persisted.

Example miner:

```json
{
  "id": "m31s_garage",
  "uuid": "c76d6d73-2f96-4c82-9ec6-c9bb06f0838b",
  "name": "M31S+ Garage",
  "driver": "whatsminer_api3",
  "host": "192.168.11.88"
}
```

`id` remains the local, readable config key used by the current runtime, URLs and GUI actions. `uuid` is the long-term identity used for history, future portal sync and device correlation.

This means a device can be renamed or moved to another IP address without losing its historical identity.

## Runtime snapshot

PV2Hash exposes a first central runtime snapshot at:

```text
GET /api/runtime/snapshot
```

The snapshot includes:

```text
- instance identity and version
- controller summary
- grid source state
- battery state
- miners with local keys and stable UUIDs
- aggregate totals
```

This endpoint is intended as a future foundation for:

```text
- local history logging
- pv2hash.net portal sync
- dashboards using a common status model
- later socket devices
```
