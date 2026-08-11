# Module Boundaries

This document is the repository authority for business-module ownership and
cross-module access. Runtime construction follows `bin -> composition ->
application -> services`; `domain` is the infrastructure-free business core.

## Public entry points

- `bin/` parses process input and invokes composition.
- `composition/` selects concrete integrations, persistence, publishers and
  mode-specific implementations.
- `application/` is the only public cross-module use-case boundary.
- `services/` contains private actors, adapters, persistence and process
  internals.
- `domain/` contains entities, value objects and invariants and must not
  depend on SDKs, transports or another business module.

Callers must not import another module's `services/` or private files. A
module may expose a reusable runtime facade under `application/process.rs`,
but mutable business state has one owner, normally its Actor.

## Ownership map

| Module | Owns | Does not own |
|---|---|---|
| Execution | intents, plans, legs, exchange-facing orders, order lifecycle, fills and execution audit | balances, positions, equity or risk reservations |
| Account | balances, positions, equity, freshness, account status, settlement and account-side observations | order entry, cancellation or Execution order state |
| Risk | budgets, authorization decisions and reservations | orders, fills or account balances |
| Market | observations, quotes, order books, subscriptions and market freshness | orders or positions |
| Reference | reference catalog and lifecycle facts | live market or account state |
| Integration | provider authentication, SDK adapters and normalized external facts | business ownership and cross-module orchestration |
| Workspace/System | paths, process lifecycle, instance resources and launch coordination | business state |

Cross-business orchestration belongs in application use cases or system
composition. Concrete provider connections are assembled in composition.

## Execution and Account fact boundary

Execution is authoritative for `intent -> plan -> leg -> order -> fill` and
is the only module allowed to access `OrderEntryConnection` or decide order
state transitions. Account private streams are synchronization and
reconciliation inputs. Execution publishes normalized order/fill facts to
Account; Account applies them idempotently to balances and positions and
never mutates Execution order state.

The same fact is merged by stable identity (`fill_id`, `remote_order_id`,
`order_id`, and event identity). Identical duplicates are ignored. Conflicts
are surfaced as reconciliation state; they must not silently overwrite an
authoritative fact.

## Contract rules

- Public application and contract types are business types, not vendor
  payloads or persistence records.
- Every event carries stream identity, sequence, schema version, producer and
  event time; execution facts also carry intent/plan/leg/order association
  when known.
- Snapshots carry generation and event sequence. Consumers resume from a
  watermark and reload a snapshot when a gap is detected.
- Risk authorization is authoritative; projections from Account, Market,
  Reference and Risk are advisory inputs for planning and freshness checks.

## Static architecture checks

The repository should reject:

1. cross-module imports from `services/` or private files;
2. SDK/vendor payloads crossing an application boundary;
3. a second mutable owner for the same business state;
4. Account order-entry or cancellation APIs;
5. strategy code directly submitting exchange orders or publishing fills.

