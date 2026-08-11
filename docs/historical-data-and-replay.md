# Historical market data and replay

## Storage contract

Historical downloads have two representations:

- JSONL is the provider/debug interchange format and is useful for small
  fixtures.
- Parquet is the workspace dataset format. It is written with ZSTD compression,
  keeps event identity and event time as columns, and preserves the complete
  normalized observation in `payload_json` so all Market observation variants
  can share one stable file contract.

The dataset catalog is JSON metadata only. It contains the dataset name, path,
format, event count, and time bounds; it is not the event store.

## Download

The canonical Rust command downloads normalized bars from either provider:

```text
kairos-market-cli download \
  --provider binance|massive \
  --symbol BTCUSDT \
  --start 1700000000000 --end 1700086400000 \
  --interval 1m --file data/events.jsonl
```

Massive requires `--api-key`. Both adapters paginate provider responses and
map them into Market-owned `Bar` observations. Passing `--workspace` also
registers the JSONL file and its manifest in `state/market/datasets.json`.

The Python surface adds the production storage conversion:

```text
kairospy market data download \
  --provider binance --symbol BTCUSDT \
  --start 1700000000000 --end 1700086400000 \
  --file data/events.jsonl --storage-format parquet
```

This invokes the canonical Rust downloader, converts the normalized output to
Parquet, and registers the resulting workspace dataset.

## Replay

Replay accepts JSONL or a dataset's Parquet path. The current Rust replay feed
is an in-memory deterministic feed, so launch materializes Parquet to an
instance-local JSONL file before starting the Market process. This keeps the
application boundary stable while allowing the storage format to evolve; a
future streaming reader can replace only this materialization step.

Manifests are checked before replay. A mismatch between declared and actual
event count is rejected, preventing a truncated download from silently being
used in a backtest.
