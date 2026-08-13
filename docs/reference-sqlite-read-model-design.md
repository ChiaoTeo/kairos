# Reference SQLite Read Model Design

This document is the authoritative target for the Reference data plane. Where
older Reference snapshot documentation disagrees with this design, migrate the
implementation and the older document to this design.

The business meaning and cardinality of Instrument, Listing, Market,
MarketDataAccess, and ExecutionAccess are defined by
[`instrument-listing-market-access-design.md`](./instrument-listing-market-access-design.md).
This document remains authoritative for storage ownership, transactions,
publication, and consumer recovery.

## Decision

Reference has one business mutation owner and one durable current-state store:

```text
provider facts -> ReferenceActor -> SQLite WAL transaction
                                      |-> canonical current rows
                                      |-> lifecycle change log
                                      `-> publication cursor -> change stream

consumer composition -> kairos-reference-contract SQLite reader
                     -> bounded module-owned projection
                     -> Reference change subscriber
```

`ReferenceActor` is the only owner allowed to accept and serialize catalog
mutations. SQLite is the authoritative durable current state and history.
Other processes open the database read-only through
`kairos-reference-contract`; they never import Reference service internals or
write Reference tables.

The Reference change stream delivers low-latency deltas. It is not the recovery
source. Consumers recover from SQLite and use generation/event-sequence
watermarks to detect missed, duplicate, or stale notifications.

Full-universe mmap snapshots are not part of the target architecture. They are
removed after every production consumer has migrated to the SQLite contract.

## Ownership and process rules

- ReferenceActor owns mutation ordering, refresh coordination, generation and
  event-sequence advancement.
- Only the Reference process opens a read-write SQLite connection or runs
  migrations.
- Provider ingestion may stage bounded pages, but cannot mutate canonical
  current tables outside the Actor-controlled commit path.
- Market, Execution, Account, Strategy, CLI, and tests read through the public
  contract and own only bounded projections required by their use cases.
- A consumer cache is never an authoritative catalog and must expose its
  Reference watermark and readiness.
- The database must be on a local filesystem. Network filesystems are not a
  supported deployment mode for SQLite WAL.

## Public SQLite contract

The public read schema contains:

```text
reference_meta
reference_entities_current
reference_assets_current
reference_instruments_current
reference_listings_current
reference_markets_current
reference_market_data_accesses_current
reference_financial_products_current
reference_execution_accesses_current
reference_lifecycle
```

`reference_meta` contains the schema version, catalog generation, latest event
sequence, and commit time. Every current table uses the canonical business ID
as its primary key. Fields used for lookup, scoping, lifecycle filtering, or
joins are explicit indexed columns. A row payload may carry the complete
contract DTO, but a single whole-catalog JSON payload is not a public read
model.

`kairos-reference-contract` owns the typed reader, schema-version validation,
query DTOs, row decoding, pagination limits, and watermark semantics. The
service owns migrations and write implementation, but contract tests ensure
that the resulting schema remains readable by Rust and Python clients.

All consumer connections use SQLite read-only/query-only mode, a bounded busy
timeout, and short transactions. Consumers do not hold a read transaction
while waiting for events or performing business work because that would retain
WAL pages and prevent checkpoint progress.

## Commit and publication semantics

One changed refresh atomically commits:

1. canonical current-row upserts and removals;
2. lifecycle change rows;
3. generation and event sequence;
4. provider promotion/sync state required by that refresh.

The publisher reads durable lifecycle rows after its stored cursor and emits
bounded batches. After the transport accepts or intentionally drops a
best-effort notification, it advances its cursor. The lifecycle table is the
durable replay source, so publication must not duplicate every payload into an
unbounded outbox table.

A delta identifies its sequence, generation, operation (`upsert` or `delete`),
record kind, record ID, event time, and the changed contract record or
tombstone. Duplicate delivery is legal. Commands are never inferred from
Reference deltas.

## Consumer bootstrap and recovery

A consumer performs this protocol:

1. Open the contract reader and read its scoped projection in one short SQLite
   transaction, receiving watermark `W0`.
2. Start the Reference change subscriber.
3. Read current SQLite watermark `W1` and replay `(W0, W1]` from the lifecycle
   log, or rebuild the scoped projection when replay is not possible.
4. Apply contiguous live deltas idempotently.
5. Periodically compare the local watermark with SQLite so a final dropped
   notification cannot leave the cache silently stale.

An event-sequence gap, incompatible payload, failed cache update, old schema,
or local watermark ahead of SQLite puts the projection into recovery/error.
There is no mmap or UDS catalog-query fallback.

## Bounded projections

- Market caches only records selected by active subscription intents and the
  provider/source facts needed to activate those subscriptions.
- Execution caches trading rules and execution access for markets referenced
  by configured routes or orders. A cache miss performs a contract read before
  preflight; it never guesses rules from symbols.
- Account caches provider-instrument-to-canonical mappings for configured
  account bindings and products.
- Strategy caches only its declared universe. Research and CLI callers use
  bounded pages.

No generic cache manager, registry, or second Reference facade is introduced.
Each business owner defines and tests its own projection semantics.

## Incremental writer target

The steady-state Actor must not hold or clone a complete `ReferenceCatalog`.
Provider pages are normalized into bounded staging/source-fact rows. Promotion
calculates changes for the provider or coverage scope, validates canonical
conflicts, updates current rows, and appends lifecycle rows in bounded work.
Memory therefore scales with page and change-batch size rather than total
catalog cardinality.

During migration, whole-catalog JSON and mmap may be dual-written only while a
named production consumer still requires them. Dual-write is not the final
state and must not become an automatic fallback.

## Migration slices and exit criteria

1. Add schema metadata, normalized current tables, read-only Rust/Python
   readers, old-database backfill, and dual-write equality tests.
2. Move canonical persistence and diffing to normalized rows; remove complete
   catalog cloning from refresh/promotion.
3. Publish from lifecycle rows with a publisher cursor; remove payload-copying
   pending outbox rows.
4. Migrate Account resolver, Execution preflight, Market projection, then
   Strategy/CLI. Each slice uses shadow comparison before deleting its mmap
   path.
5. Delete Reference mmap writers/readers, manifests, slot-size configuration,
   whole-catalog JSON state, and obsolete tests/docs.

The migration is complete only when:

- production searches for `ReferenceMmap` and `snapshots/reference` are empty;
- Reference is the only SQLite writer and consumers open query-only readers;
- all consumer caches recover from gap, duplicate, restart, and stale-watermark
  tests;
- a million-record fixture refresh and scoped read remain within the configured
  process memory budget without full-catalog clones or snapshots;
- focused Rust/Python tests, workspace tests, formatting, architecture checks,
  and `git diff --check` pass.
