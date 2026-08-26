# Decision 0027: Durable cancel evidence and fail-closed replacement

- Status: Accepted
- Date: 2026-08-26
- Scope: cancel delivery evidence, reconciliation, portable order replacement

## Context

Submit dispatch was persisted as indeterminate before a provider call, but cancel dispatch was not.
A process crash after sending cancel and before receiving its response could therefore leave no durable
evidence and permit an unsafe retry. The managed replace command also treated completion of the cancel
call as sufficient to submit a replacement, even if the acknowledgement did not prove `Canceled`.
Replacing the original total quantity after a partial fill could over-execute the requested lifecycle
quantity.

## Decision

Every order attempt carries an explicit `Submit` or `Cancel` command kind. `ExecutionActor` appends and
persists an `Indeterminate` cancel attempt before Integration can send the command. Proven local
non-delivery resolves it to `NotSent`; an identity-matched, explicit canceled acknowledgement resolves it
to `Confirmed`; a bounded private/query fact resolves a previously uncertain attempt to `Reconciled`.
An unresolved indeterminate cancel blocks another cancel across restart.

The public replace command has one portable path: cancel the original order, require authoritative
`Canceled`, then submit a new order through ordinary Risk admission and durable submit dispatch. The
replace quantity means desired total quantity across the original and replacement orders; Execution
subtracts the original cumulative fill and rejects a non-positive remainder. Terminal originals are not
replaceable. The unused package-level cancel-then-submit `replace` facade is removed, and provider-native
amend is not exposed as a parallel business path.

## Consequences

- Cancel response loss cannot silently become a second cancel after restart.
- Query/private reconciliation is required before retrying an uncertain cancel.
- A malformed success response or non-canceled lifecycle cannot release replacement submission.
- Partial fills cannot cause a replacement to exceed its requested lifecycle total.
- Provider-native amend remains an Integration capability only until Execution owns a complete typed,
  durable amend lifecycle; it cannot bypass this boundary.

## Implementation anchors

- Typed attempts: `crates/modules/execution/src/domain/order/route.rs`
- Actor attempt ownership: `crates/modules/execution/src/services/actor/orders.rs`
- Cancel and replace orchestration: `crates/modules/execution/src/application/core/orders/mod.rs` and
  `crates/modules/execution/src/application/conflux.rs`
- Wire observability: `schemas/v2/execution/types/order.fbs`
- Failure/restart tests: `crates/modules/execution/src/integration_tests.rs`
