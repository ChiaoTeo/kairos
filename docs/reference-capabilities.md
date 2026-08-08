# Reference capability and acceptance matrix

This document is the delivery checklist for the Reference module. A
capability is not complete until its acceptance condition has a focused test.

## Core contract

| Capability | Acceptance condition | Status |
| --- | --- | --- |
| Full-universe synchronization | Provider adapters do not use a concrete underlying as a global sync filter | Implemented |
| Underlying relationship | Option/instrument records retain their own `underlying_instrument_id` | Implemented |
| Provider normalization | Vendor payloads enter Reference through provider-neutral integration records | Implemented |
| Catalog validation | Duplicate IDs, missing references, and invalid product intervals are rejected | Implemented |
| Catalog reconciliation | New, changed, delisted, and relisted markets produce correct state | Implemented |
| Delisted idempotency | A market missing from repeated provider snapshots emits one delisted event | Implemented |
| SQLite recovery | Catalog generation and event sequence survive process restart | Implemented |
| FinancialProduct model | Products can be represented, persisted, queried, and encoded | Implemented |
| Historical lifecycle log | Events are append-only, queryable, and replayable by sequence/time | Implemented |
| Snapshot publication | Catalog, market, and lifecycle snapshots encode all domain fields | Implemented |
| Change publication | Persisted changes remain retryable after publication failure | Implemented |
| Query facade | Application exposes typed ID, text, venue, status, underlying, and as-of queries | Implemented |
| Server control | Health, snapshot, refresh, publish, query, asset upsert, and stop work over the control socket | Implemented |

## Provider coverage

The following providers/products must be checked for full-universe behavior:

- Binance spot, options, USDM futures, COIN-M futures, and equity;
- OKX spot, equity, swap, futures, and options;
- Massive equity and options;
- Hyperliquid;
- workspace composite sources.

Massive Options must fetch all available active contracts, handle pagination,
and derive each contract's underlying relationship from the returned record.

## Required regression scenarios

1. A full options response containing SPY and NVDA produces both records.
2. No Reference CLI, server, Python process configuration, or composition
   config exposes a global `underlying` synchronization option.
3. Active → delisted produces one event; repeated missing snapshots produce no
   duplicate event; delisted → active produces a relist/change event.
4. SQLite restart restores the same catalog and watermarks.
5. Snapshot encode/decode preserves every supported domain field.
6. A publication failure does not make a persisted change permanently
   unpublishable.
