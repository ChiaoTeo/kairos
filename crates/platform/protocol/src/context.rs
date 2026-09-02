//! Shared context for encoding process-boundary events and views.

use kairos_primitives::runtime::{EventId, InstanceIdentity, ProducerId};
use kairos_primitives::time::{Generation, Sequence};

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum ProtocolContextError {
    #[error("producer incarnation must be positive")]
    NonPositiveProducerIncarnation,
    #[error("invalid protocol identity field `{field}`: {source}")]
    InvalidIdentity {
        field: &'static str,
        #[source]
        source: kairos_primitives::DomainTypeError,
    },
    #[error("protocol text field `{field}` must be non-empty and trimmed")]
    InvalidText { field: &'static str },
}

impl From<ProtocolContextError> for String {
    fn from(error: ProtocolContextError) -> Self {
        error.to_string()
    }
}

/// Common protocol metadata carried by a module event.
#[derive(Clone, Debug)]
pub struct EventProtocolContext {
    pub producer_id: ProducerId,
    pub producer_incarnation: u64,
    pub identity: InstanceIdentity,
    pub sequence: Sequence,
    event_id: EventId,
}

impl EventProtocolContext {
    pub fn new(
        producer_id: impl Into<String>,
        producer_incarnation: u64,
        identity: InstanceIdentity,
        sequence: u64,
        event_id: impl Into<String>,
    ) -> Result<Self, ProtocolContextError> {
        if producer_incarnation == 0 {
            return Err(ProtocolContextError::NonPositiveProducerIncarnation);
        }
        Ok(Self {
            producer_id: ProducerId::new(producer_id).map_err(|source| {
                ProtocolContextError::InvalidIdentity {
                    field: "producer_id",
                    source,
                }
            })?,
            producer_incarnation,
            identity,
            sequence: Sequence::new(sequence),
            event_id: EventId::new(event_id).map_err(|source| {
                ProtocolContextError::InvalidIdentity {
                    field: "event_id",
                    source,
                }
            })?,
        })
    }

    pub fn event_id(&self) -> &EventId {
        &self.event_id
    }

    pub fn with_event_id_suffix(&self, suffix: &str) -> Result<Self, ProtocolContextError> {
        if suffix.is_empty() || suffix.trim() != suffix {
            return Err(ProtocolContextError::InvalidText {
                field: "event_id_suffix",
            });
        }
        let mut context = self.clone();
        context.event_id =
            EventId::new(format!("{}:{suffix}", self.event_id)).map_err(|source| {
                ProtocolContextError::InvalidIdentity {
                    field: "event_id",
                    source,
                }
            })?;
        Ok(context)
    }
}

/// Common protocol metadata carried by a materialized view.
#[derive(Clone, Debug)]
pub struct ViewProtocolContext {
    pub producer_id: ProducerId,
    pub identity: InstanceIdentity,
    pub generation: Generation,
    pub owner_id: String,
    pub resource_id: String,
}

impl ViewProtocolContext {
    pub fn new(
        producer_id: impl Into<String>,
        owner_id: impl Into<String>,
        identity: InstanceIdentity,
        generation: u64,
        resource_id: impl Into<String>,
    ) -> Result<Self, ProtocolContextError> {
        let owner_id = required_text(owner_id.into(), "owner_id")?;
        let resource_id = required_text(resource_id.into(), "resource_id")?;
        Ok(Self {
            producer_id: ProducerId::new(producer_id).map_err(|source| {
                ProtocolContextError::InvalidIdentity {
                    field: "producer_id",
                    source,
                }
            })?,
            identity,
            generation: Generation::new(generation),
            owner_id,
            resource_id,
        })
    }
}

fn required_text(value: String, field: &'static str) -> Result<String, ProtocolContextError> {
    if value.is_empty() || value.trim() != value {
        return Err(ProtocolContextError::InvalidText { field });
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use kairos_primitives::runtime::InstanceIdentity;

    use super::{EventProtocolContext, ProtocolContextError, ViewProtocolContext};

    #[test]
    fn event_context_requires_a_positive_producer_incarnation() {
        let error =
            EventProtocolContext::new("producer", 0, InstanceIdentity::default(), 1, "event:1")
                .unwrap_err();

        assert_eq!(error, ProtocolContextError::NonPositiveProducerIncarnation);
    }

    #[test]
    fn derived_event_identity_preserves_the_original_context() {
        let context =
            EventProtocolContext::new("producer", 1, InstanceIdentity::default(), 1, "event:1")
                .unwrap();

        let derived = context.with_event_id_suffix("plan").unwrap();

        assert_eq!(context.event_id().as_str(), "event:1");
        assert_eq!(derived.event_id().as_str(), "event:1:plan");
    }

    #[test]
    fn view_context_rejects_unowned_resources() {
        let error =
            ViewProtocolContext::new("producer", " ", InstanceIdentity::default(), 1, "resource")
                .unwrap_err();

        assert_eq!(
            error,
            ProtocolContextError::InvalidText { field: "owner_id" }
        );
    }
}
