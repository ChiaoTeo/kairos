# Decision 0021: Fill-driven maker-first and taker-hedge execution

- Status: Accepted
- Date: 2026-08-25
- Scope: PairArbitrage execution algorithm, exposure, hedge dispatch

## Context

Submitting both legs of an arbitrage Intent at acceptance does not implement maker-first execution. The
hedge must remain dormant until leader fills create real exposure. Using requested quantities alone can
over-hedge, while ignoring an already-live hedge can submit the same hedge twice. Reusing a pre-existing
limit hedge template can also accidentally place another passive order when an urgent taker is required.

Execution already owns the Intent, AlgorithmRun, order lifecycle, fills, commitments, and recovery.
These facts are sufficient to make the hedge decision without introducing a separate arbitrage manager.

## Decision

A two-leg PairArbitrage Intent with an explicit `MakerTakerHedge(HedgePolicy)` algorithm creates one
MakerTakerHedge AlgorithmRun. The policy
identifies one leader maker leg and one dormant hedge leg. Acceptance requires a leader limit price,
forces the leader request to post-only, persists the hedge request as a dormant application template,
and dispatches only the leader.

Actor maintains a normalized exposure ledger in hedge-leg quantity units. Required hedge is derived from
actual leader fills using the configured hedge ratio and contract multiplier. Filled hedge and live hedge
leaves are accounted separately. The actionable exposure is required hedge minus both, floored at zero.

When filled exposure exceeds tolerance, the domain algorithm gives a taker hedge priority over additional
maker work. It emits one stable SubmitChild action for exactly the actionable exposure. Application uses
the dormant route/account/instrument template but materializes a market order with maker and split options
removed. The action is persisted before provider submission under Decision 0020.

An optional maximum unhedged duration governs every non-zero filled tail, including exposure inside the
quantity tolerance. Actor persists the first business time of the current unhedged interval. The algorithm
derives `next_wake_at` without reading wall clock, and both maintenance and explicit time advancement drive
the same deadline. Once due, the tail is submitted as an exact taker hedge instead of being declared
complete.

An uncertain taker outcome marks the action and run Indeterminate/ReconciliationRequired and is never
retried under another identity. A live hedge commitment suppresses duplicate hedge decisions. Completion
requires the leader target, no live hedge commitment, and residual filled exposure within tolerance.

The hedge policy may contain an ordered list of fallback execution-route identities. Acceptance validates
that each route exists, differs from the primary route, is unique, and can carry the hedge market order;
temporary readiness is deliberately checked at dispatch rather than admission. Each taker action records
its selected route. The next route is eligible only when the preceding attempt has authoritative
not-sent/rejected evidence. An indeterminate attempt freezes the run for reconciliation and cannot cause
route failover.

After the primary and all configured fallback hedge routes are proven failed, the domain can select an
immediate leader unwind. Application enables it only for `FailurePolicy::Compensate`, an enabled hedge
compensation policy, remaining compensation attempts, and an explicit `max_slippage_bps` below 10,000. It
derives a bounded limit price from the latest
leader fill, submits the opposite leader side as IOC through normal Risk and Integration admission, and
persists the stable action plus complete request before dispatch. Unwind fills are accounted independently
from gross leader fills. Full closure produces the distinct `Unwound` run terminal and a failed Intent;
it is not presented as successful arbitrage completion. Indeterminate hedge or unwind delivery still
fails closed into reconciliation and is never retried under another identity.
If an unwind IOC partially fills, its remaining live quantity suppresses duplicates. A later terminal
remainder event refreshes the Intent and AlgorithmRun, and another stable unwind action covers only the
residual exposure while the compensation breaker permits it.

## Consequences

- Pair acceptance no longer creates simultaneous leader and hedge orders when MakerTakerHedge is selected.
- Hedge size follows incremental fills rather than the original requested hedge template quantity.
- Hedge actions remain deterministic and crash recoverable through the same action-first protocol as
  Immediate.
- Exposure is visible in snapshots and can be checked across restart.
- Direct and managed runtimes dispatch maker-taker children through the same persisted due-order path.
- Market taker execution is the normal hedge implementation; bounded limit IOC is reserved for emergency
  leader unwind in the current slice.
- Hedge-route failover is explicit policy, ordered, action-audited, and forbidden after an uncertain send.
- Algorithm selection is explicit under Decision 0022; Intent type and the presence of policy-shaped
  fields never imply MakerTakerHedge or Immediate behavior.

## Implementation anchors

- Maker-taker state and decision: `crates/modules/execution/src/domain/algorithm/maker_taker.rs`
- Exposure state: `crates/modules/execution/src/domain/algorithm/state.rs`
- Leader/dormant partition: `crates/modules/execution/src/application/core/intents/submission.rs`
- Fill-driven taker materialization: `crates/modules/execution/src/application/core/intents/mod.rs`
- Durable dispatch recovery: `crates/modules/execution/src/application/core/intents/lifecycle.rs`
- Recovery and order-ledger synchronization:
  `crates/modules/execution/src/services/actor/algorithms.rs`
