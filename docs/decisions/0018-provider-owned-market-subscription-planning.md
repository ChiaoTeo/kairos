# Decision 0018: Provider-owned Market subscription planning

- Status: Accepted
- Date: 2026-08-25
- Scope: Market demand planning and provider-owned Integration market-stream lifecycles

## Context

Market accepts strategy-owned observation requirements and resolves them to private provider routes.
Multiple strategies may require the same provider market at the same time, so Market must combine those
logical consumers into one desired physical subscription and release it only after the final consumer is
gone.

That shared demand is not yet a valid exchange connection plan. Providers differ in endpoint families,
stream naming, connection capacity, control-message rate limits, session lifetime, acknowledgement and
reconciliation facilities. These are current protocol facts rather than Market business vocabulary.
Putting them in Market would expose provider transport details through its application and domain. A
single generic planner would instead reduce real provider differences to optional flags and conditionals.

Binance makes the boundary concrete:

- Spot uses raw or combined streams and permits at most 1,024 streams on one connection;
- USD-M routes public book feeds and market feeds through different WebSocket paths; the legacy
  `/ws` and `/stream` endpoints were decommissioned on 2026-04-23;
- COIN-M retains its own `dstream` endpoint and a 1,024-stream connection limit;
- Options separates public and market paths and permits at most 200 streams on one connection;
- connections have a maximum lifetime of 24 hours, while control-message limits differ by product.

The protocol facts above are maintained in Binance's official Spot, USD-M, COIN-M, and Options
WebSocket documentation. They are cited as upstream sources under Implementation anchors.

## Decision

### Market compiles demand, Integration compiles provider plans

Market owns the first compilation step:

```text
strategy requirements
  -> owner-scoped logical subscriptions
  -> provider route resolution
  -> deduplicated desired physical subscriptions
```

The physical subscription identity contains the selected provider, private provider segment,
subscription symbol, and normalized observation selectors, including qualifiers such as a bar interval.
It does not contain a strategy owner. Market retains owner references and reconciles its desired set
with confirmed provider handles. Identical selector sets share one handle; overlapping selector sets may
use separate handles while the concrete provider component reference-counts their shared native streams.

The concrete Integration connection owns the second compilation step:

```text
desired provider feeds
  -> provider-native stream names
  -> traffic class and endpoint
  -> capacity shard
  -> rate-limited subscribe/unsubscribe operations
```

Market does not select Binance paths, shard numbers, request batch sizes, ping behavior, or session
rotation deadlines.

### Provider-specific components

Each concrete provider connection that has distinct production behavior owns its own subscription
planning implementation. This is not a new cross-provider manager, registry, port, or public protocol.
The existing Integration `MarketSubscriptionCommand`, connection lifecycle, maintenance, health, and
stream capabilities remain the stable lower-level boundary used by Conflux.

For Binance, one product connection is presented to Conflux under one connection key while an internal
Binance market-stream component may own multiple sockets. The component owns:

- a typed product policy for Spot, USD-M, COIN-M, or Options;
- classification of every provider-native stream as `public` or `market` where the product requires it;
- stable first-fit packing into capacity-bounded shards;
- the desired stream set and the confirmed server stream set per shard;
- control-message pacing and deterministic batching;
- acknowledgement correlation and buffering of interleaved market events;
- scheduled session rotation and restoration before the provider's 24-hour disconnect;
- provider-supported reconciliation, using `LIST_SUBSCRIPTIONS` for Binance;
- aggregate connection health across its private shards.

The first implementation uses stable first-fit placement in demand-admission order. Existing assignments
remain stable until their final reference is released; ordinary release does not move unrelated streams.
This keeps subscription churn bounded without introducing a generalized optimizer.

### Binance product policy

The built-in production policy is:

| Product | Traffic routing | Maximum streams/socket | Maximum inbound control messages | Rotation |
| --- | --- | ---: | ---: | ---: |
| Spot | one Spot stream endpoint | 1,024 | 5/second | before 24 hours |
| USD-M | `public` and `market` sockets | 1,024 | 10/second | before 24 hours |
| COIN-M | one COIN-M stream endpoint | 1,024 | 10/second | before 24 hours |
| Options | `public` and `market` sockets | 200 | 10/second | before 24 hours |

Provider ping and client pong frames count toward Binance's published incoming-message limits. The
implementation therefore reserves control-message headroom instead of treating the published maximum as
an application control-message send rate.

Endpoint overrides configure a product base endpoint, not one permanently selected traffic-class path.
The Binance component derives the required `public` or `market` path. A legacy USD-M or Options endpoint
ending in `/ws` or `/stream` is rejected during configuration instead of starting a partially working
connection. COIN-M and Spot keep their product-specific path conventions.

### Other current provider policies

The same ownership decision is realized without forcing providers into one algorithm:

| Provider | Native physical identity and capacity policy | Recovery policy |
| --- | --- | --- |
| OKX | typed channel arguments; 456 ordinary operations of the published 480/hour; control payloads below 64 KiB | correlate each argument acknowledgement and restore a replacement public socket before retiring the old one |
| Hyperliquid | symbol-scoped `bbo`, L2/trades/candles, and shared `activeAssetCtx`; process-shared 950 ordinary subscriptions of the per-IP 1,000 and 1,900 ordinary messages/minute of the per-IP 2,000 | accept provider-augmented acknowledgements; replace first when overlap fits, otherwise disconnect and restore within the global limit |
| Massive | six product-native channel vocabularies; Options enforces the documented 1,000 quote-contract limit | authenticate and restore a replacement product socket first; classify entitlement failures immediately |
| IBKR | symbol-scoped Level-I lines; configurable allowance defaults to 100 with 10% ordinary reserve; 45 ordinary messages/second of the published 50 | preserve logical demand across a disconnect-first TWS/Gateway client-ID reconnect and pace restoration |

OKX channel arguments, Hyperliquid subscription objects, Massive channel strings, and IBKR TWS
contracts stay private to their concrete Integration connections. Market continues to express only
normalized observation requirements.

### Desired and actual state

An accepted Market subscription handle identifies desired provider demand, not a socket-local request.
The Binance component keeps it valid across reconnects, shard replacement, and session rotation.

State is tracked separately:

- desired: streams referenced by active Integration subscription handles;
- planned: traffic class and shard selected for each desired stream;
- confirmed: streams acknowledged or returned by provider reconciliation;
- indeterminate: operations whose transport outcome is unknown and require reconciliation.

An unsubscribe removes desired demand only after the provider confirms removal or reconciliation proves
the stream absent. Reconnect and rotation rebuild sockets from desired state; they do not manufacture new
business subscription handles.

### Lifecycle and ownership

Conflux remains the only lifecycle owner of concrete connections, as required by Decision 0004. The
Binance component's sockets are private implementation details driven through that connection's
`connect`, `disconnect`, `reconnect`, `poll_next`, and maintenance methods. It must not spawn a second
unmanaged production event loop.

Market remains the sole owner of subscription business state and freshness. Integration owns only
provider-operation state required to realize the desired subscription. Provider shard identities and
server subscription inventories are diagnostic facts, not Market contract DTOs.

## Consequences

- Multiple strategies share one Market physical demand while Binance may realize it across the minimum
  number of protocol-valid sockets.
- USD-M and Options can route observation types to the required current endpoint without exposing those
  paths to strategies or Market contracts.
- Provider limits are enforced before network requests rather than discovered through intermittent
  rejections.
- A provider reconnect, reconciliation, or 24-hour rotation no longer changes the logical subscription
  handle observed by Market.
- New providers implement only their concrete current differences; no empty per-exchange component is
  added merely for symmetry.
- Live-provider certification remains separate from deterministic unit and fixture tests and must state
  the environment, product, feeds, and evidence date.

## Verification

The Binance implementation must demonstrate:

1. feed-to-traffic-class mapping for every supported observation and product;
2. rejection of unsupported product/feed combinations and legacy endpoints;
3. deterministic sharding at 1,024 streams for Spot/USD-M/COIN-M and 200 for Options;
4. deduplication of repeated stream demand and final-owner release behavior;
5. control-message pacing without exceeding the product policy;
6. `LIST_SUBSCRIPTIONS` delta calculation and repair during reconnect restoration;
7. rotation that connects and restores a replacement before retiring the old socket;
8. reconnect restoration without changing Integration subscription handles;
9. aggregate health across all private shards;
10. focused Market, Integration, and Conflux tests plus repository architecture checks.

Live certification is recorded only after subscribe, receive, unsubscribe, reconnect, and rotation or an
equivalent accelerated rotation test succeed against the applicable Binance environment.

## Implementation anchors

- Market desired physical plan:
  `crates/modules/market/src/application/sources/subscriptions.rs`
- Market managed connection adapter:
  `crates/modules/market/src/application/conflux.rs`
- Binance concrete connections:
  `crates/platform/integration/src/participants/binance/mod.rs`
- Binance stream mapping:
  `crates/platform/integration/src/services/participants/binance/stream.rs`
- Binance socket lifecycle:
  `crates/platform/integration/src/services/participants/binance/socket.rs`
- OKX planner:
  `crates/platform/integration/src/services/participants/okx/market_stream.rs`
- Hyperliquid planner:
  `crates/platform/integration/src/services/participants/hyperliquid/market_stream.rs`
- Massive planner:
  `crates/platform/integration/src/services/participants/massive/market_stream.rs`
- IBKR planner:
  `crates/platform/integration/src/services/participants/ibkr/market_stream.rs`
- Binance Spot WebSocket streams:
  <https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams>
- Binance USD-M connection and migration:
  <https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect>
  and
  <https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice>
- Binance COIN-M connection:
  <https://developers.binance.com/docs/derivatives/coin-margined-futures/websocket-market-streams/Connect>
- Binance Options connection:
  <https://developers.binance.com/en/docs/products/derivatives-trading-options/websocket-market-streams/Connect>
