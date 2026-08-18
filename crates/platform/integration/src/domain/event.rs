//! Metadata carried with normalized facts received from an external channel.

use kairos_primitives::UnixNanos;

use crate::domain::ParticipantRef;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalEventEnvelope<T> {
    pub participant: ParticipantRef,
    pub binding_id: String,
    pub channel_id: String,
    pub channel_epoch: u64,
    pub participant_event_id: Option<String>,
    pub participant_sequence: Option<u64>,
    pub observed_at_unix_nanos: UnixNanos,
    pub received_at_unix_nanos: UnixNanos,
    pub payload: T,
}

/// One normalized fact from a physical provider stream that multiplexes
/// market, account, and execution channels.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExternalParticipantEvent {
    Account(crate::ExternalAccountEventEnvelope),
    Execution(ExternalEventEnvelope<crate::ExternalExecutionEvent>),
    Market(crate::MarketEvent),
}
