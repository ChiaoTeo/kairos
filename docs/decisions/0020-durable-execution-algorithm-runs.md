# Decision 0020: Durable Execution algorithm runs and action-first dispatch

- Status: Accepted
- Date: 2026-08-25
- Scope: Execution Intent algorithms, child-order dispatch, crash recovery

## Context

Execution previously converted an Intent into child orders and scheduled them directly. Split, maker,
hedge, and lifecycle behavior existed in several application and domain paths, but there was no durable
algorithm instance that could explain which fact authorized a child order. Recomputing an action after a
crash could submit twice; persisting only an algorithm status could instead lose a decision made just
before dispatch.

Execution already has one mutable business-state owner, `ExecutionActor`, plus durable snapshots and
order attempt delivery certainty. Adding an independent algorithm executor or manager would create a
second state owner and make recovery ambiguous.

## Decision

Execution algorithms are finite, provider-neutral `AlgorithmRun` state machines owned by
`ExecutionActor`. An algorithm decision uses only explicit typed facts and business time. It carries an
expected sequence and produces typed actions with identities deterministically derived from the run and
new decision sequence.

Application persists the decision and Pending action before performing the action. A SubmitChild action
contains the stable local `OrderId`, leg identity, and quantity. Provider networking remains behind the
Integration-owned `OrderCommand`; algorithm code does not import provider payloads or SDK types.

Action completion describes delivery work, while the order entity describes the exchange order
lifecycle. Confirmed and explicitly rejected provider replies resolve the dispatch action; a proven
NotSent outcome fails it; uncertain delivery marks it Indeterminate and requires reconciliation. No
uncertain action is transparently retried under a new identity.

During restore, Actor reconciles persisted actions with order-attempt delivery certainty, restores
plan/leg identity from SubmitChild actions when needed, recomputes leg filled and committed quantities,
and persists any repaired state. The same Actor continues to own Intent, algorithm, order, fill,
commitment, and reconciliation state.

## Consequences

- Identical run state, input facts, business time, and algorithm version produce the same decision and
  action identity.
- Crashes before provider dispatch reuse the persisted action and local order identity.
- Crashes after dispatch but before action completion converge from persisted order-attempt evidence.
- A run cannot authorize another child while a prior action is still Pending.
- Core algorithm tests remain independent of provider protocol tests and live venue certification.
- Immediate is migrated first. Maker-first/taker-hedge, exposure, unwind, and TWAP extend the same run
  model rather than adding parallel executors.
- Historical snapshots without AlgorithmRun remain readable, but active historical Intent backfill must
  be explicit before those Intents can resume through the new algorithm boundary.

## Implementation anchors

- Algorithm state and decisions: `crates/modules/execution/src/domain/algorithm/`
- Actor ownership and recovery: `crates/modules/execution/src/services/actor/algorithms.rs`
- Durable Immediate dispatch: `crates/modules/execution/src/application/core/intents/lifecycle.rs`
- Snapshot model: `crates/modules/execution/src/application/model/snapshot.rs`
- Module boundary: `crates/modules/execution/README.md`
