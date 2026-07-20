#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-example-output/four-product-user-path}"
PYTHON="${PYTHON:-./pyenv/bin/python}"

mkdir -p "$ROOT/external-input"

cat > "$ROOT/external-input/sentiment.contract.json" <<'JSON'
{
  "dataset_id": "reference.sentiment.equity.us",
  "primary_time": "available_time",
  "grain": {"kind": "event_stream"},
  "fields": ["available_time", "instrument_id", "sentiment"],
  "freshness": {"max_age_seconds": 60}
}
JSON

cat > "$ROOT/external-input/sentiment.csv" <<'CSV'
available_time,instrument_id,sentiment
2026-01-01T00:00:00Z,equity:US:AAPL,0.4
CSV

cat > "$ROOT/external-input/sentiment_live.py" <<'PY'
def subscribe(params, context):
    yield {
        "available_time": "2026-01-01T00:00:00Z",
        "instrument_id": "equity:US:AAPL",
        "sentiment": 0.4,
    }
PY

cat > "$ROOT/external-input/momentum_factor.py" <<'PY'
def compute(data):
    return data["bars"]
PY

cat > "$ROOT/external-input/risk.json" <<'JSON'
{
  "max_gross_exposure": 1.0
}
JSON

"$PYTHON" -m kairospy --format json --lake-root "$ROOT" data download tutorial-sma-data
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" data write \
  --file "$ROOT/external-input/sentiment.csv" \
  --as reference.sentiment.equity.us \
  --contract "$ROOT/external-input/sentiment.contract.json"
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" data write \
  --live \
  --connector "$ROOT/external-input/sentiment_live.py" \
  --as reference.sentiment.equity.us \
  --contract "$ROOT/external-input/sentiment.contract.json"

"$PYTHON" -m kairospy --format json --lake-root "$ROOT" study open momentum-study \
  --hypothesis "momentum persists"
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" study add-data \
  --workspace momentum-study \
  --name bars \
  --dataset market.ohlcv.crypto.tutorial.btc-usdt.1h
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" study add-data \
  --workspace momentum-study \
  --name sentiment \
  --dataset reference.sentiment.equity.us
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" study add-factor \
  --workspace momentum-study \
  --name momentum_12_1 \
  --file "$ROOT/external-input/momentum_factor.py"
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" run start \
  --study momentum-study \
  --mode study
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" study freeze momentum-study --version 1.0.0

"$PYTHON" -m kairospy --format json --lake-root "$ROOT" strategy open momentum-long-only \
  --from-study momentum-study@1.0.0
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" strategy bind-factor \
  --workspace momentum-long-only \
  --name primary \
  --study-factor momentum_12_1
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" strategy set-risk \
  momentum-long-only \
  "$ROOT/external-input/risk.json"
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" strategy freeze momentum-long-only --version 1.0.0
"$PYTHON" -m kairospy --format json --lake-root "$ROOT" run start \
  --snapshot momentum-long-only@1.0.0 \
  --mode backtest
