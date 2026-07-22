from __future__ import annotations


OHLCV_PRIMARY_KEY = ("venue", "instrument_id", "period_start", "interval")
OHLCV_ORDER_BY = ("period_start", "instrument_id")
OHLCV_TIME_PARTITIONING = ("event_year", "event_month")
OHLCV_BUCKET_PARTITIONING = ("event_year", "event_month", "instrument_bucket")

OHLCV_INCREMENTAL_CONTRACT = {
    "watermark_field": "period_start",
    "complete_until_field": "latest_complete_period_end",
    "overlap": "1_period",
    "merge_key": OHLCV_PRIMARY_KEY,
    "correction_policy": "replace_by_merge_key",
}

OPTION_OHLCV_INSTRUMENT_BUCKETS = 64
