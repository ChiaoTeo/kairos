# Hyperliquid Market adapter provenance

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
- Signed exchange-operation implementation: Hyperliquid's
  `hyperliquid-dex/hyperliquid-rust-sdk`, crates.io `0.6.0`, source commit
  `67ee7fcb114e69f746b1efe737414e50bf261ab7`, MIT. Kairos links the SDK and does not copy its
  MessagePack hashing, nonce, EIP-712 signing, asset-index discovery, or exchange response code.

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

- Hyperliquid SDK domain models, caches, event buses and engines. The signed exchange protocol is
  intentionally delegated to the official Rust SDK; SDK request/response models remain inside the
  Integration service and do not cross the participant connection boundary.
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
- The 2026-08-25 production-public certification covers Perpetual symbol-scoped `bbo` and an active
  Spot L2 book across subscribe, receive, replacement-first reconnect/restore, receive, and
  unsubscribe. See [Hyperliquid public market-stream certification](../hyperliquid-market-stream-certification.md).

## Remaining capability slices

- Spot symbol index mapping is supplied by the SDK metadata load for signed exchange operations;
  the public Info normalizer still needs explicit display-symbol coverage.
- Private-address recovery and live fault-injection against the provider testnet.
- Market orders remain unsupported until Kairos owns an explicit slippage policy; limit GTC/Alo/Ioc
  orders and cancel-by-provider-order-id are the first command slice.

## Connection topology audit (2026-08-17)

- Official Hyperliquid documentation exposes one network-specific WebSocket endpoint, for example
  `wss://api.hyperliquid.xyz/ws` on mainnet. Source:
  <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket>.
- The same WebSocket API accepts public subscriptions (`l2Book`, `trades`, `allMids`, candles) and
  user-address subscriptions (`orderUpdates`, `userEvents`, `userFills`, funding/ledger updates).
  Multiple subscriptions coexist and unsubscribe independently. Source:
  <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions>.
- User-address filtering does not create a separate authenticated private socket. The target is one
  concrete `HyperliquidWebSocketConnection` type directly implementing the redesigned event traits;
  provider subscription state remains internal. Composition may create multiple named sockets only
  for an explicit fault/throughput boundary.
- The old `HyperliquidConnection` factory and `HyperliquidLiveMarket` adapter have been
  removed. `HyperliquidInfoRestConnection`, `HyperliquidAccountRestConnection`,
  `HyperliquidExchangeRestConnection`, and `HyperliquidWebSocketConnection` are the concrete
  owners. The exchange connection directly implements `OrderCommand`; signing stays in the SDK
  service and command transport failures are conservatively reported as indeterminate.
