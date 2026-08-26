# Decision 0025: Actor-owned Execution business time

- Status: Accepted
- Date: 2026-08-26
- Scope: Execution Core time ownership, replay determinism, lifecycle and maker refresh cadence

## Context

Algorithm decisions already accepted an explicit business time, but `ExecutionApplication` also held a
non-durable clock and maker quote freshness/cadence read the process wall clock directly. Restart could
therefore forget the latest admitted time, accept a regressing replay time, or evaluate the same quote
observation differently. A general clock trait would not solve the ownership or persistence problem.

## Decision

`ExecutionActor` is the sole owner of the monotonic Execution business time. The value is part of the
durable `ExecutionSnapshot`, is restored before algorithm recovery, and is synchronized into the private
intent-planning worker when that worker is attached. Advancing time through Application validates it
against Actor state and persists the changed snapshot.

Intent admission uses the later of an explicit source-event time and the Actor clock. It fails when
neither exists. Maker quote refresh carries an explicit `business_time_unix_nanos`; quote future/stale
checks, minimum refresh cadence, the persisted refresh time, and replay all use that value. There is no
wall-clock overload or compatibility command.

Order admission, dispatch attempts, Intent cancellation/expiration/status refresh, reconciliation,
fills, hedge/fallback/unwind actions, and scheduled child dispatch use the same Actor time. A planned
child template is re-stamped with the durable decision time when it becomes dispatchable; an old Intent
source timestamp is never replayed as a new order attempt time.

Provider facts may arrive out of occurrence-time order. Their source occurrence time remains on the
fill, while the Actor clock and current-order update time advance with the maximum observed business
time. Fill identity provides idempotency. A stale provider acknowledgement cannot regress a more
advanced or terminal current order, and a conflicting remote order identity is rejected. Provider
sequence remains ordering evidence rather than a second Execution clock; its scoped cursor and terminal
conflict semantics are defined by Decision 0026.

Live and paper composition may sample wall clock at the Conflux timer/control boundary and immediately
admit that sample through the same `advance_time` path. Backtest and replay disable that source and move
time only from explicit replay input. Provider event time may advance the clock but cannot move it
backwards when an older private event arrives.

Snapshots written before this decision may decode a missing clock as `None`; that is a storage migration
only. Every new clock advance writes the canonical field and no alternate runtime state is maintained.

## Consequences

- Equal Actor snapshot, input facts, and business time produce equal algorithm and maker-refresh decisions.
- Restart preserves monotonic time, TWAP/tail deadlines, and quote refresh cadence.
- Core and replay paths do not read wall clock to choose an algorithm action.
- Late fills remain applicable without regressing current state; stale acknowledgements become no-ops.
- Live processing time is observable durable input instead of hidden ambient state.
- Advancing live/paper processing time writes a snapshot; optimization requires measurement and must not
  weaken the persistence invariant.

## Implementation anchors

- Actor ownership and restore: `crates/modules/execution/src/services/actor/mod.rs`
- Snapshot and current view: `crates/modules/execution/src/application/model/snapshot.rs`
- Application advance/admission: `crates/modules/execution/src/application/core/mod.rs`
- Explicit maker refresh time: `crates/modules/execution/src/application/model/command.rs`
- Runtime clock-source selection: `crates/modules/execution/src/application/conflux.rs`
- Restart/replay and out-of-order evidence: `crates/modules/execution/src/integration_tests.rs`
