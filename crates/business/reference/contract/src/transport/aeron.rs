//! Aeron adapters for Reference events.

use kairos_transport::{AeronBytePublisher, AeronByteSubscription};

use crate::{ContractError, ContractResult, EventEnvelope, EventPublisher};

pub struct AeronEventPublisher {
    publisher: AeronBytePublisher,
}

impl AeronEventPublisher {
    pub fn connect(aeron_dir: Option<&str>, channel: &str, stream_id: i32) -> ContractResult<Self> {
        let publisher = AeronBytePublisher::connect(aeron_dir, channel, stream_id)
            .map_err(ContractError::Transport)?;
        Ok(Self { publisher })
    }
}

impl EventPublisher for AeronEventPublisher {
    fn publish(&mut self, event: &EventEnvelope) -> ContractResult<()> {
        if event.payload.is_empty() {
            return Err(ContractError::Invalid(
                "event payload must not be empty".into(),
            ));
        }
        self.publisher
            .publish(&event.payload)
            .map_err(ContractError::Transport)
    }
}

pub struct AeronEventSubscriber {
    subscriber: AeronByteSubscription,
    stream_id: String,
    schema_version: u16,
    producer_id: String,
}

impl AeronEventSubscriber {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        stream_name: impl Into<String>,
        schema_version: u16,
        producer_id: impl Into<String>,
    ) -> ContractResult<Self> {
        let subscriber = AeronByteSubscription::connect(aeron_dir, channel, stream_id)
            .map_err(ContractError::Transport)?;
        Ok(Self {
            subscriber,
            stream_id: stream_name.into(),
            schema_version,
            producer_id: producer_id.into(),
        })
    }

    pub fn next(
        &mut self,
        sequence: u64,
        event_time_unix_nanos: u64,
    ) -> ContractResult<Option<EventEnvelope>> {
        let Some(payload) = self.subscriber.next().map_err(ContractError::Transport)? else {
            return Ok(None);
        };
        Ok(Some(EventEnvelope {
            stream_id: self.stream_id.clone(),
            sequence,
            schema_version: self.schema_version,
            producer_id: self.producer_id.clone(),
            event_time_unix_nanos,
            payload,
        }))
    }
}
