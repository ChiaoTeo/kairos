# Binance Spot

This is the first reference note for the Phase 2 Binance Spot
provider-native connection migration described in
[`integration-session-and-operation-design.md`](../integration-session-and-operation-design.md).

## Upstream reference

- Repository: [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader)
- Adapter path: `crates/adapters/binance`
- Branch: record the exact branch/tag/commit used for each migration change
- License: LGPL-3.0; preserve notices when source is reused
- Kairos implementation:
  `crates/kairos-integration/src/application/participants/binance/connection.rs`,
  `connection/{account,execution,funding,reference,blocking}.rs`, and
  `crates/kairos-integration/src/services/participants/binance/spot/`

## Provider behavior to review

- Spot REST authentication, timestamp, and request signing;
- order submit, cancel, and query response semantics;
- private user-data WebSocket authentication and subscription;
- `executionReport` normalization and duplicate delivery;
- reconnect, sequence continuity, and snapshot/resync behavior;
- provider/IP quota versus principal/account quota;
- timeout and response-loss handling for `Indeterminate` commands;
- bounded queues and explicit backpressure for private event streams.

## Kairos mapping

| Provider behavior | Kairos owner |
|---|---|
| REST client, signer, endpoint set | `BinanceSpotConnection` / `BinanceRequestRuntime` |
| Credential and Spot account context | `BinanceSpotPrincipalConnection` |
| Submit/cancel | `BinanceSpotOrderEntry` and `CommandOutcome` |
| Open/history/detail queries | `BinanceSpotOrderQuery` |
| Private order events | `BinanceSpotOrderEvents` |
| External order/fill facts | `ExternalOrder` / `ExternalExecutionEvent` |
| Provider and principal quota | provider/private context and quota services |
| Route selection | business composition |
| Canonical order state | Execution business Actor, not Integration |

## Migration constraints

- The official Binance API documentation is the fact source.
- Nautilus behavior may guide implementation and test selection, but its
  domain types, runtime, cache, and event bus are not imported into Kairos.
- Async capability projections are the default path.
- Blocking projections belong under `kairos_integration::blocking`.
- Submit/cancel must not transparently retry after the request may have been
  sent.
- Read-only queries may use bounded retries.
- Private stream overflow must produce backpressure or resync, never silent
  loss.
- The migrated slice must remove its old registry/`ConnectionSpec` path.

## Reuse record

Update this section whenever upstream source or tests are actually reused:

- upstream commit:
- copied source:
- rewritten logic:
- tests adopted:
- local modifications:

## Exit criteria

- provider-native connection can be constructed directly;
- order entry, query, and order-event projections share the intended provider
  and principal resources;
- command/query/stream failure semantics have focused tests;
- async path runs on the business process runtime;
- blocking facade rejects accidental use from a Tokio worker;
- old Binance Spot registry and generic connection construction paths are
  deleted for the completed slice;
- the integration migration ledger is updated.

## Market source status (2026-08-11)

- Market no longer imports a blocking Binance connection and no longer runs a feed worker.
- Binance facts enter the Actor through the common async stream Source Driver.
- Spot WebSocket now uses `AsyncTokioSocket` plus `AsyncPublicHttpClient` directly on the caller's
  Tokio runtime. Subscription performs the provider snapshot barrier before deltas are accepted;
  WebSocket queue overflow is returned as explicit Integration backpressure.
- The native current-thread test uses local WebSocket and HTTP servers and proves that subscription
  yields the REST depth snapshot without a blocking bridge.
- Spot REST uses the `BinanceSpotConnection` typed catalog; USD-M, COIN-M, and Options market
  sources use their own family-native connections. No cross-family projection is constructed from
  a Spot principal.

## Account source status (2026-08-11)

- Spot snapshot/profile and private account events are projected from one
  `BinanceSpotPrincipalConnection` and run on the Account process Tokio runtime.
- Funding snapshot shares the same principal/runtime and does not invent a funding private stream.
- Account private envelopes record participant, binding, channel, epoch, provider event ID,
  sequence, observed time, and received time.
- Account production composition selects the typed Spot, Futures, and Options connections directly;
  no generic Binance connection or cross-family Spot-principal projection remains in production.
- Live production acceptance found two provider/network facts that local protocol tests did not
  expose:
  - `/api/v3/time` can arrive through a high-latency network path. Signed queries calibrate a
    conservative provider clock, and only semantically safe queries retry once after Binance
    `-1021`; submit/cancel commands are never transparently retried.
  - although the Binance WebSocket API documentation describes request `id` as arbitrary, the
    production gateway used in acceptance disconnected signed user-data subscriptions whose IDs
    contained `.` or `:`. Adapter-generated subscription IDs therefore use a bounded ASCII
    alphanumeric/hyphen subset. The same credential and payload returned `status=200` and a
    `subscriptionId` after this normalization.
- The ignored `live_hmac_user_data_subscription_connects` contract test exercises the real HMAC
  user-data subscription only when credentials are supplied explicitly. It performs no order
  command.
- A `.kairos` diagnostic Account instance reached `ready` with an authenticated, healthy required
  Binance Spot channel and a completed initial snapshot. A credential binding declared
  `role=readonly` remains non-trading even if remote credential inspection reports trade
  permission, and such an observer does not require a trade lease.
