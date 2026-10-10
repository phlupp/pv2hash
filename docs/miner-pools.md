# PV2Hash pool identities — development milestone for v0.9.0

This is the first persistence milestone for linking a **physical PV2Hash miner**
with a **PhluppPool / DATUM worker**. No pool APIs are called by the DataLogger.

## Data flow

Miner driver -> `MinerInfo.pools` -> `_build_runtime_snapshot_payload()` ->
`DataLogger` -> local `data/history.sqlite`.

Pool readback is **driver-owned**. The DataLogger does not query miners or pools;
it stores the normalized driver observations. The InfluxDB exporter will be
extended **in a separate follow-up step**; this change does not export credentials.

## Normalized pool structure

```json
{
  "slot": 0,
  "host": "stratum.example.invalid",
  "port": 23334,
  "username": "bc1q...worker01",
  "is_active": true
}
```

- `slot`: stable pool slot within the device (starting at 0)
- `host`: Stratum host/address, not the miner's management address
- `port`: Stratum TCP port or null if not known
- `username`: **complete, raw Stratum login**, never truncated or split
- `is_active`: `true`, `false`, or `null` if the API cannot tell
- `MinerInfo.pools = null`: driver does not know/has not yet read pool data;
  retain the last-known DB entries (e.g. unreachable or unsupported miner)
- `MinerInfo.pools = []`: confirmed readback with zero pools; remove DB entries
- **Never** store Stratum passwords, API passwords, tokens or configuration
  secrets in the pool model, snapshot, local pool DB tables or later exports.

There is **no assumption** that a miner is mining just because a pool entry is
active. Active *mining* is separately defined by `runtime_state == running`.

## SQLite schema v5

- `miner_pools`: up-to-date per-device pool slots, primary key
  `(miner_id, pool_slot)`, identity fields + `first_seen_at`,
  `last_seen_at`. Repeated unchanged samples only refresh
  `last_seen_at` approximately once per minute.
- `miner_pool_events`: append-only changes of observed pool slots
  (`added`, `changed`, `removed`) including the new entry's status.
  `removed` retains the former identity. Retention follows
  `datalogger.retention_days`, like other local history.
- Migration on startup is additive and does not delete existing miner samples.
- `history_miner_samples` remains the numeric/high-resolution telemetry table.

## Simulator

The simulator exposes two configurable fake Stratum pool slots. Defaults use
`.invalid` domains to prevent accidental connections. Set
`settings.active_pool_slot` to 0 or 1 to simulate failover; this **does not**
open Stratum connections.

## axeOS driver (implemented in development branch)

The existing read-only `GET /api/system/info` refresh now fills
`MinerInfo.pools` on supported firmware:

- Legacy: `stratumURL`, `stratumPort`, `stratumUser`, and optional
  `fallbackStratumURL`, `fallbackStratumPort`, `fallbackStratumUser`
  become slots 0 and 1.
- New firmware: `pools[]` is the authoritative list (array index = slot).
  `primaryPoolIndex` and `secondaryPoolIndex` identify preference slots.
- **Actual active status** is only derived when the device provides
  `isUsingFallbackStratum` and the relevant slot is present.
  `useFallbackStratum` is a *preference*, not confirmation of connection.
  Without a reliable indicator, `is_active = null`; do not assume pool 0.
- Pool addresses are normalized to their hostname / IPv6 literal and port.
  URLs containing schemes or userinfo are parsed; passwords are discarded.
- Incomplete/unavailable pool information is `None`, not an empty list;
  an API read failure does not refresh stored `last_seen_at` values.

**Read-only physical-device verification:** an existing axeOS unit exposed
both legacy primary and fallback entries (two full Stratum usernames).
That firmware did not include `isUsingFallbackStratum`; both slots were
recorded with unknown active status. A temporary SQLite database verified
two persisted entries and two added events. Device settings were not changed.
No host addresses or real Stratum usernames are committed to this repo.

## Next steps

1. Integrate with the Braiins and WhatsMiner APIs using the same model.
2. Expose normalized Pool identities to the export layer, accounting for changes
   and preserving full usernames as **values**, not high-cardinality InfluxDB
   tags. Limit public access: BTC addresses and usernames reveal identities.
3. Match with DATUM client `username_raw` and, where appropriate, pool host
   and port. Keep transient disconnects distinct from permanent config removal.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_miner_pools.py' -v
python3 -m unittest discover -s tests -p 'test_axeos_pool_readback.py' -v
```
