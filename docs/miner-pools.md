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

## Braiins OS+ and WhatsMiner API3 (implemented in development branch)

**Braiins OS+** uses the authenticated but read-only gRPC
`PoolService.GetPoolGroups` call. It provides `pool_groups[].pools[]` with
`url`, `user`, `active`, `enabled`, and `alive`. We flatten the ordered
group pools into pool slots 0..N (a changed group order can change slot
positions). In proto3 the Boolean `active=false` is omitted in JSON,
so a successful complete readback interprets its default as false.
The gRPC method is optional; errors do not interrupt ordinary status
sampling or erase last-known pool rows. No pool-setting RPCs are used.

**WhatsMiner API3** uses the read-only `get.miner.status` with
`param="pools"`, distinct from the existing `summary` query. The documented
response has `msg.pools[]`: `id` (1-based), `url`, `account` (full Stratum
login), and `stratum-active`. The latter, when explicitly present, is used
for `is_active`; a generic `status=alive` alone does not prove which pool
is actively used. Failed, malformed, or unsupported responses are unknown
(`None`), while a successfully returned empty array means no pools (`[]`).
No authenticated `set.*` command is involved.

The normalized model stores no pool passwords or embedded URL credentials.
A real WhatsMiner API3 endpoint was not reachable from the test network
during this implementation; parser, fallback and SQLite behavior were tested
with representative documented API responses.

## InfluxDB 2.x export of pool identities (implemented in dev branch)

Two new per-destination streams preserve change history and current mappings.

**`pool_state` inventory (every ~60 seconds):** reads the entire current
`miner_pools` table, writes to measurement `pv2hash_pool` using the DB's
`last_seen_at` as point timestamp. This also seeds newly enabled exporters
when old `miner_pool_events` have passed retention. The stream's
`last_success_at` is checkpointed even if the inventory is empty, to avoid
unnecessary scans; `last_seen_at` is never fabricated when a device is
unreachable. The inventory has no numeric ID cursor because it is a small
complete, periodic readback.

**`pool_events` changes:** reads `miner_pool_events` ordered by increasing
event `id`. The cursor advances *only after* InfluxDB acknowledges the batch
with HTTP 204. Failed writes are retried with existing manager backoff and
may replay safely. Measurement: `pv2hash_pool_event`; fields include
`event_id` and `event_type` (`added`, `changed`, `removed`).

Both measurements use only `instance_id`, `miner_id` and `pool_slot`
as tags. Full `username`, normalized `host`, and numeric `port` are
**fields**; they are intentionally NOT tags. The pool activity is a string
field `active_state` with precisely:

| SQLite is_active | InfluxDB active_state |
| --- | --- |
| 1 | `"active"` |
| 0 | `"inactive"` |
| NULL | `"unknown"` |

Only `event_type="removed"` means a configured pool disappeared; do not
confuse `"inactive"` with `"removed"` or `"unknown"`. To determine
**current** pool presence, combine latest inventory with *subsequent*
change events. Never infer active connection from a preferred/primary slot.
Old values can remain available as historical points; an offline device does
not update `last_seen_at`.

**Integration verified on `testvm01`**: existing four device/slot
identities were exported from SQLite to the isolated `pv2hash-dev` InfluxDB
bucket. Grafana Flux reads returned 4 `active_state`, 4 `host`, 4 `port`
and 4 `username` fields. Two AxeOS entries were `"unknown"`; the two
simulator entries were `"active"` and `"inactive"`. The 4 latest
change-event series also returned event type, event ID and status. Both
new stream cursors advanced with no errors, and all 36 regression tests
passed. Existing miner telemetry continued to export with HTTP 204.
Production nodes and devices were not modified.

**Privacy:** complete Stratum logins may include BTC payout addresses.
Keep both measurements in access-controlled InfluxDB buckets; do not include
the login, host or raw API credentials in public dashboard panels. No
Stratum passwords or API passwords are stored/exported.

## Next steps

1. Validate the Braiins gRPC and WhatsMiner API3 readbacks against physical
   devices when each is reachable; account for any firmware-specific nuances.
2. Match the local observed Stratum username with DATUM's
   `username_raw`; when possible, corroborate with source IP / pool host.
   Keep transient disconnects distinct from permanent configuration removal.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_miner_pools.py' -v
python3 -m unittest discover -s tests -p 'test_axeos_pool_readback.py' -v
python3 -m unittest discover -s tests -p 'test_braiins_whatsminer_pools.py' -v
python3 -m unittest discover -s tests -p 'test_pool_exporter.py' -v
```
