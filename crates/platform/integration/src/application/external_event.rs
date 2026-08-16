//! Metadata carried with normalized facts received from an external channel.

use kairos_primitives::UnixNanos;

use crate::domain::ParticipantRef;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalEventEnvelope<T> {
    pub participant: ParticipantRef,
    pub binding_id: String,
    pub channel_id: String,
    pub channel_epoch: u64,
    pub provider_event_id: Option<String>,
    pub provider_sequence: Option<u64>,
    pub observed_at_unix_nanos: UnixNanos,
    pub received_at_unix_nanos: UnixNanos,
    pub payload: T,
}
