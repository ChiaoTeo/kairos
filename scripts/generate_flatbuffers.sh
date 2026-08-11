#!/usr/bin/env bash
set -euo pipefail

if ! command -v flatc >/dev/null 2>&1; then
  echo "flatc is required; install FlatBuffers compiler first" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
schema_root="$repo_root/schemas"
python_out="$repo_root/kairospy/infrastructure/transport/generated"
rust_out="$repo_root/crates/kairos-protocol/src/generated"
temp_root="$(mktemp -d)"
trap 'rmdir "$temp_root" 2>/dev/null || true' EXIT
python_stage="$temp_root/python"
rust_stage="$temp_root/rust"

mkdir -p "$python_out" "$rust_out" "$python_stage" "$rust_stage"

# Generate outside the checked-in trees.  Removing the live generated
# directories before running flatc leaves the repository uncompilable while
# generation is in progress, which is especially problematic when a build and
# a schema refresh run concurrently.  We copy the completed files into place
# only after every schema has generated successfully.

flatc -I "$schema_root" --python -o "$python_stage" \
  "$schema_root/common/v1/decimal.fbs" \
  "$schema_root/common/v1/types.fbs" \
  "$schema_root/common/v1/header.fbs" \
  "$schema_root/common/v1/snapshot_header.fbs" \
  "$schema_root/reference/v1/entities.fbs" \
  "$schema_root/reference/v1/change.fbs" \
  "$schema_root/market/v1/quote.fbs" \
  "$schema_root/market/v1/trade.fbs" \
  "$schema_root/market/v1/data.fbs" \
  "$schema_root/market/v1/bar_message.fbs" \
  "$schema_root/market/v1/greeks_message.fbs" \
  "$schema_root/market/v1/rate_message.fbs" \
  "$schema_root/market/v1/ticker_24h_message.fbs" \
  "$schema_root/market/v1/mark_price_message.fbs" \
  "$schema_root/market/v1/index_price_message.fbs" \
  "$schema_root/market/v1/funding_rate_message.fbs" \
  "$schema_root/market/v1/open_interest_message.fbs" \
  "$schema_root/market/v1/instrument_status_message.fbs" \
  "$schema_root/market/v1/orderbook_message.fbs" \
  "$schema_root/account/v1/types.fbs" \
  "$schema_root/execution/v1/types.fbs" \
  "$schema_root/execution/v1/order_intent.fbs" \
  "$schema_root/execution/v1/order_filled.fbs" \
  "$schema_root/risk/v1/types.fbs" \
  "$schema_root/risk/v1/authorize.fbs" \
  "$schema_root/risk/v1/decision.fbs" \
  "$schema_root/risk/v1/assess.fbs" \
  "$schema_root/risk/v1/reserve.fbs" \
  "$schema_root/risk/v1/release.fbs" \
  "$schema_root/risk/v1/consume.fbs" \
  "$schema_root/risk/v1/assessment_result.fbs" \
  "$schema_root/risk/v1/reservation_event.fbs" \
  "$schema_root/intent/v1/types.fbs" \
  "$schema_root/system/v1/types.fbs" \
  "$schema_root/projection/reference/v1/catalog.fbs" \
  "$schema_root/projection/reference/v1/markets.fbs" \
  "$schema_root/projection/reference/v1/collections.fbs" \
  "$schema_root/projection/market/v1/current.fbs" \
  "$schema_root/projection/market/v1/orderbook.fbs" \
  "$schema_root/projection/market/v1/history.fbs" \
  "$schema_root/projection/market/v1/subscriptions.fbs" \
  "$schema_root/projection/account/v1/current.fbs" \
  "$schema_root/projection/account/v1/equity.fbs" \
  "$schema_root/projection/execution/v1/orders.fbs" \
  "$schema_root/projection/execution/v1/fills.fbs" \
  "$schema_root/projection/intent/v1/journal.fbs" \
  "$schema_root/projection/risk/v1/budgets.fbs" \
  "$schema_root/projection/system/v1/health.fbs" \
  "$schema_root/projection/system/v1/operations.fbs" \
  "$schema_root/projection/system/v1/alerts.fbs" \
  "$schema_root/projection/system/v1/freshness.fbs"

for schema in \
  "$schema_root/common/v1/decimal.fbs" \
  "$schema_root/common/v1/types.fbs" \
  "$schema_root/common/v1/header.fbs" \
  "$schema_root/common/v1/snapshot_header.fbs" \
  "$schema_root/reference/v1/entities.fbs" \
  "$schema_root/reference/v1/change.fbs" \
  "$schema_root/market/v1/quote.fbs" \
  "$schema_root/market/v1/trade.fbs" \
  "$schema_root/market/v1/data.fbs" \
  "$schema_root/market/v1/bar_message.fbs" \
  "$schema_root/market/v1/greeks_message.fbs" \
  "$schema_root/market/v1/rate_message.fbs" \
  "$schema_root/market/v1/ticker_24h_message.fbs" \
  "$schema_root/market/v1/mark_price_message.fbs" \
  "$schema_root/market/v1/index_price_message.fbs" \
  "$schema_root/market/v1/funding_rate_message.fbs" \
  "$schema_root/market/v1/open_interest_message.fbs" \
  "$schema_root/market/v1/instrument_status_message.fbs" \
  "$schema_root/market/v1/orderbook_message.fbs" \
  "$schema_root/account/v1/types.fbs" \
  "$schema_root/execution/v1/types.fbs" \
  "$schema_root/execution/v1/order_intent.fbs" \
  "$schema_root/execution/v1/order_filled.fbs" \
  "$schema_root/risk/v1/types.fbs" \
  "$schema_root/risk/v1/authorize.fbs" \
  "$schema_root/risk/v1/decision.fbs" \
  "$schema_root/risk/v1/assess.fbs" \
  "$schema_root/risk/v1/reserve.fbs" \
  "$schema_root/risk/v1/release.fbs" \
  "$schema_root/risk/v1/consume.fbs" \
  "$schema_root/risk/v1/assessment_result.fbs" \
  "$schema_root/risk/v1/reservation_event.fbs" \
  "$schema_root/intent/v1/types.fbs" \
  "$schema_root/system/v1/types.fbs" \
  "$schema_root/projection/reference/v1/catalog.fbs" \
  "$schema_root/projection/reference/v1/markets.fbs" \
  "$schema_root/projection/reference/v1/collections.fbs" \
  "$schema_root/projection/market/v1/current.fbs" \
  "$schema_root/projection/market/v1/orderbook.fbs" \
  "$schema_root/projection/market/v1/history.fbs" \
  "$schema_root/projection/market/v1/subscriptions.fbs" \
  "$schema_root/projection/account/v1/current.fbs" \
  "$schema_root/projection/account/v1/equity.fbs" \
  "$schema_root/projection/execution/v1/orders.fbs" \
  "$schema_root/projection/execution/v1/fills.fbs" \
  "$schema_root/projection/intent/v1/journal.fbs" \
  "$schema_root/projection/risk/v1/budgets.fbs" \
  "$schema_root/projection/system/v1/health.fbs" \
  "$schema_root/projection/system/v1/operations.fbs" \
  "$schema_root/projection/system/v1/alerts.fbs" \
  "$schema_root/projection/system/v1/freshness.fbs"; do
  output="$temp_root/$(basename "$schema" .fbs)"
  mkdir -p "$output"
  flatc -I "$schema_root" --rust --rust-module-root-file -o "$output" "$schema"
  while IFS= read -r generated; do
    relative="${generated#"$output/"}"
    destination="$rust_stage/$(dirname "$relative")"
    mkdir -p "$destination"
    cp "$generated" "$destination/"
  done < <(find "$output" -type f -name '*.rs' -not -name mod.rs)
done

# Keep generated Rust deterministic and compatible with the repository's
# format gate. Flatc intentionally emits compact Rust which is valid but does
# not match rustfmt's stable output.
find "$rust_stage" -type f -name '*.rs' -print0 \
  | xargs -0 rustfmt --edition 2021

mkdir -p "$python_out/kairos" "$rust_out/kairos"
cp -R "$python_stage/kairos/." "$python_out/kairos/"
cp -R "$rust_stage/kairos/." "$rust_out/kairos/"
