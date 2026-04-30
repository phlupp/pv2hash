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

## Socket quality

Sockets use the same quality vocabulary as sources and batteries:

- `live`: the device answered and the current state is usable
- `offline`: the last poll failed after at least one previous successful value or a concrete connection attempt failed
- `no_data`: no usable status has been read yet
- `stale`: reserved for future drivers that keep a last value but can detect outdated data

The runtime snapshot and `/api/sockets/status` expose `quality` for every socket. Future automation should only make safety-relevant decisions when the socket quality is `live`.

## Tasmota HTTP driver

The `tasmota_http` socket driver talks to local Tasmota devices through the HTTP command endpoint `/cm?cmnd=...`.

Core values used by PV2Hash:

- reachable / quality
- on/off state from `Power` or `PowerN`
- optional measured power from `Status 8` / `ENERGY.Power`
- last seen timestamp and last error

The driver also reads dynamic detail values when available. These are display-only and depend on the concrete Tasmota device/build:

- `DeviceName` and `FriendlyName`
- firmware/hardware
- uptime
- WLAN RSSI/signal
- voltage/current/energy counters
- ESP temperature or generic temperature values

`DeviceName` is preferred as the discovered PV2Hash socket name. If it is missing, `FriendlyName` or the IP address is used.

Supported manual actions:

- switch on
- switch off
- reboot device (`Restart 1`)

## Tasmota discovery

The socket page provides a manual Tasmota discovery action. It scans the first active local IPv4 network and probes devices through the Tasmota HTTP API. If nothing is found, Host/IP can still be entered manually.

The scan is intentionally manual and bounded so PV2Hash does not continuously scan the LAN.

## Future driver ideas

- Shelly HTTP
- Home Assistant bridge
- Matter, much later, likely through an existing local controller/bridge instead of direct Matter commissioning inside PV2Hash
