#!/usr/bin/env sh
set -eu

: "${KAIROS_RUN_ID:=binance-btc-live}"
: "${KAIROS_RUN_CONFIG:=/opt/kairos/configs/runs/binance-live.toml}"

exec python -m kairospy run live start \
  --foreground \
  --config "${KAIROS_RUN_CONFIG}" \
  --run-id "${KAIROS_RUN_ID}" \
  --confirm-live
