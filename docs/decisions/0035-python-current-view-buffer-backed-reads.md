# Decision 0035: Python current-view buffer-backed reads

- Status: Accepted
- Date: 2026-08-27
- Refines: [Decision 0034](0034-unified-current-view-storage.md) Python read semantics

## Context

The indexed current view stores independently keyed business values in LMDB and encodes each value as
an owner-owned FlatBuffer. LMDB uses mmap internally, but the current Python path copies a value from
the LMDB transaction into a Rust `Vec<u8>`, copies it again into Python `bytes`, and may then map the
FlatBuffer into another owned Python object graph.

Python consumers normally inspect, filter, or calculate from current values. They do not require every
balance, position, order, quote, and metadata record to come from one storage transaction. Even a
single LMDB snapshot cannot make independently produced business facts simultaneous. Their business
safety instead depends on entity revision, owner or provider sequence, observation time, freshness,
and synchronization state.

LMDB read transactions remain necessary even when a consumer does not require snapshot semantics: a
transaction pins the copy-on-write pages referenced by an mmap slice. Exposing that slice after the
transaction ends would be unsafe. Long-lived Python transactions would also pin old pages and allow
the environment to grow.

## Decision

Python current-view reads guarantee the integrity of one keyed value, not transactional consistency
across independently read values. A reader may observe different business revisions in consecutive
calls. Consumers use value-owned revision, sequence, time, freshness, and synchronization evidence to
decide whether a value is usable. A business operation that requires an atomic cross-owner decision
uses the owning Application or control contract rather than reconstructing that decision from current
views.

The default Python result is a buffer-backed semantic view over one Python-owned immutable byte
buffer. The view reads FlatBuffer fields lazily, hides generated accessor spelling, and does not
materialize a duplicate dataclass or domain object graph. The native boundary verifies the file
identifier and FlatBuffer structure; the owner contract validates the canonical key against the
business identity encoded in the value.

The default native path creates Python `bytes` directly from the LMDB slice while a short read
transaction is alive. It does not first copy the value into an intermediate Rust `Vec<u8>`. Once the
Python buffer exists, the transaction ends and the semantic view may safely outlive the reader call.
Materialization into an owned application model remains explicit and is used only when data must cross
an asynchronous, thread, process, persistence, or long-lived cache boundary.

Prefix and family reads are explicitly bounded. Native iteration may decode or visit records while a
short transaction is alive, but ordinary Python iterators and returned collections do not retain an
LMDB transaction or raw mmap pointer. Large numeric vectors such as order-book levels use a measured
native or contiguous-array path instead of constructing one Python object per element.

A transaction-scoped mmap-backed Python view is not the default API. It may be added for a specific
large-value caller only after measurements show that the remaining LMDB-to-`bytes` copy materially
affects accepted p95 or p99 latency. Such an API must prevent use after its context closes, reject
asynchronous or cross-thread retention, bound transaction duration, and include long-reader and page
growth tests. A general raw mmap or `ctypes` entry point is not admitted.

Metadata-and-value or multi-database snapshot reads remain explicit owner-contract operations for
current callers that genuinely require storage-version consistency. They are not a mandatory prelude
to each Python value read. LMDB write transactions continue to atomically publish each accepted set of
owner mutations and metadata; this decision relaxes consumer read semantics, not value integrity or
writer crash atomicity.

## Consequences

- Ordinary Python code receives one safe immutable buffer and a lazy business-facing view instead of
  a copied FlatBuffer plus a second owned object tree.
- The common path performs at most one value-buffer copy from LMDB into Python and releases its read
  transaction immediately.
- Consecutive reads may contain different revisions by contract. Callers that care about skew check
  business freshness and sequence evidence rather than relying on an implicit LMDB snapshot.
- Generated FlatBuffers bindings remain wire adapters and do not become the Python application API.
- Full-family reads require a contract-owned limit or continuation mechanism; `usize::MAX` is not a
  public population policy.
- Large-vector zero-copy or native field extraction requires benchmark evidence and a concrete caller.
- Python-owned buffers remain the default because their single copy buys simple lifetime, caching,
  asynchronous, and cross-thread safety.
- Read transaction lifetime and LMDB page retention remain observable operational concerns for any
  future borrowed-buffer path.
