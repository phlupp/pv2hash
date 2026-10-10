# InfluxDB 2.x Telemetry (PV2Hash)

The InfluxDB exporter transmits local DataLogger samples without changing the miner controller or local database schema.

## Measurements

- `pv2hash_system`: numeric DataLogger system fields, tagged with `instance_id` and `instance_name`
- `pv2hash_miner`: numeric DataLogger miner fields, tagged with `instance_id`, `instance_name`, `miner_id`, and `miner_name`
- `pv2hash_controller_event`: controller event numeric fields, tagged with stable miner and reason identifiers

## Portal-compatible mining metrics

Active mining is defined **exactly as in the retired PV2Hash Portal**: the miner's recorded `runtime_state` equals `running`, case-insensitively, after trimming whitespace. Reachability, control permission, positive hashrate, and power are not enough to infer active mining. Device hashrate values can remain positive while mining is paused.

Added `pv2hash_system` fields for each sample:

| Field | Unit | Meaning |
| --- | --- | --- |
| `registered_miner_count` | count | Number of miners recorded at this timestamp |
| `running_miner_count` | count | Number with runtime_state=running |
| `running_miner_power_w` | W | Sum of power_w for running miners only |
| `running_hashrate_ghs` | GH/s | Sum of hashrate_ghs for running miners only |
| `running_hashrate_ths` | TH/s | Same sum divided by 1000 |

Added `pv2hash_miner` field `mining_active` (integer 0/1), plus allowlisted string fields `runtime_state`, `profile`, and `driver`. Existing fields remain unchanged.

Additional allowlisted text fields in `pv2hash_system`: `source_quality` and `battery_quality`. In `pv2hash_controller_event`: `event_type`, `old_profile`, `requested_profile`, `new_profile`, `policy_mode`, and `distribution_mode`. Arbitrary free text (messages, credentials, decisions, host addresses) is **not** exported.

## Aggregation and freshness

When combining multiple PV2Hash nodes, select each node's latest sample inside a bounded freshness window before summing. Never sum all samples inside that window.

For historical charts, aggregate values into time windows, then sum over nodes or miners per window; do not sum data repeatedly without grouping by time. Public dashboards should not expose instance or miner identity labels.

The newly added fields begin at the first sample exported with the updated version. Historical data already written by earlier exporters does not acquire these fields automatically.

## Verification

Run:

```bash
python3 -m unittest discover -s tests -p 'test_exporter_portal_metrics.py' -v
```

Before publishing, validate the new fields with a dedicated InfluxDB test bucket, test rollback, and release using the existing GitHub Actions release workflow. Only deploy after the new release is published and its checksums verified.
