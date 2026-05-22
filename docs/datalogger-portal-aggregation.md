# DataLogger portal aggregation

The local DataLogger stores high-resolution samples locally, typically every 10 seconds. The portal does not need that raw resolution for overview/history pages, so local portal upload aggregates DataLogger samples before sending them.

## Behavior

- Raw DataLogger samples remain stored locally in `history_samples` and `history_miner_samples`.
- Portal upload groups unsent raw samples into 1-minute buckets.
- Only fully completed minute buckets are uploaded. The currently active minute remains local until it is complete.
- Numeric values are averaged per bucket.
- Boolean values use a majority value per bucket.
- Text/status values use the latest non-empty value in the bucket.
- Miner detail rows are aggregated per miner within the same minute bucket.

## Upload payload

Aggregated samples are sent under the existing `datalogger.samples` key:

```json
{
  "datalogger": {
    "schema_version": 1,
    "upload_mode": "queued",
    "sample_count": 120,
    "has_more": false,
    "samples": [
      {
        "sample_id": "2026-05-23T10:15:00+00:00/2026-05-23T10:16:00+00:00",
        "sample_ids": ["2026-05-23T10:15:00+00:00", "2026-05-23T10:15:10+00:00"],
        "ts": "2026-05-23T10:15:00+00:00",
        "bucket_start": "2026-05-23T10:15:00+00:00",
        "bucket_end": "2026-05-23T10:16:00+00:00",
        "aggregation_seconds": 60,
        "aggregation": "avg_1m",
        "raw_sample_count": 6
      }
    ]
  }
}
```

`sample_id` identifies the aggregated minute bucket. `sample_ids` contains the raw local samples covered by that bucket.

## Marking samples as uploaded

After a successful portal snapshot upload, the raw samples listed in `sample_ids` are marked with `portal_sent_at`. If upload fails, the same raw samples remain unsent and `upload_attempts` / `last_upload_error` are updated.

This preserves reliable catch-up semantics while reducing the portal upload volume by roughly the DataLogger interval factor. For a 10-second local interval, this reduces portal DataLogger samples by about 6:1.
