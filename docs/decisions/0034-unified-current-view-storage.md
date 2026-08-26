# Decision 0034: Unified indexed current-view storage

- Status: Accepted
- Date: 2026-08-26
- Supersedes: [Decision 0033](0033-single-execution-current-view-and-audit-query.md) current-view storage shape
- Refines: [Decision 0005](0005-conflux-typed-io-ownership.md) View transport mechanics and
  [Decision 0013](0013-read-model-and-query-naming.md) default current-view mechanism

## Context

Kairos initially represented a current view as one complete FlatBuffers root in a double-slot
snapshot file. That shape works for a small point value, but aggregate roots such as
`CurrentExecutionView`, `AccountCurrentView`, `RiskLatestView`, and `CapitalCurrentView` combine
independently keyed collections with different update rates and consumers. A change to one entity
rebuilds and copies the whole root, a lookup by business ID scans a vector unless another index is
built, and one fixed slot carries the combined capacity risk.

Splitting every collection into a custom fixed-slot mmap or adding a separate bounded-ring protocol
would reduce individual hot-path costs but would also make Kairos own allocation, reclamation, ABA,
cross-file transaction, crash recovery, language ABI, and schema-evolution protocols before measured
requirements justify them.

LMDB is a mature mmap-backed B+Tree/MVCC store whose single-writer, multiple-reader model matches the
one-Actor writer rule. Named databases provide logical type separation while one write transaction can
atomically update related current entities. A reader uses a stable business key and touches the B+Tree
and value pages needed for that lookup; it does not deserialize or copy one aggregate state image.

## Decision

Kairos standardizes three cross-process data paths:

- Aeron carries ordered immutable business changes.
- An owner-scoped LMDB environment carries indexed current state.
- Owner JSON-RPC queries backed by durable storage carry history, audit, complex filtering, and
  synchronous control results.

FlatBuffers remains an encoding, not a fourth delivery path. Aeron events and LMDB entity values may
use owner-owned FlatBuffers schemas.

Each current-state owner uses one LMDB environment per runtime owner identity and one named database
per independently keyed entity family. Stable canonical business IDs are keys. Values contain one
entity or one explicitly bounded metadata record; they do not contain an owner's complete Actor state.
One Actor transition updates every affected named database and the environment metadata in one write
transaction.

Market current storage keeps one latest completed bar per semantic series key. Rolling windows are
Strategy-owned state rebuilt from `BarCompleted` events; window length, warm-up, gap handling, and
replay position are consumer policy. Historical backfill uses an explicit Market query. Kairos does
not add shared window metadata or a fixed-slot ring ABI without a named owner/consumer and benchmark
evidence.

The former aggregate snapshot resources are not part of the current-state contract. Each owner
migrates with a hard cut: its old snapshot publisher, reader, schema root, CLI
path, and generated bindings are removed in the same change that activates its LMDB view. There is no
dual publication, fallback reader, alias, or compatibility API.

The Actor remains the sole mutable business-state owner. The LMDB environment is a rebuildable
materialized read model and must not become an independent recovery or command authority. Journal,
audit, catalog, and owner persistence keep their existing authority.

## Consequences

- Clients fetch one current entity by stable business key and can range-scan an explicitly keyed
  family without reading unrelated state.
- Changes write the affected keys and copy-on-write B+Tree pages rather than rebuilding an aggregate
  FlatBuffer root.
- Related current-view changes across named databases are transactionally visible.
- Kairos does not expose stable physical offsets, raw pointers, handwritten C structs, or Python atomic
  access. Read values are valid only through the owner contract and the lifetime of a read transaction.
- One physical mmap environment may contain several logical entity families; physical file count is not
  the partitioning contract.
- Long-lived read transactions, map-size exhaustion, schema mismatch, and view publication lag become
  explicit health and certification concerns.
- The former snapshot implementations, aggregate roots, generated bindings, and public entry points
  have been removed. They are not available as alternate production paths.

## Implementation requirements

The platform provides one concrete indexed-view capability for environment lifecycle, transactions,
read-only opening, typed named-database registration, metadata, health, and safe paths. It does not own
business keys or values and does not introduce an application-owned port trait.

Each module contract owns its current-view keys, value schemas, validation, typed Rust reader, and
Python mapping. Python uses the project native transport boundary for LMDB transaction and lifetime
safety; it does not parse live mmap pages with `ctypes`.

Certification uses the tests and measurements in
[`docs/architecture/current-view-storage.md`](../architecture/current-view-storage.md). Execution,
Account, Risk, Capital, and Market all use the indexed platform capability and cross-language reader.
