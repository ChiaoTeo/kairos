# Decision 0013: Read Model and Query Naming

- Status: Accepted
- Date: 2026-08-24
- Scope: Business contracts, current views, catalogs, dependency state, SDK, CLI, persistence
- Supersedes: Generic terminology in Decisions 0003, 0007, 0008, and 0011

Decision 0034 replaces snapshot publication as the default current-view mechanism with an indexed LMDB
store. This Decision's semantic names remain valid.

## Context

The repository used one generic term for unrelated things: request queries,
current-view readers, SQLite catalog pages, consumer-side cached facts, deterministic
calculations, normalized provider records, and notification submissions. The
name did not tell callers who owned the data, whether the result was current or
historical, how it was bounded, or which consistency guarantees applied.

## Decision

Public and internal names state the actual read semantics:

- owner-handled bounded reads are `Query` values;
- indexed publications are `CurrentView` or `LatestView` values;
- one immutable consistent read is a `Snapshot`;
- consumer-held external facts are `DependencyState`;
- Reference's persistent searchable data is a `Catalog`;
- durable historical reads are `HistoryQuery` or `AuditQuery`;
- boundary conversion uses `map`, `decode`, or `from` names;
- deterministic analytics use `Calculation` or a business result name.

RPC, LMDB, SQLite, and event streams remain transport or storage mechanisms,
not business data categories. Reference exposes `control + catalog + event`;
Account, Market, Execution, Risk, and Capital expose their applicable
`control + view + event` capabilities.

The migration is a hard cut. Public aliases, old log names, old journal
discriminators, and dual-write paths are not retained. The active workspace
Reference database was migrated in place; runtime code accepts only the new schema.

## Consequences

- A read entry point communicates its owner, consistency boundary, and
  boundedness without relying on a generic architecture label.
- Reference consumer snapshots use distinct types, so an omitted collection
  cannot be confused with an authoritative empty collection.
- Execution's refreshed foreign facts are explicitly private dependency state.
- Current views remain bounded and cannot claim to provide complete audit
  history.
- A repository check prevents the removed generic vocabulary from returning,
  except for the local relational column-selection term.

## Implementation anchors

- Naming guide: `docs/architecture/read-model-and-query-naming.md`
- Reference catalog contract: `crates/modules/reference/contract/src/catalog/`
- Execution dependency state: `crates/modules/execution/src/services/dependencies/state/`
- Vocabulary check: `scripts/check/check_read_model_vocabulary.py`
