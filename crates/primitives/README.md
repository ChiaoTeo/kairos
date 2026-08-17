# Kairos shared primitives

`kairos-primitives` contains small, infrastructure-free value objects whose
meaning and invariants are genuinely shared by multiple business modules.
It is not a general common-types crate and it does not own business workflows.

## Admission rule

A type belongs here only when all of the following are true:

1. At least two business modules independently use the value with the same
   meaning and invariants. Merely consuming another module's event does not
   count as independent ownership.
2. Removing the module name leaves the meaning complete. Commands, events,
   snapshots, lifecycle state machines, and module-specific policies remain
   owned by their module.
3. The type is free of transports, persistence records, SDK payloads, runtime
   handles, and provider clients.
4. Conversions at wire, persistence, and integration boundaries remain
   explicit and fallible where input can be invalid.
5. Moving the type removes an existing duplicate definition or serves a
   current cross-module caller. Prospective uniformity is not sufficient.

Module-owned data that must cross a process boundary belongs in
`crates/modules/<owner>/contract`; contract fields may use these primitives.
In short: primitives share vocabulary atoms, while contracts share what a
module says.

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
