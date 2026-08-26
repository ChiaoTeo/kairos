# Decision 0033: Single Execution current view and durable audit query

- Status: Superseded by [Decision 0034](0034-unified-current-view-storage.md)
- Date: 2026-08-26
- Supersedes: [Decision 0031](0031-execution-delivery-surfaces.md) mmap and history-query shape

Decision 0034 supersedes this Decision's single aggregate mmap shape. Its durable audit-query boundary,
one-ExecutionActor topology, instance-safe resource identity, and removal of compatibility aliases
remain valid.

## Context

Execution published `ActiveOrders`, `ActiveIntents`, and `CurrentExecution` mmap roots from the same
Actor generation. Only `CurrentExecution` had a production reader in this repository, while it repeated
the two active roots and retained every terminal Order, Intent, and AlgorithmRun. Its fixed-size mmap
slot was therefore not actually bounded. Connected `history`, `trace`, `journal`, and `audit` commands
then read the view's recent diagnostic window and presented it as durable history.

The physical view path also omitted launch and instance even though both identities were present in the
logical key. Concurrent instances in one workspace could resolve to the same snapshot file.

## Decision

- Execution publishes exactly one mmap root: `ECV2 CurrentExecutionView`.
- The view contains only operational Orders and Intents, their AlgorithmRuns, capacity-consuming
  commitments, active or uncertain Risk reservation sagas, and unresolved remote orders. Recent Fill,
  Order-event, and Intent-event windows remain explicitly capped operational diagnostics.
- `ECI2 ActiveIntentsView` and `ECO2 ActiveOrdersView`, their schema roots, generated code, readers,
  publishers, and runtime declarations are removed. They are not decoded or dual-published.
- The physical resource path is partitioned by workspace, launch, instance, and view identity.
- One launch instance owns one ExecutionActor and therefore one view key. That Actor owns
  arrays of operational Intents, AlgorithmRuns, Orders, commitments, reservations, and unresolved
  remote facts; the single view does not impose single-Intent execution. Multiple Execution actors,
  executor shards, shard routing, and multiple writers in one launch instance are out of scope.
- Complete Order lifecycle history uses the bounded JSON-RPC `order_audit` query backed by Execution
  audit persistence. Its response preserves typed lifecycle and delivery-attempt evidence. Venue
  open/history/detail queries remain private reconciliation inputs and never become public history.
- Connected current-view commands use explicit operational names: `active-orders`, `active-order`,
  `recent-order-events`, and `recent-fills`. Removed names are not aliases.

ExecutionActor remains the sole mutable business-state owner. Audit storage is an immutable history
authority, not a second current-state owner.

## Consequences

- Consumers cannot select overlapping mmap representations of the same current state.
- Terminal entity growth cannot exhaust the current-view slot merely because the process has run for a
  long time; active-set capacity and the explicit recent windows define its remaining bounds.
- Cross-instance mmap writer collisions are prevented by construction and tested in the contract.
- Operators can distinguish a current operational snapshot from complete durable audit history.
- This is a hard migration. Deployments must remove retired `active-orders` and `active-intents` files;
  no compatibility reader, alias, or dual-publication period exists.

## Implementation anchors

- Contract key and reader: `crates/modules/execution/contract/src/view`
- Runtime declaration/publication: `crates/modules/execution/src/composition/launch.rs` and
  `crates/modules/execution/src/application/conflux.rs`
- Operational view filtering: `crates/modules/execution/src/services/publication/encoding.rs`
- Audit persistence: `crates/modules/execution/src/services/audit`
- Connected product surface: `crates/modules/execution/src/application/connected.rs`
