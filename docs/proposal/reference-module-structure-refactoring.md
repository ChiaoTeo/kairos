# Reference Module Refactoring

## Status

This document is the migration authority for the Reference module. It records
the target boundary, the concrete problems being removed, and the exit
criteria for each slice. A migration slice is incomplete until its old path is
deleted.

R1 through R5 are implemented. R2 intentionally keeps the transport-specific
server loop in `bin/` as allowed by the repository architecture; reusable
catalog, query, synchronization, and publication behavior lives behind
Application.

## Mission

Reference is Kairos's authority for canonical tradable identity, effective
reference relationships, and provider access addresses. It converts
Integration-owned provider facts into stable Reference-owned identities and
publishes revisioned facts that other business modules can consume without
knowing a provider payload or the Reference persistence schema.

Reference owns:

- canonical assets, instruments, venues, listings, and tradable markets;
- market-data and execution access addresses;
- effective lifecycle, provenance, catalog revision, and event sequence;
- provider-fact conflict policy, last-known-good promotion, and catalog
  coverage state;
- typed current projections and lifecycle events.

Reference does not own:

- provider authentication, HTTP/WebSocket behavior, or provider payloads;
- market observations, order books, subscriptions, or freshness;
- execution routing policy or order lifecycle;
- account facts;
- workspace paths, process resources, or transport implementations;
- research-file preparation or corporate-action history.

## Ownership and invariants

`ReferenceActor` is the only mutable catalog owner and the only component that
may advance catalog revision or lifecycle sequence. Domain reconciliation is
the only implementation of canonical merge, validation, tombstone, revision,
and lifecycle-event rules. SQLite provides typed candidate reads and atomic
writes; it does not decide business outcomes.

One catalog commit has these semantics:

1. Completed provider staging is included in a candidate provider-fact view.
2. Domain reconciliation produces the next canonical state and typed change
   events from the previous state and candidate facts.
3. Provider promotion, canonical rows, lifecycle history, publication outbox,
   revision, and event sequence commit in one SQLite transaction.
4. Publication sends the exact FlatBuffers bytes committed for that event. It
   never reconstructs an old event from a newer current row.
5. A catalog revision advances exactly once when at least one canonical record
   changes. Event sequence advances once per committed lifecycle event.
6. Operational provider health, cursor, and coverage changes do not advance
   catalog revision unless they change canonical facts.

## Canonical model decisions

- `Instrument` is an economic contract independent of a provider surface.
- `Listing` is an optional venue listing. A tradable market is not required to
  invent a securities listing.
- `Market` is a canonical tradable venue/product surface for an instrument.
- `MarketDataAccess` is a provider address used to observe a Market.
- `ExecutionAccess` is a provider address used to submit for an Instrument or
  Market. Smart-route selection and destination policy belong to Execution.
- Reference participants use a closed kind. Arbitrary `entity_type` strings
  and parallel unused Provider/Broker/Exchange wire models are not both kept.
- Provenance is metadata and does not silently become part of canonical
  identity equality.
- Missing provider rows do not erase canonical identity accidentally. A real
  lifecycle withdrawal becomes a retained inactive/delisted record or an
  explicit typed tombstone.

## Application boundary

The public application exposes business commands and typed results:

- synchronize all configured sources or one source;
- change explicit discovery coverage;
- query canonical records and lifecycle history;
- obtain consumer-oriented current projections;
- inspect process/catalog health separately from catalog state.

Administrative writes use command types rather than accepting domain structs
as transport payloads. Pending-event acknowledgements and persistence records
are process internals, not public business use cases.

## Process contract

The independently depend-able contract owns typed cross-process models and
FlatBuffers codecs and the only read-only SQLite adapter. Reference remains
the only database writer and table names remain private to that adapter.
Consumers receive bounded typed projections tailored to their current callers:

- Market: active Market plus MarketDataAccess and watermark;
- Execution: active ExecutionAccess and watermark;
- Account: access identity to canonical Instrument/Market and watermark;
- CLI/diagnostics: typed paginated query results.

A full workspace universe, provider health, option coverage, and lifecycle
tail are not bundled into every consumer snapshot.

## Target source layout

```text
src/
  bin/                 argument/input adaptation and invocation only
  composition/
    providers/
      mod.rs            narrow exports and shared access mapping
      fan_in.rs         concurrency, last-known-good, health, and control
      binance.rs        Binance sources and canonical mapping
      hyperliquid.rs    Hyperliquid sources and canonical mapping
      massive.rs        Massive sources, scoped discovery, and mapping
      okx.rs            OKX sources and canonical mapping
    config.rs           provider/product configuration mapping
  application/
    app.rs              public commands and use-case facade
    commands.rs         application-owned administrative write requests
    control.rs          process-control request adaptation
    queries.rs
  services/
    actor.rs
    sqlx_storage.rs     concrete canonical/provider persistence
    publication.rs     exact typed outbox encoding
    source.rs          internal multi-provider source capability
  domain/
    catalog.rs
    entities.rs
```

This layout does not justify additional crates, ports, gateways, managers, or
test-only dependency-inversion traits.

## Non-trivial boundary justification

### Contract-owned typed SQLite queries

1. They solve current business consumers importing the private SQLite schema
   and remove duplicated mmap copies of the canonical catalog.
2. Current callers are Market, Execution, and Account.
3. The old mmap contract was insufficient because it republished the same
   committed facts into three files and imposed a second state-publication
   lifecycle on data already committed transactionally.
4. The simplest implementation is a contract-owned client that opens the
   Reference database read-only and exposes typed, consumer-oriented queries
   from one SQLite snapshot transaction. Business modules never know table
   names and never issue SQL themselves.
5. The three mmap resources, `ReferenceViewReader`, and old full-universe view
   path are removed after migration.
6. Architecture tests forbid SQL and Reference table names outside the
   contract adapter; contract tests verify typed query watermarks and snapshot
   consistency.

### Typed transactional publication outbox

1. It solves event payloads being rebuilt from a newer or missing current row.
2. The current caller is the Reference event publisher consumed by Market.
3. A publication cursor alone cannot reproduce revision-faithful history.
4. Store the exact FlatBuffers event bytes in the catalog transaction and
   publish those bytes until acknowledged.
5. `record_payload_json` as a publication adapter and publisher current-row
   lookups are removed.
6. Tests update a record twice before publication, remove a record, restart,
   and prove ordered byte-identical delivery for both revisions.

## Migration slices and exit criteria

### R1: Reconciliation and publication correctness

- one typed Domain reconciliation path;
- one revision/sequence rule for every record kind;
- explicit retained lifecycle or typed tombstone semantics;
- revision-faithful transactional publication outbox;
- SQL contains persistence operations, not canonical business decisions;
- old normalized SQL reconciliation and JSON publication adapter deleted.

### R2: Internal module layering

- concrete provider clients and mapping move to composition;
- reusable business behavior lives in Application; the remaining server loop
  is transport-specific lifecycle and therefore stays private to the binary;
- application no longer has private generic bounds;
- test-only store/source abstraction layers are deleted;
- main library exposes application as its business boundary.

### R3: Consumer contracts

- Market, Execution, and Account use contract-owned typed SQLite queries;
- no business module imports `rusqlite`, Reference table names, persistence
  records, or the Reference SQLite schema version;
- all Reference mmap views and publishers are retired;
- Aeron remains a change notification; consumers recover by querying the
  authoritative SQLite watermark and typed current facts.

### R4: Model convergence

- participant kind is closed and relationship validation is typed;
- Listing is optional for non-listing markets;
- access records contain addresses, not Execution routing policy;
- unused FinancialProduct and shadow Provider/Broker/Exchange paths are either
  backed by a current production caller or deleted.

### R5: Dataset separation and cleanup

- option discovery uses normal Reference synchronization;
- research exports and cash dividends leave the public Reference facade;
- obsolete CLI commands, schemas, migrations, compatibility fields, and tests
  are deleted.

## Completion evidence

Completion requires focused Reference and contract tests, consumer tests,
workspace tests, Python tests, formatting, diff checks, crate-layout checks,
architecture searches, and explicit searches proving removal of consumer
SQLite imports, JSON publication adapters, duplicate reconciliation paths,
generic application traits, and obsolete schemas.
