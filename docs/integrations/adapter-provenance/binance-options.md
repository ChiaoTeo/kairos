# Binance Options execution and Account adapter provenance

## Sources inspected

- Official Binance Options REST and WebSocket Streams documentation for
  order submit/cancel/query, user-data listen-key lifecycle, and
  `ORDER_TRADE_UPDATE`.
- Binance's official generated Go connector
  [binance/binance-connector-go](https://github.com/binance/binance-connector-go),
  commit `a0c61d1ef7539023322e3b138a16cc077f9ea1d1`.
- Relevant connector paths:
  - `clients/derivativestradingoptions/src/restapi/api_trade.go`;
  - `clients/derivativestradingoptions/src/restapi/api_user_data_streams.go`;
  - `clients/derivativestradingoptions/src/websocketstreams/websocket_streams.go`;
  - `clients/derivativestradingoptions/src/websocketstreams/models/model_order_trade_update_o.go`.
- Connector license: MIT.

No connector source was copied. Kairos independently implements the protocol;
the generated connector was used to verify endpoint paths, private-stream URL
composition, and exact case-sensitive JSON field names. Its generated models,
transport runtime, reconnect machinery, and public API shape were not adopted.

## Kairos mapping and behavior

- `BinanceOptionsRestConnection` directly implements account, instrument,
  quote/trade/bar/book/Greeks, order command, and order query capabilities.
- `BinanceOptionsWebSocketConnection` owns public market subscriptions;
  `BinanceOptionsUserWebSocketConnection` owns the authenticated listen-key
  stream and directly implements the unified participant event stream.
- Submit/cancel are sent exactly once with a 10-second deadline. Ambiguous
  transport, HTTP server, or timeout outcomes are `Indeterminate`; commands
  are never transparently retried.
- Read-only queries recalibrate provider time and retry at most once only for
  explicit Binance timestamp rejection (`-1021`).
- Provider symbols come from Integration-owned `ParticipantInstrumentRef` values resolved by
  business composition against Reference facts.
- The private stream creates an `/eapi/v1/listenKey`, connects to the Options
  `/private/stream/<listenKey>` path, renews the key every 30 minutes, and
  exposes readiness only after the authenticated channel is connected.
- `ORDER_TRADE_UPDATE` preserves partial fills, trade identity, fee, provider
  timestamps, binding, channel, and epoch. Listen-key expiry, socket loss, and
  bounded queue overflow require route-scoped reconciliation.
- The provider path is async-first. Synchronous capability signatures exist only under
  `kairos_integration::blocking`; there is no hidden blocking transport or facade.
- Stop and stop-limit requests are explicitly unsupported because the current
  business request has no trigger-price field; they are not silently changed
  to market or limit orders.
- Business-module composition migration is deliberately deferred; Integration and Conflux are the
  current compilation boundary.

## Remaining exit criteria

- Add write-before/response-loss socket fault injection for submit/cancel.
- Validate permissions, listen-key lifecycle, order acknowledgement/fills,
  reconciliation, and delivery uncertainty against a real Binance Options
  account or supported test environment.

Those open items are opt-in provider smoke/fault coverage. Focused tests cover
the Account async snapshot/stream wiring and isolation from other segments.
