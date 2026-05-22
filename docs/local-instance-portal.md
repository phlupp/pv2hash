# Local instance portal payload

This document describes the local-instance additions used by the PV2Hash portal snapshot upload.

## Battery SoC validation

Battery SoC values are treated as invalid when they are lower than `0 %` or higher than `150 %`.

Invalid SoC values are normalized to `null` before they can reach the controller payload, DataLogger samples, controller events, or the portal snapshot. Values between `100 %` and `150 %` are intentionally not clamped so BMS-specific high-but-not-obviously-corrupt values remain visible for later analysis.

## DataLogger portal queue

The local DataLogger remains the source of truth for time-series samples. Portal upload uses a queued, best-effort mechanism:

1. Samples are written locally to `history_samples` and `history_miner_samples`.
2. The DataLogger schema is extended with `portal_sent_at`, `upload_attempts`, and `last_upload_error` on `history_samples`.
3. Each portal snapshot includes a bounded batch of unsent samples under `datalogger.samples`.
4. After a successful snapshot upload, the included samples are marked with `portal_sent_at`.
5. If the upload fails, the samples remain unsent and `upload_attempts` / `last_upload_error` are updated.

The snapshot contains:

```json
{
  "datalogger": {
    "schema_version": 1,
    "upload_mode": "queued",
    "sample_count": 120,
    "has_more": true,
    "samples": []
  }
}
```

This mirrors the controller event upload semantics: local data is not lost when the portal is temporarily unavailable.

## Instance location

Portal settings can store an optional local instance location:

```json
{
  "portal": {
    "location": {
      "address": "...",
      "lat": 49.123456,
      "lon": 8.123456,
      "source": "manual"
    }
  }
}
```

Portal snapshots expose this as:

```json
{
  "instance": {
    "location": {
      "address": "...",
      "lat": 49.123456,
      "lon": 8.123456,
      "has_coordinates": true,
      "source": "manual"
    }
  }
}
```

Coordinates can be edited manually. A lightweight geocoding endpoint is available at `/api/portal/location/geocode`; it uses Nominatim only when explicitly called and does not run automatically during normal offline/local operation.
