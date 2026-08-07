# Shared snapshot layout

The first portable implementation uses a file-backed mmap. On Unix the same
layout can be placed in a memfd or POSIX shared-memory object without changing
the FlatBuffers payload.

```text
offset 0   magic KSS1
offset 4   format version (u16 little-endian)
offset 6   slot count (u16, currently 2)
offset 8   slot size (u32)
offset 12  active slot (u8)
offset 16  published generation (u64)
offset 24  slot lengths[2] (u32)
offset 32  slot generations[2] (u64)
offset 64  slot 0 bytes
offset 64 + slot_size  slot 1 bytes
```

There is one writer. It writes the inactive slot, flushes it, writes the slot
metadata, and then publishes the active slot and generation. Readers verify the
active slot and generation before and after decoding. A zero-copy view is only
valid while its slot remains active; callers that need a durable value should
use an owned copy or a future reader-lease API.

The FlatBuffers `SnapshotHeader` inside each slot carries the owning Actor,
the publication generation, the event stream identifier, and the event
sequence watermark. The mmap generation protects slot publication; the event
sequence protects snapshot-to-event-stream handoff. They are different
monotonic values and must not be conflated.
