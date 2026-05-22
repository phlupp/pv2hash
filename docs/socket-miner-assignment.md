# Socket assignment for miners

Sockets can be assigned to miners as an optional power supply / measurement device. The miner configuration is the leading configuration; the socket stores mirrored assignment metadata so the sockets page can show that a socket is reserved for a miner.

## Scope

This step only adds assignment, validation, UI display, and snapshot metadata. It intentionally does not add automatic controller switching yet.

## Miner configuration

```json
{
  "socket": {
    "enabled": true,
    "socket_id": "s-12345678",
    "mode": "measure_only",
    "startup_delay_seconds": 60,
    "power_off_after_off_seconds": 3600
  }
}
```

Modes:

- `measure_only`: PV2Hash reads socket state / power, but does not switch the socket.
- `switching`: PV2Hash may use the socket for a later safe start/stop workflow. This PR only stores the intent.

Defaults:

- `startup_delay_seconds`: 60
- `power_off_after_off_seconds`: 3600
- minimum `power_off_after_off_seconds`: 600

## Socket configuration

When a miner references a socket, the socket receives mirrored metadata:

```json
{
  "assignment": {
    "role": "miner",
    "target_id": "m-12345678"
  },
  "control_enabled": false
}
```

A socket assigned to a miner is not available for future automatic consumer control.

## Validation rules

- Miners can only select real socket drivers. Currently this means `tasmota_http`.
- Simulator sockets are never selectable for miner power supply.
- A socket can only be assigned to one miner.
- A socket with active automation / consumer control cannot be assigned to a miner.
- The currently assigned socket remains visible for the owning miner so the assignment can be reviewed or removed.

## Snapshot payload

Runtime snapshots include the miner socket assignment:

```json
{
  "miners": [
    {
      "key": "m-12345678",
      "socket": {
        "enabled": true,
        "socket_id": "s-12345678",
        "mode": "measure_only",
        "startup_delay_seconds": 60,
        "power_off_after_off_seconds": 3600,
        "socket_name": "Miner Socket",
        "socket_driver": "tasmota_http",
        "socket_host": "192.168.1.50",
        "runtime": {},
        "missing": false
      }
    }
  ]
}
```

Sockets include assignment metadata:

```json
{
  "sockets": [
    {
      "key": "s-12345678",
      "assignment": {
        "role": "miner",
        "target_id": "m-12345678",
        "target_name": "Miner 1",
        "label": "Miner: Miner 1",
        "class": "ok"
      }
    }
  ]
}
```

## Next step

The follow-up step should add the safe switching workflow:

1. If a miner should leave `off` and its assigned socket is off, switch the socket on.
2. Wait `startup_delay_seconds`.
3. Start / resume / profile the miner.
4. When the miner remains in profile `off` for `power_off_after_off_seconds`, switch the socket off.
5. Reset timers whenever the miner leaves `off` again.
