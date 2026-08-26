# Decision 0026: Execution order fact ordering and terminal conflicts

- Status: Accepted
- Date: 2026-08-26
- Scope: private execution ordering evidence, lifecycle convergence, reconciliation

## Context

Command responses, private-stream facts, and remote queries can arrive in a different order from the
venue lifecycle. Occurrence time is not a total order, and a provider sequence is normally scoped to one
physical channel rather than one order. Blindly applying arrival order can regress a filled order to
accepted or overwrite one terminal fact with an incompatible terminal fact. Treating every sequence gap
as loss is also invalid because intervening channel events may belong to other orders.

## Decision

`ExecutionActor` persists the last normalized private-order cursor on each order and the source cursor on
each fill and unknown-remote-order fact. A cursor consists of connection identity, channel identity,
channel epoch, and an optional participant sequence.

Ordering comparisons are made only within the same connection and channel. A lower channel epoch is
stale. Within one epoch, a lower participant sequence is stale. A higher epoch is a reconnect reset and
may begin from any sequence. Sequence gaps and equal sequences are not interpreted as loss or duplicate
evidence; exact event and fill identities own deduplication. Providers without a sequence continue to use
those identities, occurrence time, and lifecycle monotonicity.

A non-advancing acknowledgement or private fact cannot regress current order status, but its newer
cursor is still persisted. Different terminal statuses are not ordered by arrival time: the order enters
`Unknown` with `AuthoritativeFactConflict`, its commitment and Risk reservation become `Uncertain`, and
further stream/ack facts cannot silently clear the conflict. A complete remote query may resolve it; a
matching cumulative target quantity is canonical `Filled` evidence even if the provider's terminal label
differs. Repeated conflict delivery is idempotent.

Command acknowledgements do not invent a provider cursor. Private Conflux envelopes map their real
connection/channel/epoch/sequence evidence into the same order transition. Old snapshots decode a missing
cursor and reconciliation cause as `None`; this is a storage migration, not a compatibility execution
path.

## Consequences

- Reconnect sequence resets cannot make an older socket epoch authoritative again.
- Per-order state does not reject valid channel sequence gaps caused by other orders.
- Terminal disagreement is durable, observable, restart-safe, and fail-closed.
- Query reconciliation is the explicit authority for clearing a terminal conflict.
- Providers that do not publish sequence evidence remain safe through identity and state monotonicity,
  but cannot gain sequence-based stale-event rejection.

## Implementation anchors

- Cursor and reconciliation cause: `crates/modules/execution/src/domain/order/entity.rs`
- Actor transitions and persistence: `crates/modules/execution/src/services/actor/reconciliation.rs`
- Command/private/query convergence: `crates/modules/execution/src/application/core`
- Conflux envelope mapping: `crates/modules/execution/src/application/conflux.rs`
- Restart and ordering evidence: `crates/modules/execution/src/integration_tests.rs`
