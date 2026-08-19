//! Process-boundary transport adapters.

pub mod aeron;
mod generated_spec;
pub mod replacement;
pub mod shared_memory;

pub use aeron::{
    AeronBytePublisher, AeronByteSubscription, AeronEndpoint, AeronTransportError, PublishOutcome,
};
pub use generated_spec::{
    DEFAULT_CHANNEL, DEFAULT_MAX_PAYLOAD_LEN, TRANSPORT_FINGERPRINT, TRANSPORT_SPEC_VERSION,
    stream_ids,
};
pub use replacement::{
    ReplacementSnapshotStorage, SnapshotCodec, SnapshotPublishReceipt, SnapshotResource,
};
pub use shared_memory::{
    SNAPSHOT_ENVELOPE_VERSION, SharedSnapshotPayload, SharedSnapshotReader, SharedSnapshotWriter,
    SnapshotEnvelopeMetadata, SnapshotError,
};
