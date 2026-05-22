# Assigned socket miner workflow

This document describes the safe switching workflow for sockets assigned to miners.

## Scope

The controller decision logic remains unchanged. The workflow only wraps the actual miner profile application step for miners whose assigned socket is configured with:

```json
{
  "socket": {
    "mode": "switching"
  }
}
```

Miners in `measure_only` mode are not switched.

## Start workflow

When the controller requests a profile other than `off`:

1. The assigned socket is checked.
2. If the socket is unavailable or unreachable, the miner profile is not applied.
3. If the socket is off, PV2Hash switches it on.
4. PV2Hash waits `startup_delay_seconds` before applying the miner profile.
5. Once the delay has elapsed, the requested miner profile is applied.

During startup delay the miner receives a temporary runtime state of `waiting_for_socket`. This is a protective state and does not modify the controller decision itself.

## Stop workflow

When the controller requests profile `off`:

1. PV2Hash applies `off` to the miner first.
2. The socket off timer starts if it is not already running.
3. If the miner remains in `off` for `power_off_after_off_seconds`, the socket is switched off.
4. If the controller later requests a non-`off` profile, the off timer is reset.

The minimum socket-off delay is 600 seconds. The default is 3600 seconds.

## Repeated off calls

After the socket has been switched off by the workflow, PV2Hash does not keep calling the miner API with profile `off` every control cycle. The miner is treated as intentionally powered off until the controller requests a non-`off` profile again.

## Runtime snapshot

The miner `socket.workflow` payload exposes the current state:

```json
{
  "socket": {
    "workflow": {
      "state": "off_timer",
      "message": "Socket-Off in 42 min 00 s.",
      "startup_remaining_seconds": null,
      "startup_remaining_text": "",
      "off_since": "2026-05-23T...+00:00",
      "power_off_due_at": "2026-05-23T...+00:00",
      "power_off_remaining_seconds": 2520,
      "power_off_remaining_text": "42 min 00 s",
      "last_socket_action": "off",
      "last_socket_action_at": "2026-05-23T...+00:00",
      "last_error": ""
    }
  }
}
```

Known states include:

- `idle`
- `startup_delay`
- `running`
- `off_timer`
- `socket_off`
- `socket_unavailable`
- `socket_unreachable`
- `socket_error`
- `socket_on_failed`
- `socket_off_failed`

## Safety notes

- The socket is never switched off immediately when the miner is sent to `off`.
- Socket-off only happens after a stable `off` period.
- A new non-`off` request resets the off timer before starting the socket-on workflow.
- If the socket cannot be reached, PV2Hash avoids starting the miner and exposes the error state.
