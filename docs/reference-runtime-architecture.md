# Reference Runtime Architecture

The authoritative data-plane design is
[`reference-sqlite-read-model-design.md`](./reference-sqlite-read-model-design.md).
Reference is a single-writer catalog service with parallel provider ingestion,
durable SQLite current state, an ordered lifecycle log, and consumer-owned
bounded projections.

## Runtime lanes

```text
provider futures -> refresh coordinator -> ReferenceActor -> SQLite WAL
                                                        |-> current rows
                                                        |-> lifecycle rows
                                                        `-> generation/sequence

lifecycle rows -> publication cursor -> Aeron change notifications

consumer -> kairos-reference-contract read-only SQLite reader
         -> module-owned cache
         -> change subscriber
```

The first deployment remains one process. Provider calls are async capabilities
polled concurrently by the process Tokio runtime; they do not create hidden
runtimes or blocking provider threads. `ReferenceActor` serializes mutations.
Only the Reference process opens SQLite read-write or runs migrations.

## Provider rules

- Every provider exposes an independent async query future. One serialized
  refresh owns bounded fan-in, so refresh requests cannot accumulate a second
  provider backlog.
- Provider pagination has a refresh deadline of 150 seconds per run.
- Provider failures use exponential backoff and a bounded open circuit.
- A transport failure preserves the provider's last-known-good facts. It is
  never interpreted as an authoritative empty catalog.
- Shared canonical records are accepted only when their economic fields agree;
  source order is not a conflict-resolution policy.
- Building a replacement provider candidate does not invalidate the completed
  state currently served.
- Sources can be refreshed independently and paused/resumed. Pausing does not
  erase last-known-good records.
- Large pages are staged with their cursor in the same SQLite transaction.
- Providers with large universes use explicit coverage. Massive options are
  scoped by underlying and expiry rather than promoted as an unbounded global
  universe.

Massive options coverage is controlled through:

```text
kairospy reference options-add --underlying SPY
kairospy reference refresh --source massive-options
kairospy reference options-coverage
kairospy reference options-remove --underlying SPY
```

Market WebSocket observations cannot mutate Reference directly. An unknown
symbol may request coverage through the Reference application API; Reference
then performs REST reconciliation and publishes normal lifecycle changes.

## State and durability

SQLite uses WAL mode. Canonical current state is stored in indexed tables for
entities, assets, instruments, listings, markets, financial products, and
execution accesses. `reference_meta` stores schema version, generation, event
sequence, and commit time.

Lifecycle history is append-only in `reference_lifecycle`, with indexed and
bounded reads. Publication keeps one durable `published_sequence` cursor; it
does not duplicate every event payload into a second outbox table.

One changed refresh atomically persists:

1. canonical current rows;
2. newly generated lifecycle rows;
3. generation and event sequence;
4. provider promotion state required by the refresh.

The whole-catalog JSON blob, eight-view manifest, and full-universe mmap
snapshots are retired.

## Publication and recovery

- Event publication is bounded to 1,024 lifecycle rows per batch.
- A batch high watermark is the sequence of its last event.
- The publisher advances its cursor only after the transport accepts the batch
  or intentionally drops a best-effort notification because no subscriber is
  attached.
- Duplicate notification delivery is legal; consumers apply sequence-based
  idempotency.
- A consumer bootstraps its scoped cache from one short SQLite transaction,
  replays later lifecycle rows, then consumes live changes.
- A gap, incompatible event, stale watermark, or failed cache update triggers
  SQLite catch-up or scoped rebuild. There is no catalog-query UDS or mmap
  fallback.

## Consumer projections

- Account resolves only provider instruments observed by configured bindings
  and invalidates its identity cache when Reference generation changes.
- Execution watches the Reference watermark and reads the exact market or
  instrument required by preflight.
- Market rebuilds its active market projection through bounded pages and uses
  lifecycle notifications as recovery hints.
- Strategy and CLI use the Python read-only SQLite contract and bounded result
  limits.

Consumers must use `kairos-reference-contract`; direct service imports and
write-capable SQLite connections are architecture violations.

## Scheduling and backpressure

The control queue is bounded to 64 requests. Provider work is serialized and
timed out. The publisher advances at most one bounded batch per tick or control
request. Consumer read transactions must be short so WAL checkpointing cannot
be retained by idle business work.

## Failure semantics

Provider failure degrades provider freshness without erasing last-known-good
facts. A failed SQLite transaction exposes neither partial current state nor a
new watermark. Publication failure leaves the publication cursor unchanged.
Schema mismatch, database corruption, or an unreadable current row produces an
explicit persistence/contract error and puts consumer projections into error or
recovery.

## Required observability

The runtime exposes provider fetch latency and freshness, refresh and reconcile
duration, SQLite commit duration, current-row counts, database/WAL size,
publication latency, unpublished sequence depth, control queue depth, consumer
projection cardinality, recovery count, and projection watermark age.
