# Provider-native account and audit history

Document type: maintained integration evidence.

Last reviewed: 2026-08-18.

These read-only APIs remain concrete connection methods because no business module currently owns
a shared audit-history contract. They return typed Integration records and never expose provider
JSON. Account remains the owner of current balances and positions; Integration does not retain or
replay a second copy of business state.

## Coverage

| Provider/product | Typed history | Cursor/window behavior |
|---|---|---|
| Binance Spot | trades, commission | symbol required; `fromId`, start/end, bounded limit |
| Binance Margin | trades, commission | cross-margin endpoint; symbol and bounded time/id window |
| Binance USD-M / COIN-M | trades, commission, realized PnL, income/funding | bounded time/id window; income type filter |
| Binance Options | trades and fee aliases | symbol and bounded time/id window |
| Hyperliquid Spot / Perpetual | fills, funding, non-funding ledger | inclusive start/end; each page returns an exclusive resume timestamp |
| OKX Trading Account | fills and account bills | begin/end plus `after`/`before`, bounded to 100 rows |

Hyperliquid fill identity preserves `tid`, `oid`, optional `cloid`, transaction hash, fee token,
builder fee and liquidation evidence. Funding preserves the signed USDC amount, signed position,
rate and sample count. Non-funding ledger records preserve every documented scalar field for
deposits, withdrawals, transfers, liquidation, vault activity, spot/account-class transfers,
genesis and rewards while retaining the provider discriminator for forward compatibility.

OKX account bills are the provider's typed source for funding, fee, transfer and other balance
changes. They retain bill type/subtype rather than guessing a cross-provider business category.
Binance income records likewise retain `incomeType` and signed amount.

## Recovery rules

- History queries are read-only and may use bounded query retries.
- Inclusive time windows must resume from the returned exclusive timestamp or provider cursor;
  consumers deduplicate by provider identity.
- Hyperliquid only exposes the most recent 10,000 fills and returns at most 2,000 fills per call;
  durable consumers must poll before that provider retention window is exceeded.
- Hyperliquid WebSocket envelopes carry `Snapshot` versus `Incremental` delivery and stable fill
  identity so reconnect snapshots can be deduplicated against REST history.
- No history method mutates Account or Execution state. A downstream owner decides persistence,
  lifecycle reconciliation and replay policy.

Official references:

- <https://developers.binance.com/en/docs/catalog>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals>
- <https://www.okx.com/docs-v5/en/>
