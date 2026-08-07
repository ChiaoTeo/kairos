# Published snapshot schemas

Published snapshot schemas describe immutable snapshots published by an owning Actor.
The snapshot is not the Actor's mutable state and it is not an alias for a
language-specific view class. The Actor remains the only writer and is
responsible for ordering, recovery, generation changes, and publication.

A writer builds a complete snapshot in a new buffer, publishes its version
atomically, and never mutates a buffer that a reader may still hold. Readers
can then access FlatBuffers views directly from mmap, memfd, or another shared
memory region without constructing a second object graph.

Each published snapshot has an independent root and file identifier. A
single Actor may publish several roots when their read cadence or size differs
(for example current quotes versus order-book depth). Snapshot roots should
reference business types from the owning namespace. A small read-specific
type is justified only when the shape is genuinely different or has an
independent compatibility boundary.

Consumers are readers, not owners. A newly started strategy obtains the latest
snapshot from the Actor and then follows the `event_stream_id` from the
snapshot's `event_sequence`. `generation` identifies publication order;
`event_sequence` identifies the event-tail watermark. A strategy may maintain
private indicators or model state, but it must not reconstruct shared
Actor-owned state by itself.
