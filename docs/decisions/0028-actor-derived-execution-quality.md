# Decision 0028: Actor-derived per-leg execution quality

- Status: Accepted
- Date: 2026-08-26
- Scope: AlgorithmRun realized quality, persistence, read models

## Context

An execution algorithm must expose what it actually achieved, not only its requested quantity and final
lifecycle. Order count, fill count, cancel pressure, realized quantity, notional, VWAP, latency, and fees
are needed to compare Immediate, TWAP, and maker-first/taker-hedge behavior. These facts already belong
to Execution, but computing a second mutable metric stream outside `ExecutionActor` would create another
state owner and could diverge after duplicate delivery, out-of-order facts, or restart.

Arrival price and decision benchmark are different business facts from an order's limit price. Inferring
slippage or implementation shortfall from the limit would publish a plausible but false metric. A
multi-leg run may also cross instruments and fee currencies, so summing all legs into one price or fee
number has no stable meaning.

## Decision

`AlgorithmRun` carries `AlgorithmExecutionQuality`, grouped by semantic leg. Actor synchronization
deterministically rebuilds it from Actor-owned orders, attempts, and fills whenever those facts change and
again after restore. The rebuilt value is checkpointed with the run and published in both current
Execution and active-Intent FlatBuffers views.

Each leg exposes exact decimal realized quantity, gross notional, optional VWAP, order/fill/cancel-attempt
counts, first submission and fill times, time to first/last fill, and fee totals grouped by currency.
Emergency unwind orders are excluded from the normal leader-leg quality; unwind effectiveness remains a
separate compensation concern. No cross-leg price or fee total is invented.

Benchmark-relative metrics such as arrival slippage, implementation shortfall, spread capture, and markout
will be added only when the Intent/AlgorithmRun receives an explicit, durable benchmark observation with
its market identity, price, and observation time. The current schema does not substitute limit price or
wall clock for that missing fact.

Historical snapshots may omit the derived `quality` field. Restore treats it as empty, rebuilds it from
canonical Actor facts, and writes a repaired checkpoint. This is a storage migration, not another
execution API or decision path.

## Consequences

- Equal Actor order/fill truth produces equal realized per-leg quality after replay and restart.
- Duplicate fills cannot increase counts, notional, fees, or generate additional hedge quality.
- Consumers can inspect algorithm quality without reconstructing it from truncated public histories.
- Cross-asset and cross-currency runs remain semantically valid because aggregation stops at the leg and
  fee-currency boundaries.
- Benchmark-dependent quality remains explicitly unavailable until its required evidence is modeled.

## Implementation anchors

- Quality model: `crates/modules/execution/src/domain/algorithm/state.rs`
- Deterministic rebuild: `crates/modules/execution/src/services/actor/algorithms.rs`
- Current-view encoding: `crates/modules/execution/src/services/publication/encoding.rs`
- Wire schema: `schemas/v2/execution/types/algorithm_run.fbs`
- Restart, weighting, and publication evidence: `crates/modules/execution/src/integration_tests.rs` and
  `crates/modules/execution/src/services/publication/mod.rs`
