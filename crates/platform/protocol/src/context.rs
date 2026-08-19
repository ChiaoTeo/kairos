//! Shared context for encoding process-boundary events and views.

use kairos_primitives::runtime::{EventId, InstanceIdentity, ProducerId};
use kairos_primitives::{Generation, Sequence};

/// Common protocol metadata carried by every module event or view.
#[derive(Clone, Debug)]
pub struct ProtocolContext {
    pub producer_id: ProducerId,
    pub identity: InstanceIdentity,
    pub sequence: Sequence,
    pub event_id: Option<EventId>,
    pub generation: Generation,
    pub owner_id: String,
    pub resource_id: String,
}

impl ProtocolContext {
    pub fn event(
        producer_id: impl Into<String>,
        identity: InstanceIdentity,
        sequence: u64,
        event_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            producer_id: ProducerId::new(producer_id).map_err(|error| error.to_string())?,
            identity,
            sequence: Sequence::new(sequence),
            event_id: Some(EventId::new(event_id).map_err(|error| error.to_string())?),
            generation: Generation::new(0),
            owner_id: String::new(),
            resource_id: String::new(),
        })
    }

    pub fn view(
        producer_id: impl Into<String>,
        owner_id: impl Into<String>,
        identity: InstanceIdentity,
        generation: u64,
        resource_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            producer_id: ProducerId::new(producer_id).map_err(|error| error.to_string())?,
            identity,
            sequence: Sequence::new(0),
            event_id: None,
            generation: Generation::new(generation),
            owner_id: owner_id.into(),
            resource_id: resource_id.into(),
        })
    }
}
