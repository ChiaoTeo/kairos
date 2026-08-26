# Current-view storage architecture

This document specifies the target cross-module storage contract for current business state. It is
governed by [Decision 0034](../decisions/0034-unified-current-view-storage.md). The implementation-status
section distinguishes the accepted target from the KSS snapshot code that still exists during the hard
migration.

## 1. Outcome and scope

Kairos has three cross-process data paths:

| Path | Semantic role | Examples |
| --- | --- | --- |
| Aeron | ordered immutable changes | quote updates, order lifecycle, fills, reservation transitions |
| LMDB current view | indexed state that is true now | current order, active intent, latest quote, balance, open reservation |
| JSON-RPC query/control | history, audit, filtering, calculation, and commands | order audit, catalog search, health, submit/cancel |

FlatBuffers is the canonical cross-language value/event encoding where an owner admits a schema. It is
not a transport and does not imply one aggregate root per owner.

This specification covers owner-published current views. It does not replace:

- Actor/domain state or recovery journals;
- Reference's authoritative SQLite catalog;
- immutable Aeron streams;
- durable audit/history stores;
- provider reconciliation queries;
- same-package Application reads.

## 2. Why an indexed mmap store

A complete serialized owner snapshot has write amplification and poor lookup locality when it contains
independently keyed collections. A custom fixed-slot store avoids indexing but makes Kairos responsible
for allocation, ABA-safe reuse, multi-record atomicity, crash recovery, resize, and a cross-language
atomic ABI.

LMDB provides a page-oriented mmap B+Tree with MVCC, one concurrent writer, non-blocking readers, named
subdatabases, and transactions. Those properties match one owner Actor writing current state while many
local processes read it. See the
[LMDB design paper](https://www.openldap.org/pub/hyc/20111010LDAPCon-MDB.html) and the Rust
[`heed` API](https://docs.rs/heed/latest/heed/).

The baseline deliberately accepts an indexed lookup instead of exposing a stable physical address. A
cached address is unsafe across copy-on-write transactions, remapping, compaction, restart, and schema
replacement. A stable canonical business key is the public identity.

## 3. Ownership and dependency direction

```text
module Actor/Application
  -> module-owned current-view mapper
  -> module Contract key/value schema
  -> platform indexed-view transaction/storage
  -> LMDB environment
```

- The Actor is the only mutable business-state owner and the only logical writer.
- The module contract owns business keys, values, validation, and typed readers.
- Composition selects paths, map size, permissions, and the concrete platform store.
- The platform owns LMDB mechanics and common metadata only. It never owns Order, Balance, Position,
  Reservation, Quote, or another business concept.
- Other business packages consume the owner contract; they never open a private main-package store.
- No application-owned `ViewStore` port or duplicate storage trait is introduced.

The current view is rebuildable. An owner must recover its Actor from its authoritative journal/store
and then reconcile or rebuild the LMDB view. It must never recover business truth from a possibly stale
view merely because the view is durable on disk.

## 4. Resource identity and layout

One owner process uses one environment per typed runtime identity:

```text
<runtime-resource-root>/views/v3/<owner>/<publisher-resource-id>/current.lmdb/
```

The exact safe path is derived by the owner contract and workspace runtime; callers do not scan
directories. The environment identity contains:

- `workspace_id`;
- `launch_id` and `instance_id` when instance-scoped;
- `owner` and `publisher_resource_id`;
- `resource_epoch`;
- `producer_incarnation`;
- storage format version.

The LMDB environment contains a reserved `__kairos_metadata` named database plus owner-declared named
databases. One file may therefore serve several entity families without making them one serialized
object or one business type.

## 5. Common metadata

The reserved metadata database contains fixed keys with typed values:

| Key | Meaning |
| --- | --- |
| `identity` | exact runtime and owner identity |
| `format_version` | platform indexed-view format |
| `schema_set` | exact owner database/value schema versions |
| `resource_epoch` | incompatible replacement epoch |
| `producer_incarnation` | current writer process incarnation |
| `applied_event_sequence` | highest owner event sequence reflected by the committed transaction |
| `committed_at_unix_nanos` | business publication commit time |
| `rebuild_state` | `building`, `ready`, or `failed` with bounded diagnostic code |

An ordinary update changes `applied_event_sequence` and commit time in the same transaction as entity
values. A schema-set, key encoding, comparator, or incompatible value change creates a new resource
epoch. There is no old-version fallback.

### 5.1 Storage versions versus business ordering

LMDB removes KSS active-slot generation, per-file generation joins, application seqlocks, physical-slot
generation/ABA handling, and manual crash-atomic publication. Its read transaction already selects one
internally consistent MVCC database version. Kairos does not expose the LMDB transaction ID as a
business sequence because maintenance, rebuild, and storage implementation details may advance it
without representing one business fact.

The following semantics remain owner contracts and are not delegated to LMDB:

| Value | Why it remains |
| --- | --- |
| storage/schema version | reject bytes whose key or value encoding has changed |
| `resource_epoch` | invalidate an incompatible environment replacement or rebuild |
| `producer_incarnation` | fence and diagnose writer restart/takeover |
| `applied_event_sequence` | correlate a current transaction with the owner Aeron stream and measure lag |
| provider connection/reconnect epoch and participant sequence | reject stale or conflicting external facts before they mutate Actor state |
| entity revision/lifecycle evidence | express owner business concurrency and reconciliation |
| command/idempotency identity | make retry outcomes deterministic |
| bar/event sequence and business time | define domain ordering and retained ranges |

LMDB orders B+Tree keys and transactions; it does not decide whether an exchange event is stale, a fill
conflicts with a terminal order, an Intent transition is legal, or an Aeron event is missing. Those
rules stay in the owner Actor/domain.

## 6. Named database and key rules

A named database is admitted only when it has:

1. one business owner;
2. a canonical key encoding;
3. one value schema and version;
4. a declared current/terminal retention rule;
5. a real reader;
6. a maximum expected cardinality and map-size budget;
7. update, range, and deletion semantics;
8. Rust/Python parity tests.

Keys are deterministic bytes, independent of Rust hashing and process-local memory. Composite keys use
versioned, length-delimited components or a fixed-width canonical encoding. `usize`, native-endian
integers, pointers, and compiler-layout structs are forbidden.

Values contain one entity or one small metadata record. The preferred cross-language value is an
owner-owned FlatBuffer with an exact file identifier/version check. JSON and `serde_json::Value` do not
carry core current state. A reader validates semantic IDs in the value against the requested key.

## 7. Write transaction contract

One accepted Actor transition produces at most one current-view write transaction:

```text
authoritative state transition succeeds
  -> begin LMDB write transaction
  -> put/delete every affected owner key
  -> update __kairos_metadata.applied_event_sequence
  -> commit
  -> publish/drain the corresponding Aeron event according to owner outbox rules
```

The owner may batch consecutive already-authoritative changes into one transaction when their event
sequence and freshness semantics remain explicit. It must not acknowledge a command based solely on a
view write. A failed view commit leaves business state authoritative, marks current-view health
degraded, and fails closed wherever a fresh view is required for trading.

Cross-family changes that must be observed together use named databases in the same environment and one
transaction. Splitting them into multiple environments forfeits this atomicity and requires a concrete
owner justification.

## 8. Read transaction contract

Readers open the exact environment read-only through the owner contract:

1. validate safe path, permissions, environment identity, epoch, and schema set;
2. begin a short read transaction;
3. read metadata and the requested key/range;
4. decode and semantically validate values while the transaction is alive;
5. map to owned application/SDK values unless a callback explicitly bounds the borrowed lifetime;
6. end the transaction promptly.

Long-lived read transactions are forbidden in ordinary clients because they pin old MVCC pages and can
cause file growth. Python obtains reads through the native transport extension; it does not keep raw
pointers, LMDB buffers, or transactions behind Python object lifetimes.

Missing key means `not observed` or `not active` according to the owner contract. It is not an empty
fabricated entity. A schema/identity/epoch mismatch is a hard error, not a transient retry.

## 9. Current entity retention

Current databases retain only facts needed to answer “what is true now?” Examples:

- Execution keeps operational Orders/Intents/Runs, capacity-consuming commitments, active or uncertain
  reservations, and unresolved remote orders. Terminal lifecycle belongs to audit query.
- Account keeps current balances, positions, valuations, status, and provider-observed open orders.
- Risk keeps active policy/limit/circuit state and current reservations; terminal decisions belong to
  audit/history.
- Capital keeps current objectives, demands, routes, plans, reservations, operations, and active alerts
  required by current reads.
- Market keeps latest observations/order books/freshness and retained completed-bar ranges.

Events, diagnostic logs, and unbounded history are not duplicated into current databases.

## 10. Bounded windows

The baseline represents a retained window as indexed records plus one metadata record, not one array
value and not a new ring protocol:

```text
bars database:
  key   = (series_id, bar_sequence)
  value = one Bar

bar_windows database:
  key   = series_id
  value = first_sequence, last_sequence, capacity, completeness
```

Insertion/revision, expired-key deletion, and metadata update occur in one transaction. Range reads use
the ordered composite-key prefix. The number of retained keys is logically bounded; LMDB map size and
MVCC page retention are separately monitored.

Storing `Bar[N]` as one LMDB value is forbidden for a frequently updated window because replacing one
element rewrites the whole value. A custom fixed-slot ring is not part of this baseline. It may be
introduced only by a later Decision backed by end-to-end latency and write-amplification evidence for a
named owner/current consumer.

## 11. Capacity, lifecycle, and health

Every environment declares and monitors:

- configured LMDB map size and current page use;
- named-database cardinality and retained-window counts;
- oldest/longest read transaction where observable;
- last committed event sequence and commit time;
- Actor-to-view publication lag;
- map-full, corruption, permission, identity, and schema errors;
- rebuild progress and last successful verification.

`MDB_MAP_FULL` is a controlled degraded condition. The process must not silently drop a current update,
truncate a family, publish partial success, or resize behind an undisclosed identity. An operationally
supported resize preserves the storage format and is performed under the platform lifecycle; an
incompatible replacement increments `resource_epoch`.

LMDB environments must reside on supported local filesystems. Network filesystems and independently
copied live environment files are forbidden. Backups use LMDB-supported consistent copy/transaction
semantics rather than file-by-file copying while a writer is active.

## 12. Security and process rules

- The owner process alone opens the environment writable.
- Consumers open it read-only with least-privilege filesystem permissions.
- Safe-path checks reject absolute paths, traversal, and unexpected symlinks.
- Secrets, provider tokens, raw signed requests, and private SDK payloads never enter a current view.
- The environment lock files and data files are lifecycle-managed as one resource.
- A process must not mix incompatible LMDB builds/options for the same live environment; Python uses the
  Kairos native reader to keep the implementation uniform.

## 13. Required certification

The platform vertical slice must prove:

1. Rust single writer with concurrent Rust and Python read-only clients;
2. exact key/value and range parity across languages;
3. atomic visibility across multiple named databases;
4. crash before, during, and after commit;
5. restart, rebuild, producer-incarnation change, and resource-epoch replacement;
6. schema, comparator, identity, permissions, and path mismatch rejection;
7. map-full and stale/long-reader behavior with actionable health;
8. deletion, terminal retention, and bounded-window eviction;
9. no current-view read mutates or repairs Actor/event state;
10. benchmark distributions for single-key read, prefix range, batch write, rebuild, and concurrent
   read/write at the owner's admitted scale.

Performance acceptance records p50, p95, p99, and maximum latency plus database size, key/value sizes,
reader count, batch size, filesystem, sync mode, and hardware. A custom storage protocol requires this
evidence to show the indexed baseline is insufficient.

## 14. Owner target layouts

| Owner | Target named databases |
| --- | --- |
| Execution | `orders`, `intents`, `algorithm_runs`, `commitments`, `risk_reservations`, `unknown_remote_orders` |
| Account | `account_status`, `segments`, `balances`, `collateral`, `positions`, `valuations`, `observed_orders` |
| Risk | `policies`, `limit_usage`, `allocations`, `reservations`, `circuits` |
| Capital | `objectives`, `demands`, `source_facts`, `targets`, `routes`, `plans`, `reservations`, `operations`, `alerts` |
| Market | latest family databases keyed by observation identity, plus `bars` and `bar_windows` |

This table fixes ownership and intended access units, not final schema spelling. A named database is
created only with a current publisher and consumer.

Reference remains outside this layout because its contract-owned SQLite catalog already provides the
authoritative point-in-time indexed read surface.

## 15. Implementation status and hard migration

As of 2026-08-26, modules still publish KSS1 double-slot FlatBuffers snapshots. The existing physical
contract is documented as legacy in [`schemas/v2/mmap-contract.md`](../../schemas/v2/mmap-contract.md).
No LMDB current-view implementation is claimed complete by this document.

Migration order:

1. add and certify the concrete platform indexed-view capability and native Python reader;
2. migrate Execution as the first complete owner vertical slice;
3. remove `CurrentExecutionView`, KSS declaration/publication/reader, and connected snapshot decoding in
   the same change;
4. migrate Account, Risk, Capital, and Market owner by owner using the same hard-cut rule;
5. remove KSS current-view transport after its final caller is gone.

There is never a production dual-read comparison or compatibility fallback. Pre-cut tests may construct
isolated fixtures for evidence, but the activated owner exposes exactly one current-state path.
