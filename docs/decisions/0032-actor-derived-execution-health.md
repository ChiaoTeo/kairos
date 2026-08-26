# Decision 0032: Actor-derived Execution operational health

- Status: Accepted
- Date: 2026-08-26
- Scope: Execution health contract, reconciliation observability, runtime readiness

## Context

Execution can remain process-healthy while an order command is indeterminate, an Intent requires
reconciliation, a remote order has no local owner, or durable Risk evidence cannot be recovered. A
health response based only on route connectivity, writer takeover, and outbox publication can therefore
report `ready` while business execution is fail-closed and needs operator attention.

## Decision

The bounded JSON-RPC health response derives business recovery signals from the same ExecutionActor
truth used by current views and algorithm decisions. It reports separate counts for orders and Intents
requiring reconciliation, unresolved remote orders, and indeterminate AlgorithmRun actions, together
with Risk recovery readiness and its current error.

Any non-zero business recovery count or unready Risk recovery state makes the aggregate status
`degraded`, as do an unready required route, incomplete writer takeover, or an outbox read error. The
counts remain separate because one uncertain provider command can legitimately affect an Order, its
Intent, and its AlgorithmRun; they are not summed into a misleading incident total. Health remains a
bounded operational query and does not become another order or Intent listing API.

## Consequences

- A persisted safety blocker remains visible through health after restart.
- Operators can distinguish transport, publication, Risk, unknown-order, and algorithm recovery work.
- Health is derived read-only in Application; it owns no mutable state beside ExecutionActor.
- A degraded aggregate is diagnostic and does not invent a global business mutation or bypass the
  existing per-order and per-Intent fail-closed rules.

## Implementation anchors

- Contract: `crates/modules/execution/contract/src/control/types.rs`
- Actor-derived summary: `crates/modules/execution/src/application/core/mod.rs`
- Contract mapping: `crates/modules/execution/src/application/conflux.rs`
- Restart and blocker tests: `crates/modules/execution/src/integration_tests.rs`
