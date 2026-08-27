# Decision 0036: Owner contract Python bindings

- Status: Superseded by [Decision 0037](0037-complete-owner-contract-python-convergence.md)
- Date: 2026-08-27
- Supersedes: Decision 0035 for Python business current-view reads

## Context

Market, Account, Execution, Risk, and Capital publish owner-scoped LMDB indexed current views whose
values are owner-owned FlatBuffers. The Rust contract crates already own canonical keys, schemas,
identity validation, and business vocabulary. Python currently opens the same environments through a
generic platform binding, receives complete byte buffers, and repeats owner-specific FlatBuffer
decoding and validation in Python.

That split creates two production implementations of readiness, key/value identity, enum, decimal,
and value validation. It also copies a complete value into Python when a business caller needs only a
small typed result. Keeping a Python generated-binding fallback would preserve the split and make
runtime behavior depend on which native module is available.

## Decision

Every LMDB current-view owner provides one companion Pyo3 package at
`crates/modules/<owner>/contract/py`. The companion depends on the independently depend-able Rust
owner contract and exposes a private ABI3 extension named `kairospy._native_<owner>_contract`.

The Rust owner contract is the only implementation of current-view key construction, metadata and
readiness checks, FlatBuffer verification, key/value identity validation, and typed business result
construction. Its ordinary Python APIs return owned typed narrow results. A transaction-scoped
borrowed view may exist only for a concrete Rust caller and never crosses Pyo3.

Python infrastructure contract facades call the owner Pyo3 extension and map its immutable ABI values
to typed Python contract records. Consuming Applications and Strategy do not import private native
modules, generated current-view FlatBuffer roots, LMDB database names, or raw byte buffers.

The generic `kairos-python-transport` package remains a platform capability for Aeron and LMDB
mechanics. It does not depend on business contracts and is not a business current-view API. Once all
owner readers migrate, its generic indexed-view Python surface and the duplicated Python
current-view decoders are removed. There is no runtime fallback or dual production path.

Complete owner snapshots remain allowed when the business operation genuinely requires one atomic
multi-family result. This is not a second transport path: the owner Rust contract still performs the
read and returns a typed result through the same owner binding.

## Consequences

- Rust and Python share one business validation implementation.
- Python objects never retain an LMDB transaction, mmap pointer, or FlatBuffer table.
- Small business reads avoid copying the complete FlatBuffer into Python.
- Each owner version-controls and tests its own Python ABI without making platform depend on business
  vocabulary.
- The wheel contains multiple private ABI3 extensions and release smoke tests import and exercise all
  of them.
- Migration is a hard cut. A migrated API does not fall back to generic bytes or Python generated
  current-view decoding.
- New Cargo packages, workspace dependency entries, crate-layout checks, Python type checks, and wheel
  verification become part of the change.

## Performance evidence

The checked-in [field-read benchmark](../../crates/platform/indexed-view/benches/field_reads.rs)
compares a 1 MiB owned exact-value read with an in-transaction selection of one fixed-width field.
On the implementation host the representative Criterion intervals were 18.58–18.62 µs versus
382–385 ns. The result supports narrow borrowed field reads for small queries; complete atomic owner
snapshots remain owned because the business operation consumes the complete family result.
