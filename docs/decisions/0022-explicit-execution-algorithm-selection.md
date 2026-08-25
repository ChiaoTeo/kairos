# Decision 0022: Explicit single-path Execution algorithm selection

- Status: Accepted
- Date: 2026-08-26
- Scope: Execution Intent contract, algorithm admission, TWAP ownership

## Context

Execution previously had enough overlapping policy vocabulary to infer behavior: pair Intent plus a
hedge field could select maker-taker execution, while an interval on split options could be interpreted
as scheduled execution. That makes the effective algorithm depend on combinations of unrelated fields,
creates compatibility branches, and allows two components to believe they own dispatch timing.

Intent type, quantity splitting, and execution algorithm answer different questions. Intent type names
the business objective and validates legal shapes. Split policy materializes quantities. The algorithm
owns the state machine, cadence, and authorization of exchange-facing actions.

## Decision

Every Execution Intent command contains exactly one required tagged `ExecutionAlgorithmPolicy`:
`Immediate`, `Twap(TwapPolicy)`, or `MakerTakerHedge(HedgePolicy)`. The application and FlatBuffers
contract both require it. There is no default, legacy `hedge_policy` field, split interval, compatibility
facade, or inference from `IntentType`.

`IntentType` remains an admission constraint. MakerTakerHedge is legal only for PairArbitrage. TWAP is
currently legal only for one planned leg. Immediate is selected explicitly even when it matches the
historical behavior of a single or multi-leg Intent.

TWAP alone owns `slice_count` and `slice_interval`. Ordinary `SplitOrderPolicy` owns quantity subdivision
only and cannot carry timing. TWAP rejects simultaneous split options, persists one `TwapSpec` with its
business-time start, derives each deadline by slice index, and authorizes every slice through the common
action-first dispatch path from Decision 0020.

This is a breaking migration. Current callers must send the new tagged algorithm; Execution does not
translate or accept the removed shapes.

## Consequences

- A command has one inspectable source of truth for its algorithm and timing semantics.
- Adding an algorithm means adding one policy variant, one durable spec/decision implementation, and its
  admission/tests; it does not add another executor or scheduler facade.
- Split quantity and TWAP cadence cannot silently combine or override each other.
- Maker-taker selection no longer depends on optional-field presence.
- Old command payloads fail decoding/admission instead of silently taking an assumed Immediate path.
- Storage recovery may decode historical snapshot structure where explicitly supported, but it cannot
  be used as a second command API or to infer a resumable algorithm.

## Implementation anchors

- Command policy: `crates/modules/execution/src/domain/intent/policy.rs`
- Contract policy: `crates/modules/execution/contract/src/control/types.rs`
- Admission and run construction: `crates/modules/execution/src/application/core/intents/submission.rs`
- TWAP decision: `crates/modules/execution/src/domain/algorithm/twap.rs`
- Wire schema: `schemas/v2/execution/types/intent.fbs`
