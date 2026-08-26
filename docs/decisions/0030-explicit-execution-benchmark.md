# Decision 0030: Explicit per-leg execution benchmark

- Status: Accepted
- Date: 2026-08-26
- Scope: AlgorithmRun input, realized execution quality, replay and wire publication

## Context

Per-leg quality already derived exact fills, notional, VWAP, timing, and fees from ExecutionActor truth,
but implementation shortfall had no authoritative market reference. An order limit is a constraint rather
than an observed market price, and process time is not a market observation. Inferring either would make
quality depend on the caller path and would not survive replay as the same business fact.

## Decision

An Intent may carry one explicit `Arrival` benchmark per executable plan leg: optional `leg_id` only for
a single-leg plan, exact `instrument_id`, `market_id`, positive `price`, and observation business time.
Admission rejects a future observation, ambiguous multi-leg assignment, duplicate assignment, unknown
leg, instrument or market mismatch, and a benchmark for an Intent with no executable plan leg.

The normalized observation is persisted on the matching `AlgorithmLegState`. Actor insertion and restore
validate it again against the owner Intent plan. ExecutionActor derives benchmark notional as filled
quantity multiplied by benchmark price. Buy shortfall is gross fill notional minus benchmark notional;
Sell shortfall is benchmark notional minus gross fill notional. Positive therefore always means worse
execution and negative means price improvement. An unfilled leg publishes benchmark notional zero and no
shortfall. Values remain exact decimal money facts; no rounded bps value is invented.

Intent events carry the original benchmark input. Current and active-Intent mmap views carry the Actor-
derived benchmark quality. Kairospy exposes the same closed `ExecutionBenchmark` value and exact JSON-RPC
shape. Limit price and wall clock are never fallback benchmarks.

## Consequences

- Fill arrival order, duplicate delivery, and restart/replay produce the same benchmark quality.
- Multi-leg algorithms can be compared without accidentally applying one market observation to every leg.
- Historical snapshots without benchmark fields remain readable as having no benchmark; this is storage
  evolution, not a second execution API.
- Post-trade markout remains a separate future observation type and is not conflated with arrival price.

## Implementation anchors

- Admission and plan matching: `crates/modules/execution/src/application/core/intents/submission.rs`
- Actor-derived quality: `crates/modules/execution/src/services/actor/algorithms.rs`
- Contract schema: `schemas/v2/execution/types/benchmark.fbs`
- Network-disorder and restart evidence: `crates/modules/execution/src/integration_tests.rs`
- Kairospy request boundary: `kairospy/investment/apps/execution/application/intents.py`
