# Kairos shared primitives

`kairos-primitives` is the project's shared semantic kernel. It contains
small, infrastructure-free value objects whose meaning and invariants are
stable across an owner's domain/contract boundary or genuinely shared by
multiple business modules. It is not a general common-types or utilities crate
and it does not own business workflows.

Business types are grouped by their governing vocabulary: account, capital,
execution, market, reference, risk, and integration. Genuinely cross-cutting value
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
  runtime.rs       cross-cutting validated runtime identities
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
5. Moving the type removes an existing duplicate definition, serves a current
   owner domain/contract boundary, or serves a current cross-module caller.
   Prospective uniformity is not sufficient.

Module-owned data that must cross a package or process boundary belongs in
`crates/modules/<owner>/contract`; contract messages should use these
primitives whenever a field has the established shared meaning. This keeps a
canonical identity or value usable across contracts without introducing
contract-to-contract dependency cycles. In short: primitives share the words;
contracts define what a module says with those words.

The serialized representation is not a reason to downgrade the Rust model.
A serde-transparent `MarketId` may still encode as a JSON string, and
`UnixNanos` may still encode as an integer. Raw values belong in generated
wire accessors, provider DTOs, CLI/config input, and private persistence rows;
adapters convert them once with the primitive's fallible constructor.

Do not admit generic collection helpers, string manipulation, logging,
configuration convenience, serialization utilities, or other meaning-free
code. Those are utilities even when many crates could reuse them.

## Current ownership decisions

| Family | Primitive namespace / governor | Boundary |
|---|---|---|
| Canonical catalog IDs and taxonomy | `reference` / Reference | Shared atoms only; catalog lifecycle and messages remain in Reference domain/contract |
| Account IDs and stable position vocabulary | `account` / Account | Balances, positions, freshness, and account events remain in Account |
| Capital IDs | `capital` / Capital | Funding plans, decisions, and workflows remain in Capital |
| Order/fill IDs and stable order instruction vocabulary | `execution` / Execution | Execution lifecycle, plans, and audit models remain in Execution |
| Risk IDs | `risk` / Risk | Budgets, metrics, reservations, and policy behavior remain in Risk |
| Provider/participant identity | `integration` / Integration | SDK payloads and normalized external records remain in Integration boundaries |
| Market source/subscription IDs | candidate `market` / Market | Admit only after confirming stable use across the domain/contract boundary; books and observations remain in Market |
| Price, quantity, money, rate, ratio | `decimal` / shared value mechanics | Wire encodings and field-specific constraints remain in contracts/domains |
| Unix time and duration | `time` / shared value mechanics | Clock access, scheduling, and envelope metadata remain outside primitives |
| Runtime identities and instance ownership | `runtime` / protocol/runtime foundation | `ActorId`, `ProducerId`, `EventId`, and workspace/launch/instance identity are shared values; protocol metadata and frames remain in `kairos_protocol` |
| Sequence, generation, event context, request/idempotency IDs | split by semantics | Sequence and generation remain typed values; event/view context belongs to `kairos_protocol`, request/idempotency IDs stay with their owning business contract |
| Normalized order status | unresolved Integration vs Execution | Do not preserve a generic `trading` group until the meaning is decided |

## Migration checklist

Before moving a type here, record its current callers, prove identical
invariants, migrate one slice, update boundary conversions, delete the old
definition, and add tests for invalid construction and representation
round-trips. Do not leave compatibility wrappers without a current caller.
