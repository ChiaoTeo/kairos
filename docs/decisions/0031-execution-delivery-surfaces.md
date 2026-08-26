# Decision 0031: Single-purpose Execution delivery surfaces

- Status: Superseded by [Decision 0033](0033-single-execution-current-view-and-audit-query.md)
- Date: 2026-08-26
- Scope: JSON-RPC control/query, Aeron business events, mmap current views, venue reconciliation query

## Context

Execution exposes control, change notification, current state, durable audit, and provider recovery. If
the same current truth is independently served by JSON-RPC, Aeron payloads, provider queries, and mmap,
consumers can observe conflicting owners and compatibility paths. The initial active mmap encoders also
published terminal history despite their names, while a draft standalone `ReconciliationRequired` event
had decoders but no producer.

## Decision

Each process surface has one purpose:

- Contract-owned JSON-RPC over the workspace Unix socket carries commands and bounded on-demand
  operations such as health, route discovery, reconciliation, explicit time advance, and backtest. It
  does not add order, Intent, or AlgorithmRun listing APIs that duplicate current views.
- Aeron `execution-events` carries ordered immutable business changes: Intent admission/lifecycle and
  its Plan, Order lifecycle, and Fill facts. Reconciliation is the affected Intent/Order lifecycle; the
  unused draft `EXV2 ReconciliationRequired` root and its compatibility decoders are removed.
- mmap carries replaceable current views with generation and applied-event-sequence metadata.
  `ActiveOrders` contains only orders that may still change, capacity-consuming commitments, and active
  or uncertain Risk reservation sagas. `ActiveIntents` contains only operational Intents, including
  reconciliation-required Intents, and their AlgorithmRuns. `CurrentExecution` is the bounded operational
  snapshot containing complete current entities, unknown remote orders, and explicitly truncated recent
  fill/order/Intent history; it is not the durable audit history.
- Venue open-order, history, and detail queries are private reconciliation inputs. Their normalized facts
  must enter ExecutionActor and persistence before appearing through events or views; their raw result is
  never a parallel public truth.

ExecutionActor remains the only mutable business-state owner. Conflux owns transport resources and
publishes encodings of the same Actor generation; no publisher or query result owns a second state copy.

## Consequences

- Consumers bootstrap from an mmap view and continue from Aeron sequence without querying a second state
  facade.
- Terminal history cannot accumulate in an `Active*` mmap view; the durable audit remains authoritative.
- Algorithm benchmark input is visible in Intent events, while continuously derived quality is a current
  view fact reconstructed from Fill events rather than a noisy second metric event stream.
- Adding a new surface requires a distinct current caller and semantics, not compatibility with an old
  path.

## Implementation anchors

- Surface declarations: `crates/modules/execution/src/composition/launch.rs`
- Publication ordering: `crates/modules/execution/src/application/conflux.rs`
- Event change set and encoding: `crates/modules/execution/src/application/model/event.rs` and
  `crates/modules/execution/src/services/publication/events.rs`
- mmap view encoders: `crates/modules/execution/src/services/publication/encoding.rs`
- Private venue reconciliation: `crates/modules/execution/src/application/core/reconciliation/mod.rs`
