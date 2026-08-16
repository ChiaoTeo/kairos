//! Data-plane constants. Message identity and versioning live in each
//! independent FlatBuffers root under `schemas/`.

/// Four-byte network-order length prefix used by the Unix transport adapter.
pub const TRANSPORT_FRAME_PREFIX_BYTES: usize = 4;
