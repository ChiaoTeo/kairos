//! Process-boundary transport adapters.

pub mod aeron;
mod generated_spec;

pub use aeron::{
    AeronBytePublisher, AeronByteSubscription, AeronEndpoint, AeronTransportError, PublishOutcome,
};
pub use generated_spec::{
    DEFAULT_CHANNEL, DEFAULT_MAX_PAYLOAD_LEN, TRANSPORT_FINGERPRINT, TRANSPORT_SPEC_VERSION,
    stream_ids,
};
