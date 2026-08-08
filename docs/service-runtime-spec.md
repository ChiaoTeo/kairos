# Kairos service runtime specification

This document defines the system-level contract for a long-lived business
service. It is a process and transport contract, not a Rust trait hierarchy.

## Required control plane

An instance-scoped service exposes HTTP/1.1 over its Unix domain socket. The
socket is owned by `kairos-workspace` and is located at:

```text
<instance>/sockets/<service>.sock
```

The following endpoints are reserved for every long-lived service:

```text
GET  /v1/health
GET  /v1/snapshot
POST /v1/stop
```

`/v1/snapshot` is a diagnostic JSON representation. Strategy and other
high-throughput consumers use the binary snapshot resource described below.
Business endpoints must use `/v1/<business-name>` and must not redefine the
reserved endpoints.

## Lifecycle

The process creates its socket and health resource before becoming ready. A
ready health response has `status = "ready"`. A service that can answer
control requests but cannot refresh its external source reports
`status = "degraded"`. A stop request returns `202` and the process removes
its socket after it has stopped accepting work.

## Snapshot plane

The canonical instance snapshot resource is:

```text
<instance>/snapshots/<service>/<service>.snapshot
```

Snapshots are fixed-capacity FlatBuffers state regions defined under
`schemas/projection`. Every region contains a runtime header and a FlatBuffers
payload with the service owner, view key, workspace identity, event sequence,
generation, and capacity/active-count fields appropriate to that projection.
Entity collections use preallocated slots; additions claim an inactive slot
and removals mark a slot inactive. Mutable strings use fixed-size byte storage
with an explicit length.

The schema remains business-owned. For example, Market live schemas live under
`schemas/projection/market` and their generated bindings live in
`kairos-protocol`. `kairos-transport` must not contain Market, Account, or
other business tables; it only owns the generic mapped-region header,
seqlock/watermark protocol, and byte-level Aeron/IPC primitives. The business
crate owns slot allocation and the code that maps its domain events into the
fixed-capacity fields.

The region is updated in place. The runtime header carries a seqlock epoch:
odd means a write is in progress and even means a consistent state is
available. Readers copy or inspect the region only when the epoch is unchanged
before and after the read. Resizing is a process lifecycle operation, never an
in-place operation visible to existing readers.

`kairos-transport` provides the byte-level shared-memory and Aeron primitives.
Business crates select channels and stream ids, but do not depend directly on
the Aeron SDK.

The shared region is the live current state. A process initializes it once and
then mutates it in place; it must not rebuild and replace a complete payload
for each business event.

## Event plane

Event streams are optional. A service only exposes an event stream when its
business contract has a consumer. Event messages use the generated
`MessageHeader` and a monotonically increasing service-owned sequence. The
transport may be a framed Unix socket or Aeron, but framing, stream identity,
sequence semantics, and schema version are part of the service contract.

Consumers read the live region at a stable epoch and use events as update
notifications and business facts. Each event carries the state
`event_sequence`/watermark it produced. A consumer that observes a gap or an
older watermark must reread the region rather than applying stale updates.
Event application must be idempotent by `(stream_id, sequence)`.

Aeron publication is a real-time notification/fact stream, not the storage for
the live snapshot. If lossless historical recovery is required, persistence or
an Aeron Archive is a separate durability concern; it does not introduce a
periodic snapshot checkpoint into the live-state contract.

## Workspace ownership

Services must obtain sockets, health paths, and snapshot paths from
`kairos-workspace`. Binaries may select providers and modes, but must not
invent a second instance resource layout.

## Conformance

A service is conforming when its process-level tests prove the reserved
control endpoints, lifecycle cleanup, and its declared binary snapshot. A
service is not required to implement an event stream merely because another
service does.
