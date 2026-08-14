# V2 mmap current-view contract

Status: target external mmap contract for v2 current views.

This document defines the physical resources exposed by each business owner.
The FlatBuffers roots define bytes inside a slot; this document defines which
files exist, how readers discover them, their key/cardinality, and how they are
created, replaced, read, and retired.

Reference is deliberately excluded. Its authoritative current state is the
point-in-time SQLite contract. A Reference lifecycle event may tell a consumer
that a revision exists, but there is no Reference v2 mmap catalog.

## 1. Boundary and terminology

A **publisher** is one concrete owner Actor/process. A **resource** is one mmap
file containing one KSS envelope and exactly one FlatBuffers current-view root.
Resource discovery is not a generated JSON artifact. Typed runtime/component
configuration supplies the publisher root, and `MarketViewKey` derives each
resource filename deterministically. The publisher must not create
`manifest.json` files, and there is no `schemas/v2/*.json` mmap manifest
schema.

The public boundary remains the owning module's contract/application API:

```text
caller
  -> module current-view reader
  -> typed component/runtime resource root
  -> one or more mmap resources
  -> validated FlatBuffers roots
  -> owned application values
```

Callers do not scan snapshot directories or retain generated FlatBuffers table
references; they use the typed reader for the requested resource key.

## 2. Scope roots and paths

Workspace-shared publishers use the workspace root. Instance publishers use
the launch-instance root.

```text
<scope-root>/
  snapshots/
    v2/
      <owner>/
        <publisher-resource-id>/
          <resource-id>.e<resource-epoch>.mmap
```

Examples:

```text
# Shared Market runtime
<workspace>/snapshots/v2/market/market-shared/market-...-quote-none.e1.mmap

# One instance Account process
<instance>/snapshots/v2/account/account-7f83a921/current.e1.mmap

# Instance Risk and Execution
<instance>/snapshots/v2/risk/risk-default/current.e1.mmap
<instance>/snapshots/v2/execution/execution-default/active-intents.e1.mmap
```

`publisher-resource-id` and filenames are System/composition-owned safe
resource identifiers. They are not arbitrary business IDs. The application
reader receives the exact publisher root from the typed runtime boundary and
constructs only the requested `MarketViewKey` resource path. Readers never
infer a Market snapshot path from an Execution resource or fall back to an aggregate service snapshot filename.

## 3. Resource discovery and identity

The runtime boundary carries the publisher root and the requested view key;
it does not carry or generate a JSON manifest. A resource is discoverable when
its deterministic path exists and its KSS header and FlatBuffers metadata
validate against the requested key.

- `resource_id` is stable while the logical resource exists.
- `resource_epoch` increases when slot size, schema root, shard count, or file
  identity changes.
- `resource_path` is a safe filename below the publisher root; absolute paths
  and traversal are rejected.
- file identifier, view key, shard metadata, capacity, and runtime identity
  must match the decoded resource.
- a resource is visible only after its first complete KSS generation is
  readable.

## 4. Physical envelope

The first v2 migration uses the existing KSS1 double-slot envelope:

```text
offset 0   magic KSS1
offset 4   envelope version u16
offset 6   slot count u16 = 2
offset 8   fixed slot size u32
offset 12  active slot u8
offset 16  published generation u64
offset 24  slot lengths[2] u32
offset 32  slot generations[2] u64
offset 64  slot 0 bytes
offset 64 + slot_size  slot 1 bytes
```

Each active slot contains exactly one complete FlatBuffers root with the file
identifier registered for that resource. Payload bytes never span resources.

The KSS header generation, selected slot generation, and decoded
`ViewMetadata.generation` must be equal. The decoded resource identity,
resource epoch, view key, owner, and runtime identity must match the requested
application scope and typed runtime boundary.

KSS1 readers return an owned payload copy. V2 does not claim zero-copy lifetime
semantics. A future leased zero-copy envelope is a transport-version change,
not a schema reinterpretation.

### 4.1 FlatBuffers, copies, and borrowing

FlatBuffers is the byte-level payload contract, not an authorization to expose
process-local Rust addresses. A pointer or reference is meaningful only in the
virtual address space and lifetime of the process that created it. No mmap
JSON discovery document, KSS header, FlatBuffers table, application result, or cross-process
API may contain or transfer a Rust pointer as an object reference.

The v2 baseline writer path is:

```text
owner current-view model
  -> FlatBufferBuilder-owned complete buffer
  -> borrowed finished_data(): &[u8]
  -> one copy into the inactive KSS slot
  -> generation switch
```

That final copy is deliberate: KSS1 owns a fixed inactive slot, while
`FlatBufferBuilder` owns a backwards-built scratch allocation. Contract
publishers must not add an intermediate `finished_data().to_vec()` followed by
an envelope `Vec::clone()`. The migration target is therefore one full payload
copy after FlatBuffers construction, not a claim of zero-copy publication.

Building directly in an mmap slot is deferred. It would require a mmap-backed
FlatBuffers allocator, a payload offset in the physical envelope, rollback on
encoding failure, and exclusive guarantees for the inactive slot. It is a
transport optimization to adopt only after publication benchmarks show that
the single copy, rather than flush or encoding work, is material.

The KSS1 reader path is:

```text
stable double-slot read
  -> one mmap-to-owned-buffer copy
  -> FlatBuffers tables borrow from that owned buffer
  -> validated owned application values
```

The owned-buffer copy is required by the current lifetime contract. A writer
may flip to the other slot and then reuse the reader's previously selected slot
on its next publication. Returning a long-lived table or `&[u8]` into that slot
would therefore permit the bytes to change while the caller reads them. A true
zero-copy reader needs an explicit lease/pinning or hazard protocol and is a new
KSS transport version. A closure alone is insufficient because the writer can
publish twice during the closure.

Current implementation and v2 transport target:

| Contract | Current publication behavior | Target |
| --- | --- | --- |
| Account | builder -> `Vec` -> envelope clone -> mmap copy | remove both intermediate owned-buffer copies |
| Risk | builder -> `Vec` -> envelope clone -> mmap copy | remove both intermediate owned-buffer copies |
| Execution | builder -> `Vec` -> mmap copy | publish borrowed builder bytes directly |
| Market aggregate/views | builder -> `Vec` -> mmap copy | publish borrowed builder bytes directly |
| Market order book | builder bytes -> mmap copy | already matches the KSS1 write target |
| Every KSS1 reader | mmap -> owned `Vec` | retain until a measured KSS2 lease design exists |

Benchmarks must separately measure FlatBuffers construction, payload copy,
`flush_range`, and owned application mapping before introducing allocator or
lease complexity.

## 5. File epoch and resize safety

A published mmap file is never truncated, resized, or repurposed in place.
Existing `SharedSnapshotWriter::create(path)` behavior is not valid for an
already advertised v2 path.

Creation/replacement follows:

1. choose a new `resource_epoch` and therefore a new filename;
2. create the fixed-size KSS file with owner-only write permissions;
3. publish generation 1 with a complete v2 payload;
4. reopen/read/validate it through the contract reader;
5. atomically make the new resource path visible through the typed runtime boundary;
6. readers resolve the new path and remap the resource;
7. the previous file is retired after the configured grace period.

On Unix, unlinking an old file does not invalidate an existing mapping, but a
reader must resolve the typed resource boundary periodically and before each
new application query after an epoch change. No correctness rule depends on inode
replacement behavior.

Slot size and Market shard count are immutable for a resource epoch. Capacity
growth creates a new epoch; it never mutates the current file layout.

## 6. Publication and read semantics

The owner publishes only from its current-view model, never from event records
or persistence rows.

Publication rules:

- one writer per resource;
- encode and semantically validate the entire FlatBuffers payload first;
- reject empty, oversized, over-row-limit, or incomplete payloads;
- write the inactive slot and publish it through the KSS generation switch;
- update neither runtime resource metadata nor resource epoch for an ordinary generation;
- retain the previous active generation if publication fails;
- mark publisher health degraded and fail closed for trading readiness when a
  required resource can no longer publish.

Initial v2 resources publish only `ViewCompleteness.COMPLETE`. `PARTIAL` is
reserved for a future root whose consumer and missing-population semantics are
explicitly designed. It must not be used to squeeze an oversized collection
into a slot.

Read rules:

1. resolve the owner through typed component/runtime metadata;
2. derive and validate the exact resource path from the requested view key;
3. select exact Market resources and shards;
4. open KSS resources read-only;
5. perform the stable double-slot read;
6. verify KSS generation and FlatBuffers identity/metadata equality;
7. validate row bounds, uniqueness, canonical IDs, and requested scope;
8. return owned application values.

A reader retries a concurrently changing slot a bounded number of times. A
resource/root/identity mismatch is not retried as transient corruption.

## 7. Generation and consistency

Generation is local to `(resource_id, resource_epoch)`:

- it starts at 1;
- it increases only when that resource publishes a new complete image;
- values from different resources or epochs are not comparable;
- it is not an event position or a cross-resource transaction number.

There is no atomic snapshot across Market families, Account and Risk, or the
two Execution resources. A caller records the generation and `as_of` of every
resource it used. If a use case requires an atomic cross-resource answer, it
uses an owner query/command instead of inventing a mmap join.

Rows inside vectors are encoded in canonical key order. Deterministic ordering
supports reproducible fixtures and avoids generation changes caused only by
map iteration order.

## 8. Market mmap resources

Market is the only initial owner whose current views support physical
sharding. Quote, Bar, and Greeks are separate resource families because their
keys, payloads, update cadence, and consumers differ. Trade remains event-only.

| Family | Root | Row key | Resource payload |
| --- | --- | --- | --- |
| Quote | `MLQ2 QuoteLatestView` | `(source_id, market_id)` | latest canonical quote per key |
| Quote | `MLQ2 QuoteLatestView` | `(source_id, market_id)` | one latest value |
| Completed Bar | `MBW2 BarWindowView` | `(source_id, market_id, bar_spec_id)` | bounded completed-bar window per key |
| Greeks | `MLG2 GreeksLatestView` | `(source_id, market_id)` | one latest value |
| Order Book | `MLO2 OrderBookLatestView` | `(source_id, market_id)` | one latest synchronized book per key |
| Greeks | `MLG2 GreeksLatestView` | `(source_id, market_id)` | latest Greeks value per key |
| Order Book | `MLO2 OrderBookLatestView` | `(source_id, market_id, instrument_id)` | one latest synchronized or explicitly unsynchronized book |
| Freshness | `MLF2 MarketFreshnessLatestView` | `(source_id, market_id, data_kind)` | one latest age, sequence evidence, and freshness status |

Each resource is one shard and carries `shard_id` and `shard_count`. The values
must match its typed resource metadata. Shard count is a power of two and immutable for
the resource epoch.

The shard input is the UTF-8 canonical row key with components length-prefixed
before concatenation. Its shard is:

```text
u64_be(SHA-256(canonical_row_key)[0..8]) & (shard_count - 1)
```

Rust and Python must share golden key-to-shard fixtures. A reader computes only
the shards needed by its requested keys; reading every Market file is not the
default application behavior.

V2 starts with `shard_count = 1`. Increasing it requires measured payload size
or publication-cost evidence, creates a new resource epoch/set, and updates the
typed runtime resource metadata atomically. The schema supports sharding without assuming it is
always beneficial.

Market publishes dirty shards at its configured snapshot interval, not once
for every incoming tick. This is a bounded current-state publication policy,
not event batching; Aeron facts remain independent.

OrderBook current views are published only by the Market Actor after applying a
provider snapshot or a contiguous delta range. `synchronized = false` is an
explicit degraded image and must be rejected by execution preflight. A
freshness view is independently generated because its rows and cadence are not
the same as the observation current views. Its `event_sequence` field is audit
evidence only and is never a resumable stream cursor.

`COMPLETE` means every current row held by Market for the declared family and
shard is included. It does not promise that every subscribed market has
produced data. A consumer requesting a missing key receives `not observed`,
not a fabricated empty quote.

## 9. Account mmap resources

One Account process/publisher owns one canonical account and may own several
segments. It exposes two independent resources:

| Resource | Root | Logical content |
| --- | --- | --- |
| `account-current` | `AAV2 AccountCurrentView` | account identity plus all balance/collateral/position/valuation/status segments |
| `observed-orders-current` | `AOV2 ObservedOrdersCurrentView` | provider-observed open orders grouped by segment |

The current-view root carries `account_id` once. Every segment row belongs to
that account and has a unique `segment_key`. A resource containing multiple
account IDs is invalid.

Observed orders are split because reconciliation cadence and consumers differ
from balance/position reads. They are Account observations, not the Execution
order ledger. A reader requiring both records the two independent generations;
it does not assume they form one transaction.

Account publishes after its owned state transition is accepted. Where the
transition is durable, persistence succeeds before publication. Refresh bursts
may coalesce into one generation, but the final image must be complete.

## 10. Risk mmap resources

One Risk Actor exposes one `RXV2 RiskLatestView` resource containing:

- the active policy version;
- complete structured policy scopes and current limit usage;
- active reservations only;
- current circuits.

Consumed, released, and expired reservations move to Risk query/audit storage
and do not grow the mmap indefinitely. The active-reservation set is bounded by
configured mailbox/admission capacity and TTL policy.

Risk publishes only after the authoritative mutation is journaled. Failure to
publish does not roll back an acknowledged durable decision, but it marks the
view stale/degraded; Execution must use the authoritative command path for a
new reservation and must not infer capacity from a stale mmap alone.

## 11. Execution mmap resources

One Execution Actor exposes:

| Resource | Root | Logical content |
| --- | --- | --- |
| `active-intents` | `ECI2 ActiveIntentsView` | non-terminal intents and their current plans/legs |
| `active-orders` | `ECO2 ActiveOrdersView` | non-terminal exchange-facing orders |

Terminal intents, orders, and fills are query/audit records. They do not remain
in mmap merely to provide history.

The two resources are deliberately independent: order updates are more
frequent, and an order-only reader should not copy every intent/plan. Their
generations cannot be joined. A caller needing an atomic Execution audit uses
an Execution query.

Execution publishes after its state-store transition succeeds. A fill is an
event/query fact and does not create a third current-view resource.

## 12. System mmap resources

The System monitor exposes:

| Resource | Root | Logical content |
| --- | --- | --- |
| `health-current` | `SHV2 SystemHealthCurrentView` | current Actor and connection health |
| `alerts-current` | `SAV2 AlertsCurrentView` | active and acknowledged alerts |

Resolved alert history and operation chains are query/audit data. System mmap
may reference owner IDs but never substitutes for Account, Market, Risk, or
Execution truth.

System resources are operational. Their absence does not authorize trading;
readiness policy is evaluated by application/System composition using the
required business resources.

## 13. Capacity contract

Slot sizes and row limits are configuration validated at publisher startup and
carried by the typed runtime boundary. They are not hidden constants in readers.

Initial configuration uses one Market shard per family. Composition estimates
the maximum encoded size from configured universe/Actor limits plus explicit
headroom. A publisher refuses startup when configured bounds cannot fit its
slot.

Capacity policy:

- no vector is silently truncated;
- no resource publishes `PARTIAL` on overflow;
- no slot resizes in place;
- normal operation keeps a configured headroom threshold;
- crossing the warning threshold emits operational health before hard
  overflow;
- resizing or resharding is a supervised new resource epoch;
- sharding, caching, or larger slots require payload-size/publication-cost
  evidence rather than directory uniformity.

Two slots means each resource consumes approximately `64 + 2 * slot_size`
bytes. Capacity planning counts both slots and every Market shard.

## 14. Permissions and validation

- resource directories and typed runtime metadata are workspace/instance owned;
- only the owner process has write access to its mmap files;
- consumers open files read-only;
- resource paths are resolved below the validated publisher directory;
- symlink/path traversal checks are applied before mmap;
- workspace, launch, and instance identity in payload metadata must match the
  caller's resolved runtime scope;
- an account reader additionally verifies requested `account_id`;
- a Market reader verifies every row hashes to the advertised shard;
- wrong root/file identifier is a hard contract error.

## 15. Required mmap tests

Every active resource requires:

1. first-generation creation and typed resource registration;
2. stable reads while the writer switches slots;
3. KSS/header/payload generation equality;
4. wrong file identifier, resource ID, epoch, view key, and runtime identity
   rejection;
5. row uniqueness, sort order, bounds, and Market shard validation;
6. oversized payload leaves the previous generation active;
7. new file epoch and typed resource-path switch without in-place truncate;
8. stale resource metadata and missing resource failure behavior;
9. Rust writer to Python reader parity;
10. application APIs return owned values and do not expose mmap/generated
    types;
11. proof that mmap reads do not create, advance, or repair event cursors;
12. owner-specific terminal/history exclusions.

## 16. Required implementation changes

V2 migration must not reuse the existing one-service/one-snapshot assumption.
It requires:

- workspace APIs for a publisher resource directory and typed resource paths;
- component endpoint metadata carrying the Market publisher root;
- a generic validated resource-key reader;
- epoch-specific KSS file creation without truncating an advertised path;
- business contract readers selecting semantic resources and Market shards;
- owner publishers enforcing configured capacity and payload metadata equality;
- tests proving independent readers resolve only their declared resource.

These are transport/composition responsibilities. They do not create a global
snapshot manager or move business ownership out of the module Actors.
