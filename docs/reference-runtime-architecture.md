# Reference Runtime Architecture

Reference is a single-writer catalog service with parallel provider ingestion,
durable incremental commits, isolated publication, and generation-based
read models.

The Reference change stream is a transport-registered resource. Runtime
configuration accepts the canonical stream only; stream changes are a
contract migration across publishers and subscribers, not a local tuning
parameter.

## Runtime lanes

```text
provider workers -> refresh coordinator -> reconcile writer -> SQLite WAL
                                                     |-> event outbox -> Aeron
                                                     `-> snapshot projections -> manifest

query/control server -> bounded command queue
read queries --------> immutable snapshot/read model
```

The first deployment remains one process. The process is not one serial
operation: provider calls run in persistent workers, while the reconcile
writer remains the only owner allowed to mutate the catalog.

## Provider rules

- Every provider has an independent worker and a bounded request slot.
- Provider pagination has a refresh deadline of 150 seconds per run, aligned
  with the current 120-second integration request timeout plus coordination
  overhead.
- Provider failures use exponential backoff and a bounded open circuit before
  recovery probing.
- A provider transport failure uses its last-known-good snapshot.
- A failed request is never interpreted as an authoritative empty catalog.
- Provider results are merged only after all available results for the run
  have been collected.

## State and durability

SQLite uses WAL mode. The current catalog payload stores current state and
watermarks only. Lifecycle history is append-only in
`reference_lifecycle`, with indexed fields and bounded page reads; only the
latest 4096 events are retained in the process read model. Publication work is stored in
`reference_pending_publication`.

One refresh commit must atomically persist:

1. current state;
2. newly generated lifecycle events; and
3. newly generated publication outbox rows.

Outbox readers use bounded batches and acknowledge event IDs individually.
They must never delete the complete outbox merely because one batch was
published.

## Publication rules

- Every successful catalog mutation, including an administrative asset
  upsert, updates the mmap projection.
- A refresh with no generation change does not re-encode snapshots.
- Event publication is bounded to 256 lifecycle events per batch.
- All snapshot views carry the same generation.
- The manifest is fsynced, written last, and atomically renamed after every
  view has been published. Contract readers validate the manifest generation
  and complete view set before consuming snapshots.

## Scheduling and backpressure

The control queue is bounded to 64 requests and rejects new requests
immediately when full. Provider workers use a one-request slot so a slow
provider cannot accumulate an unbounded backlog.

## Failure semantics

Provider failure degrades provider freshness but does not erase its last good
records. Persistence failure leaves the in-memory generation uncommitted and
the refresh fails. Publication failure leaves outbox rows pending and does not
invalidate current state. Consumers recover through a complete snapshot and
then replay events after the snapshot event sequence.

## Required observability

The runtime should expose at least provider fetch latency and freshness,
refresh run duration, reconcile duration, SQLite commit duration, snapshot
payload sizes, publication latency, outbox depth, and control queue depth.
