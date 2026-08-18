//! Process-boundary transport adapters.

pub mod aeron;
mod generated_spec;
pub mod replacement;
pub mod shared_memory;

pub use aeron::{
    AeronBytePublisher, AeronByteSubscription, AeronEndpoint, AeronTransportError, PublishOutcome,
};
pub use generated_spec::{
    stream_ids, DEFAULT_CHANNEL, DEFAULT_MAX_PAYLOAD_LEN, TRANSPORT_FINGERPRINT,
    TRANSPORT_SPEC_VERSION,
};
pub use replacement::{
    ReplacementSnapshotStorage, SnapshotCodec, SnapshotPublishReceipt, SnapshotResource,
};
pub use shared_memory::{
    SharedSnapshotPayload, SharedSnapshotReader, SharedSnapshotWriter, SnapshotEnvelopeMetadata,
    SnapshotError, SNAPSHOT_ENVELOPE_VERSION,
};
