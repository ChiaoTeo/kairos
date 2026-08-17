# Reference query design

## Ownership and boundary

Reference owns the canonical catalog and the Rust Reference Actor remains its only mutable state
owner. The Python query surface is read-only: `ReferenceClient` owns knowledge of the SQLite
projection, while `ReferenceApplication` maps contract records into strategy-safe business
models. Strategy code and CLI commands do not import table names or issue arbitrary SQL.

The current callers are strategy processes through `ctx.reference` and the `kairospy reference`
CLI. Both are composed with the same concrete contract client; no dependency-inversion protocol,
registry, or alternate state owner is introduced.

## Why a read session exists

Previously each query opened a separate connection, collection queries loaded full tables before
filtering, and `require_market(market_id)` searched only the first 10,000 ordered markets. That
could report an existing market as missing and could combine records from different catalog
generations during one strategy decision.

`ReferenceReadSession` is the smallest concrete boundary that addresses those problems. It holds
one read-only SQLite transaction, pins `generation` and `event_sequence`, and implements the same
indexed filters used by one-shot client calls. `ReferenceApplication.snapshot()` scopes that
session for strategy code. It owns no business state and has no implementation trait.

The old Python-side execution-access filtering and capped scan used for market ID resolution are
removed. Exact and batch IDs, filters, limit, and offset are now bound SQLite parameters.

## Query inventory

The application exposes typed entity, asset, instrument, listing, market, execution-access, and
market-data-access queries. Each record family supports exact or batch IDs; collection queries
support their indexed business selectors and pagination. `option_chain` combines the canonical
option type, underlying, expiry range, and right filters without introducing a second option
catalog.

The CLI exposes the same inventory, including `market-data-accesses` and `option-chain`. Repeated
ID options perform batch lookup. It intentionally does not expose arbitrary SQL because the table
layout is a contract implementation detail.

## Evidence

- A catalog larger than 10,000 markets verifies direct ID lookup beyond the former cap.
- A WAL-backed concurrency test verifies that a read session retains its original generation and
  rows while another connection commits a newer generation.
- Contract tests cover typed records, batch lookup, pagination, option chains, both access kinds,
  CLI filters, invalid bounds, and not-found behavior.
- SQLite query-plan checks use the market-symbol, option-underlying, execution-provider, and
  market-data-market indexes on the production projection.
