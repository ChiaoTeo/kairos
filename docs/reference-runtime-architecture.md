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
provider futures -> refresh coordinator -> reconcile writer -> SQLite WAL
                                                     |-> event outbox -> Aeron
                                                     `-> snapshot projections -> manifest

query/control server -> bounded command queue
read queries --------> immutable snapshot/read model
```

The first deployment remains one process. Provider calls are async capabilities
polled concurrently by the process Tokio runtime; they do not create hidden
runtimes or blocking provider threads. The reconcile writer remains the only
owner allowed to mutate the catalog.

## Provider rules

- Every provider exposes an independent async query future. One serialized
  refresh owns the bounded fan-in, so refresh requests cannot accumulate a
  second provider backlog.
- Provider pagination has a refresh deadline of 150 seconds per run, aligned
  with the current 120-second integration request timeout plus coordination
  overhead.
- Provider failures use exponential backoff and a bounded open circuit before
  recovery probing.
- A provider transport failure uses its last-known-good snapshot.
- A failed request is never interpreted as an authoritative empty catalog.
- Provider results are merged only after all available results for the run
  have been collected.
- Entity, Asset, and Instrument are canonical records. They do not carry one
  arbitrarily selected provider source; provenance remains on concrete
  Listing, Market, and access projections until a provenance set is modeled.
- Shared Instruments are reconciled only when their canonical economic fields
  agree. Availability is active when any listing is active. A same-ID record
  with different canonical fields rejects the refresh instead of using source
  order as a hidden winner.
- Provider last-known-good snapshots are eligible for fallback only when they
  use the current canonical normalization shape. An obsolete persisted shape
  must be refreshed successfully before promotion.
- Building a replacement paginated snapshot does not invalidate a completed
  snapshot already being served. The provider remains ready until the new
  candidate is atomically promoted; `syncing` means the first usable snapshot
  has not completed yet.
- A source can be refreshed independently (`POST /v1/refresh?source=<id>`) or
  paused/resumed (`POST /v1/sources/pause|resume?source=<id>`). A paused
  provider is not polled, but its last-known-good snapshot remains in the
  catalog. This makes a credentialed or slow provider operationally isolated
  from Binance, OKX, and the other sources.
- Large-provider pages are staged individually in SQLite with their cursor in
  the same transaction. This bounds normal incremental progress, but it is
  not a license to promote an unbounded universe: a provider whose completed
  catalog exceeds the Reference process/snapshot budget must use a managed,
  explicitly scoped coverage model before it is enabled for routine refresh.

For Massive stock options, the concrete operational commands are:

```text
kairospy reference options-add --underlying SPY
kairospy reference refresh --source massive-options
kairospy reference options-coverage
kairospy reference options-remove --underlying SPY
```

Adding an underlying starts only that provider's next bounded page. Removing
one reconciles its committed contracts out of the Massive provider snapshot;
neither operation schedules Binance, OKX, or Hyperliquid.

Massive WebSocket observations remain owned by Market. They are not an
authoritative Reference catalog feed and cannot mutate Reference state
directly. A Market-side unknown-symbol recovery path may call the public
coverage command, after which Reference performs the scoped REST reconciliation
and publishes the normal lifecycle facts. This preserves the `Market ->
Reference application API` boundary while keeping WebSocket discovery
incremental.
- Spot Instrument identity owns the base asset, not a provider quote pair or
  listing expiry. Quote, external symbol, and listing expiry remain on the
  Listing/Market. Expiring derivative IDs use compact UTC `YYYYMMDD` dates.

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

Outbox readers use bounded batches and acknowledge only event IDs accepted by
the transport or intentionally dropped because there is no subscriber. A
transactionally maintained counter makes health reads
constant-time even when millions of rows are pending. They must never delete
the complete outbox merely because one batch was published.

## Publication rules

- Every successful catalog mutation, including an administrative asset
  upsert, updates the mmap projection.
- A refresh with no generation change does not re-encode snapshots.
- Event publication is bounded to 1,024 lifecycle events per batch.
- The server advances one batch per publication tick or control request. It
  does not hold the application/control loop while draining a large historical
  backlog. Rows are acknowledged after Aeron accepts the complete batch or
  intentionally drops it because there is no subscriber; real transport
  failures leave rows pending for retry.
- A change batch carries the sequence of its last event as its high watermark.
  Encoding uses only generation and sequence metadata, so publishing a batch
  never clones or serializes the complete catalog.
- Aeron consumers reassemble fragmented messages before decoding the
  FlatBuffer envelope. Aeron is a best-effort notification stream: a publisher
  with no connected subscriber treats the batch as intentionally dropped. The
  mmap snapshot and its generation/event watermark are the recovery source for
  late consumers; the publication outbox must not wait for a subscriber.
- All snapshot views carry the same generation.
- The manifest is fsynced, written last, and atomically renamed after every
  view has been published. Contract readers validate the manifest generation
  and complete view set before consuming snapshots.

## Scheduling and backpressure

The control queue is bounded to 64 requests and rejects new requests
immediately when full. The single-owner application serializes refreshes and
each provider future has a 150-second timeout, so a slow provider cannot
accumulate an unbounded backlog.

The server attempts one publication batch every 50 milliseconds. Missing or
backpressured subscribers leave the durable rows untouched and do not turn a
publish retry into an unbounded synchronous drain.

## Failure semantics

Provider failure degrades provider freshness but does not erase its last good
records. Persistence failure leaves the in-memory generation uncommitted and
the refresh fails. Publication failure leaves outbox rows pending and does not
invalidate current state. Consumers recover through a complete snapshot and
then replay events after the snapshot event sequence.

The development CLI exposes both durable replay (`kairospy reference events`)
and live Aeron observation (`kairospy reference stream`). These commands are
acceptance clients for the same contracts used by business consumers; they do
not bypass the application boundary or read private service state.

## Required observability

The runtime should expose at least provider fetch latency and freshness,
refresh run duration, reconcile duration, SQLite commit duration, snapshot
payload sizes, publication latency, outbox depth, and control queue depth.
