# Binance Margin execution adapter reference

## Sources inspected

- Official Binance Margin API documentation for margin order submit/cancel,
  order query, cross-margin user-data stream, isolated-margin user-data stream,
  listen-key keepalive, and `executionReport`.
- Upstream reference inventory:
  [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader),
  commit `d89b6c84bb742e43d3451794893320defb10f067`,
  `docs/integrations/binance.md` and `crates/adapters/binance/`.
- Upstream license: LGPL-3.0.

The inspected Nautilus adapter explicitly does not implement Binance Margin,
so no source or behavior was copied from it for this slice. Kairos implements
the documented provider protocol independently. Nautilus's domain model,
cache, execution engine, event bus, and runtime are not used.

## Kairos mapping and behavior

- `BinanceSpotPrincipalConnection` owns the shared credential, clock, HTTP lane,
  and quota context.
- `BinanceMarginPrincipalConnection` projects either Cross or Isolated Margin;
  the connection domain remains provider-native and distinct.
- `BinanceMarginOrderEntry` sends submit/cancel once with a 10-second deadline;
  ambiguous transport/server/deadline failure is `Indeterminate`.
- `BinanceMarginOrderQuery` exposes open/history/detail queries. History
  requires a provider symbol, matching the provider endpoint.
- `BinanceMarginOrderEvents` creates and renews the product-specific listen
  key on the caller Tokio runtime and normalizes `executionReport` into a
  binding/epoch-stamped external event.
- Isolated Margin requires an explicit `isolated_symbol` in Execution route
  configuration because Binance scopes its account and listen key per symbol.
  Entry rejects a different `ProviderInstrumentRef`; query injects the route
  symbol when absent and rejects a different one. Cross Margin rejects that
  field.
- Socket loss, credential rotation, listen-key keepalive failure, and bounded
  queue overflow require route-scoped reconciliation before reconnect.
- Provider symbols enter commands through Reference-owned
  `ProviderInstrumentRef`; Integration does not parse canonical market IDs.
- Production and direct CLI paths are async-only for both margin products;
  the old public blocking margin order-entry path was removed.

## Remaining exit criteria

- Add explicit response-loss fault injection for margin submit/cancel.
- Exercise Cross and Isolated listen-key creation/keepalive and reconciliation
  against a Binance test account with Margin enabled.
- Verify isolated-symbol permissions and account activation failures are
  classified as proven provider rejection rather than ambiguous delivery.
