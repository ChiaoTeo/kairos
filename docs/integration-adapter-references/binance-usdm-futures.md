# Binance USDⓈ-M and COIN-M Futures execution adapter reference

## Sources inspected

- Official Binance USDⓈ-M and COIN-M Futures API documentation:
  - `POST /fapi/v1/order` and `DELETE /fapi/v1/order`;
  - `GET /fapi/v1/openOrders`, `GET /fapi/v1/allOrders`, and
    `GET /fapi/v1/order`;
  - listen-key creation/keepalive and the `ORDER_TRADE_UPDATE` user-data event.
- Upstream reference: [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader),
  commit `d89b6c84bb742e43d3451794893320defb10f067`.
- Upstream paths inspected:
  - `crates/adapters/binance/src/futures/execution.rs`;
  - `crates/adapters/binance/src/futures/http/`;
  - `crates/adapters/binance/src/futures/websocket/streams/parse_exec.rs`;
  - `crates/adapters/binance/src/futures/websocket/streams/recovery.rs`;
  - `crates/adapters/binance/test_data/futures/`.
- Upstream license: LGPL-3.0.

No upstream source was copied. Kairos independently implements the provider
protocol against Binance's official API. Nautilus was used to check failure
and recovery cases: an ambiguous submit remains reconcilable, listen-key
expiry/keepalive failure rotates the stream, and open-order recovery precedes
normal event processing. Its domain model, execution engine, cache, message
bus, task framework, Python bindings, and retry driver were deliberately not
adopted.

## Kairos mapping

| Provider fact | Kairos owner |
|---|---|
| REST endpoint, HMAC signing, API-key header | Integration Futures client |
| Principal credentials and product projection | `BinancePrincipalConnection` / `BinanceFuturesPrincipalConnection` |
| Submit/cancel delivery certainty | `BinanceFuturesOrderEntry` / `CommandOutcome` |
| Open/history/detail query | `BinanceFuturesOrderQuery` |
| Listen key, keepalive, bounded WebSocket queue | `BinanceFuturesOrderEvents` |
| `ORDER_TRADE_UPDATE` normalization | Integration external execution facts |
| Route/account/product selection and recovery target | Execution composition and Actor |
| Canonical business order state | Execution Actor |

## Implemented behavior

- Production Execution projects native async USDⓈ-M and COIN-M entry, query, and event
  capabilities; it does not construct the blocking compatibility facade.
- Futures projections reuse the principal's provider HTTP worker and shared
  egress quota lane; product-specific clients no longer bypass process or
  cross-process Binance request-weight allocation.
- Submit and cancel run once with a 10-second deadline. Transport, 5xx, 408,
  409, 425, 429, response loss, and deadline expiry are `Indeterminate`, never
  transparently retried.
- Read-only queries use the shared bounded query retry policy.
- Provider symbols come from the Reference-produced `ProviderInstrumentRef`,
  not from parsing a canonical market ID.
- The stream creates a listen key, connects on the caller Tokio runtime,
  renews the key every 30 minutes, and exposes authenticated readiness only
  after both steps succeed.
- Listen-key expiry, socket loss, keepalive failure, and queue overflow stop
  normal consumption and require route-scoped reconciliation.
- `ORDER_TRADE_UPDATE` preserves partial-fill status, execution/fill identity,
  fee, event time, binding, channel, and epoch.
- Stop and stop-limit submission is explicitly unsupported until the public
  business request carries a trigger price; it is not silently downgraded to
  MARKET/LIMIT.

## Remaining exit criteria

- Add local wire-fault tests that distinguish failure before HTTP write from
  response loss after a completed write.
- Add a local end-to-end listen-key/WebSocket recovery fixture that proves the
  Execution reconciliation barrier is released only after a successful query.
- Validate credential permissions, listen-key keepalive, submit/cancel
  uncertainty, and order reconciliation against Binance Futures testnet with
  an explicitly supplied test account.
- Validate COIN-M contract quantity and delivery-contract cases against the
  provider testnet; its business route/product remains distinct from USDⓈ-M.
