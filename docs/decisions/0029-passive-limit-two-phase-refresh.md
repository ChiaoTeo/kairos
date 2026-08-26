# Decision 0029: Two-phase PassiveLimit quote refresh

- Status: Accepted
- Date: 2026-08-26
- Scope: QuoteProvisioning algorithm ownership, cancel/replace ordering, network disorder

## Context

QuoteProvisioning previously used the Immediate algorithm while refresh performed a direct
cancel-and-submit loop. Managed and direct runtimes consequently had different dispatch paths, and
maker cadence could also be inferred from order options. Staging a fixed replacement quantity before
cancel was determinate created another hazard: a fill delivered during the cancel window could make the
replacement exceed the leg's remaining target.

## Decision

QuoteProvisioning requires the explicit `PassiveLimit` algorithm. Its `reprice_interval` and
`max_quote_age` are the only algorithm timing and freshness policy; removed maker cadence fields are not
accepted as compatibility aliases. Direct and managed runtimes use the same due-order dispatch path.

Refresh is an Actor-owned, persisted two-phase transaction. Phase one persists the exact refresh request,
version, and cancel targets before any cancel command can leave the process. Replacement actions do not
exist yet. Only after every cancel has a determinate terminal result does phase two recompute each leg's
remaining quantity from current Actor fill truth, apply the deterministic PassiveLimit decision, and
persist stable actions plus complete replacement requests before venue submission.

An indeterminate cancel leaves the transaction in `Canceling`, moves the Intent to reconciliation, and
cannot activate a replacement. An identical request may resume the persisted transaction after restart;
a different request is rejected while it remains unresolved. Duplicate or late fills update the sole
Actor truth and cannot bypass the phase boundary.

## Consequences

- Immediate is no longer a valid QuoteProvisioning path in Rust or Kairospy.
- Maker order options cannot secretly schedule child orders or quote refreshes.
- A crash before, during, or after cancellation resumes the same refresh version and targets.
- A late fill observed before replacement authorization reduces the replacement quantity exactly.
- Cancel response loss remains fail-closed across restart and cannot leak replacement orders.
- Venue certification must prove cancel identity and terminal semantics; Core fixtures do not certify a
  real exchange's private-stream ordering.

## Implementation anchors

- Policy and deterministic decision: `crates/modules/execution/src/domain/algorithm/passive_limit.rs`
- Actor-owned transaction: `crates/modules/execution/src/application/model/result.rs`
- Direct and managed orchestration: `crates/modules/execution/src/application/core/orders/mod.rs` and
  `crates/modules/execution/src/application/conflux.rs`
- Network-disorder evidence: `crates/modules/execution/src/integration_tests.rs`
- Public Python policy: `kairospy/investment/apps/execution/application/intents.py`
