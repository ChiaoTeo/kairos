# Kairos shared primitives

`kairos-primitives` is the project's shared semantic kernel. It contains
small, infrastructure-free value objects whose meaning and invariants are
genuinely shared by multiple business modules. It is not a general
common-types or utilities crate and it does not own business workflows.

Business types are grouped by their governing vocabulary: account, execution,
market, reference, risk, and integration. Genuinely cross-cutting value
mechanics such as decimal and time remain separate groups. The current broad
`identity` and `trading` files are migration sources, not the desired final
taxonomy: their members should move into the governing group without changing
their meaning or wire representations. The target public paths are namespaced,
for example `kairos_primitives::account::AccountId` and
`kairos_primitives::execution::OrderId`; broad root wildcard re-exports should
be removed as callers migrate. These groups make ownership visible; they are
not miniature domain modules and do not transfer workflows or mutable state
into this crate.

```text
src/
  account.rs
  capital.rs
  execution.rs
  integration.rs
  market.rs
  reference.rs
  risk.rs
  decimal.rs       cross-cutting exact values
  time.rs          cross-cutting time units
```

## Admission rule

A type belongs here only when all of the following are true:

1. The value is currently needed across its owner's domain/contract boundary,
   or at least two business modules use it with the same meaning and
   invariants. A purely internal domain value does not qualify, and merely
   consuming a complete event does not move that event into primitives.
2. The type is a semantic atom that contracts and domains can use directly.
   A business module may still govern its meaning, but the type does not carry
   that module's workflow, mutable state, or lifecycle behavior. Commands,
   events, snapshots, lifecycle state machines, and module-specific policies
   remain owned by their module.
3. The type is free of transports, persistence records, SDK payloads, runtime
   handles, and provider clients.
4. Conversions at wire, persistence, and integration boundaries remain
   explicit and fallible where input can be invalid.
5. Moving the type removes an existing duplicate definition or serves a
   current cross-module caller. Prospective uniformity is not sufficient.

Module-owned data that must cross a package or process boundary belongs in
`crates/modules/<owner>/contract`; contract messages should use these
primitives whenever a field has the established shared meaning. This keeps a
canonical identity or value usable across contracts without introducing
contract-to-contract dependency cycles. In short: primitives share the words;
contracts define what a module says with those words.

Do not admit generic collection helpers, string manipulation, logging,
configuration convenience, serialization utilities, or other meaning-free
code. Those are utilities even when many crates could reuse them.

## Current ownership decisions

| Family | Owner | Reason |
|---|---|---|
| Canonical IDs, provider IDs, order/fill IDs | primitives | Shared identity semantics across Reference, Market, Account, Execution, Risk, and Integration |
| Price, quantity, money, rate, ratio | primitives | Shared exact numeric invariants; wire encodings remain in contracts |
| Sequence, generation, Unix time, duration | primitives | Shared unit values without runtime behavior |
| Order side and normalized external order status | primitives | Shared provider-neutral facts; Execution retains its richer lifecycle |
| Instrument kind, asset class, reference status | primitives for the current migration baseline | Reference defines canonical meaning and multiple modules consume that exact taxonomy; review if their semantics diverge |
| Account state, balances, positions, refresh events | Account | Account-owned business state and events |
| Execution order lifecycle, plans, policies | Execution | Execution-owned state machine and behavior |
| Market sources, subscriptions, books, observations | Market | Market-owned state and runtime vocabulary |
| Risk amount, metrics, budgets, reservations | Risk | Risk-specific measurement semantics and policy state |
| External decimal/payload records | Integration | Boundary representations converted explicitly into domain values |

## Migration checklist

Before moving a type here, record its current callers, prove identical
invariants, migrate one slice, update boundary conversions, delete the old
definition, and add tests for invalid construction and representation
round-trips. Do not leave compatibility wrappers without a current caller.
