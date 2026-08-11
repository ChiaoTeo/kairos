# Binance Spot

This is the first reference note for the Phase 2 Binance Spot
provider-native connection migration described in
[`integration-session-and-operation-design.md`](../integration-session-and-operation-design.md).

## Upstream reference

- Repository: [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader)
- Adapter path: `crates/adapters/binance`
- Branch: record the exact branch/tag/commit used for each migration change
- License: LGPL-3.0; preserve notices when source is reused
- Kairos implementation: `crates/kairos-integration/src/application/providers/binance.rs`

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
| REST client, clock, signer, endpoint set | `BinanceSpotProviderConnection` / services gateway |
| Credential and account context | `BinanceSpotPrivateConnection` |
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
