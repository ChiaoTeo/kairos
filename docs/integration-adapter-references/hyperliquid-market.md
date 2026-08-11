# Hyperliquid Market Adapter Reference

## Source and license

- Provider specification: [Hyperliquid official Info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).
- Endpoint used: read-only `POST /info` request with `{"type":"allMids"}`.
- Retrieved: 2026-08-11.
- Upstream behavior reference: NautilusTrader `develop` commit
  `718f0b704aa4144c3237eb32fb88be7e15cef3b7`, especially
  `docs/integrations/hyperliquid.md`, `crates/adapters/hyperliquid/src/data.rs`, and its WebSocket
  client/models. License: LGPL-3.0-or-later.
- No third-party adapter source code was copied or translated; only provider behavior and test
  cases were cross-checked.

## Kairos mapping

- Integration owns `HyperliquidMarketSnapshot` and the provider request/response contract.
- `allMids` object keys remain provider symbols.
- Decimal string values become Integration `MarketEventKind::Snapshot` facts.
- The native public WebSocket subscribes independently to `trades` and `l2Book` for each active
  symbol. Every `l2Book` push is mapped as an authoritative `BookSnapshot`; venue `time` is used
  for event time and snapshot sequence identity. Trades retain venue `tid`.
- Market composition creates independent Spot and Perpetual source descriptors.
- The Market snapshot Source Driver owns cadence, active symbol selection, backpressure, and
  conversion to canonical `MarketObservation` values.

## Deliberately not copied

- Hyperliquid SDK domain models, caches, event buses, engines, signing, trading and account code.
- UI symbol remapping rules.
- Nautilus caches, message bus, engine runtime, Python bindings, client factories, generalized
  reconnect framework, and order-book domain model.

## Evidence

- Integration unit test verifies the `allMids` request and decimal normalization.
- Focused live normalizer test verifies both L2 sides, snapshot semantics, trade values and `tid`.
- Market controlled-disconnect test verifies bounded recovery advances the source epoch and emits
  ready; the common source driver resubscribes active symbols on reconnect.
- Market uses the common async snapshot Source Driver, including bounded Actor input delivery and
  explicit source status.

## Remaining capability slices

- Spot symbol index mapping from `spotMeta` for non-display provider symbols.
- Heartbeat timing policy and live fault-injection against the provider testnet.
