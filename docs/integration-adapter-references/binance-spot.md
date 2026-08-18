# Binance Spot adapter reference

## Sources inspected

- Official Binance Spot REST, WebSocket API, market-stream, and user-data documentation.
- Upstream reference: [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader),
  commit `d89b6c84bb742e43d3451794893320defb10f067`, adapter path
  `crates/adapters/binance/`.
- Upstream license: LGPL-3.0.

No upstream source was copied. Kairos independently implements the provider protocol. Nautilus was
used only to recover provider behavior and failure/recovery cases; its domain model, cache, engine,
event bus, runtime, and Python bindings were deliberately not adopted.

## Current Kairos topology

```text
BinanceSpotRestConnection
  -> InstrumentCatalogQuery
  -> MarketQuoteQuery + MarketTradeQuery + MarketBarQuery + MarketOrderBookQuery
  -> AccountQuery
  -> OrderCommand + OrderQuery

BinanceSpotWebSocketConnection
  -> ConnectionLifecycleCommand + ConnectionHealthQuery
  -> MarketSubscriptionCommand + MarketDataStream

BinanceSpotWebSocketApiConnection
  -> ConnectionLifecycleCommand + ConnectionHealthQuery
  -> OrderCommand + AccountQuery + OrderQuery

BinanceSpotUserWebSocketConnection
  -> ConnectionLifecycleCommand + ConnectionHealthQuery
  -> ParticipantEventStream
  -> AccountStream + ExecutionStream
```

Each concrete connection owns one real REST pool, socket, or request/response WebSocket session.
There is no provider facade, principal connection, capability wrapper, registry, blocking transport,
or type-erased connection. Multiple instances are created as named Conflux resources.

## Protocol and failure semantics

- Public market subscriptions use one socket and Binance `SUBSCRIBE`/`UNSUBSCRIBE` requests.
  Frames arriving before the matching acknowledgement are normalized and retained in a bounded
  queue.
- Subscription results are `Confirmed`, explicit provider `Rejected`, or `Indeterminate` after
  an ambiguous post-send failure. Reconnect restores the desired subscription set.
- One Spot user-data stream carries balance/account and `executionReport` order/fill events.
  The connection demultiplexes both into one closed `ExternalParticipantEvent` stream; domain-
  specific stream traits remain available to callers that consume only one domain.
- Listen keys are created by REST and renewed every 30 minutes. Reconnect creates a new listen key
  and channel epoch.
- Submit/cancel requests are sent exactly once. Transport, server, timeout, or response-decoding
  failures after the command may have been sent are `Indeterminate`; explicit provider rejection
  remains `Rejected`.
- Read-only queries may use the declared bounded query retry policy.
- Provider payloads are normalized to Integration-owned domain objects before leaving the adapter.
- Business route selection, canonical market identity, reconciliation, and authoritative order or
  account state remain business composition/Actor responsibilities.

## Official capability inventory

The official catalog includes public/reference queries, depth/trades/klines/tickers, market streams,
account and commission queries, open/all order queries, submit/cancel/amend/order-list/SOR commands,
request/response WebSocket API, and user-data streams. The first framework slice implements the
common catalog/market/account/order loop. Binance-only operations are added as concrete inherent
methods only when they have a current caller; endpoint inventory alone does not justify another
capability trait.

Official sources:

- <https://developers.binance.com/en/docs/catalog>
- <https://developers.binance.com/en/docs/products/spot/rest-api>
- <https://developers.binance.com/en/docs/products/spot/websocket-api>
- <https://developers.binance.com/en/docs/products/spot/websocket-streams>
- <https://developers.binance.com/en/docs/products/spot/user-data-stream>

## Remaining exit criteria

- Add provider-frame fixtures for interleaved event/ack, rejection, reconnect, and bounded overflow.
- Add write-before/response-loss fault injection for REST and WebSocket commands.
- Validate permission, listen-key, and delivery-uncertainty behavior against an explicitly supplied
  Binance test account.
- Migrate business modules separately after the Integration/Conflux boundary stabilizes.
