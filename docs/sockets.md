# PV2Hash Sockets

Sockets are the first foundation for controllable outlet/relay devices in PV2Hash.

## Phase 1 scope

The first implementation intentionally keeps sockets independent from the controller:

- sockets are stored as a separate device list in the local config
- every socket has a persistent UUID for later history/portal usage
- the runtime snapshot includes a `sockets[]` section and socket totals
- the UI has a dedicated `Sockets` page
- sockets can be monitored and switched manually
- the first driver is a local simulator socket

Automatic PV-surplus switching, miner power-socket assignment, long-off shutdown logic and real hardware drivers are planned as later phases.

## Device identity

A socket has both a readable config key and a persistent UUID:

```json
{
  "id": "s-1234abcd",
  "uuid": "0b6a8c2a-...",
  "name": "Simulator Socket",
  "driver": "simulator"
}
```

The config key is used for local URLs/actions. The UUID is intended for history, portal synchronization and long-term device correlation.

## Runtime snapshot

`GET /api/runtime/snapshot` now includes:

```json
{
  "sockets": [
    {
      "id": "<socket-uuid>",
      "key": "<local-config-key>",
      "name": "...",
      "driver": "simulator",
      "reachable": true,
      "is_on": false,
      "power_w": 0.0,
      "runtime_state": "off"
    }
  ],
  "totals": {
    "socket_power_w": 0.0,
    "reachable_socket_count": 1,
    "monitor_enabled_socket_count": 1
  }
}
```

## Simulator settings

The simulator supports:

- `initial_on`
- `reachable`
- `on_power_w`
- `standby_power_w`

These values are useful for testing the GUI and future controller integration without real hardware.
