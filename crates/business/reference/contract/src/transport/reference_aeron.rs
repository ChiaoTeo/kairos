//! Aeron publication service for Reference change events.

use crate::encoding::FlatbuffersChangeEncoder;
use crate::event::{EventEnvelope, EventPublisher};
use crate::model::{LifecycleEvent as ContractLifecycleEvent, ReferenceCatalog as ContractCatalog};
use crate::transport::AeronEventPublisher;

use crate::{ContractError, ContractResult};
/// Publishes Reference change events through Aeron.
pub struct ReferenceAeronEventWriter {
    changes: AeronEventPublisher,
    encoder: FlatbuffersChangeEncoder,
}

impl ReferenceAeronEventWriter {
    pub fn connect(
        aeron_dir: Option<&str>,
        aeron_channel: &str,
        reference_changes_stream_id: i32,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
    ) -> ContractResult<Self> {
        let changes =
            AeronEventPublisher::connect(aeron_dir, aeron_channel, reference_changes_stream_id)
                .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self {
            changes,
            encoder: FlatbuffersChangeEncoder::new(actor_id, event_stream_id),
        })
    }

    pub fn publish(
        &mut self,
        catalog: &ContractCatalog,
        events: &[ContractLifecycleEvent],
    ) -> ContractResult<()> {
        if events.is_empty() {
            return Ok(());
        }
        let payload = self.encoder.encode_change(catalog, events)?;
        self.changes
            .publish(&EventEnvelope {
                stream_id: self.encoder.event_stream_id.clone(),
                sequence: catalog.event_sequence,
                schema_version: 1,
                producer_id: self.encoder.actor_id.clone(),
                event_time_unix_nanos: unix_nanos(),
                payload,
            })
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

fn unix_nanos() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
