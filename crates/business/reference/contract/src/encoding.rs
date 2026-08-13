//! FlatBuffers encoding for bounded Reference change notifications.

use kairos_protocol::generated::kairos::common::v_1::{MessageHeader, MessageHeaderArgs};
use kairos_protocol::generated::kairos::reference::v_1::{
    finish_reference_changed_buffer, LifecycleEvent as FbLifecycleEvent,
    LifecycleEventArgs as FbLifecycleEventArgs, ReferenceChanged as FbReferenceChanged,
    ReferenceChangedArgs as FbReferenceChangedArgs,
};
use kairos_protocol::InstanceIdentity;

use crate::error::ContractResult;
use crate::model::{LifecycleEvent, ReferenceCatalog};

pub struct FlatbuffersChangeEncoder {
    pub actor_id: String,
    pub event_stream_id: String,
    pub identity: InstanceIdentity,
}

impl FlatbuffersChangeEncoder {
    pub fn new(actor_id: impl Into<String>, event_stream_id: impl Into<String>) -> Self {
        Self {
            actor_id: actor_id.into(),
            event_stream_id: event_stream_id.into(),
            identity: InstanceIdentity::default(),
        }
    }

    pub fn with_identity(
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Self {
        Self {
            actor_id: actor_id.into(),
            event_stream_id: event_stream_id.into(),
            identity,
        }
    }

    pub fn encode_change(
        &self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
    ) -> ContractResult<Vec<u8>> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let message_id = builder.create_string(&format!("reference:{}", catalog.event_sequence));
        let stream_id = builder.create_string(&self.event_stream_id);
        let producer_id = builder.create_string(&self.actor_id);
        let snapshot_id = builder.create_string(&format!("reference:{}", catalog.generation));
        let event_offsets = events
            .iter()
            .map(|event| {
                let event_id = builder.create_string(&event.event_id);
                let event_type = builder.create_string(&event.event_type);
                let market_id = optional_string(&mut builder, event.market_id.as_deref());
                let instrument_id = optional_string(&mut builder, event.instrument_id.as_deref());
                let listing_id = optional_string(&mut builder, event.listing_id.as_deref());
                let exchange_id = optional_string(&mut builder, event.exchange_id.as_deref());
                let source_symbol = optional_string(&mut builder, event.source_symbol.as_deref());
                let previous_status =
                    optional_string(&mut builder, event.previous_status.as_deref());
                let current_status = optional_string(&mut builder, event.current_status.as_deref());
                let previous_symbol =
                    optional_string(&mut builder, event.previous_symbol.as_deref());
                let current_symbol = optional_string(&mut builder, event.current_symbol.as_deref());
                let record_kind = optional_string(&mut builder, event.record_kind.as_deref());
                let record_id = optional_string(&mut builder, event.record_id.as_deref());
                let operation = optional_string(&mut builder, event.operation.as_deref());
                let record_payload_json =
                    optional_string(&mut builder, event.record_payload_json.as_deref());
                FbLifecycleEvent::create(
                    &mut builder,
                    &FbLifecycleEventArgs {
                        event_id: Some(event_id),
                        event_type: Some(event_type),
                        event_time_unix_nanos: event.event_time_unix_nanos,
                        record_kind,
                        record_id,
                        market_id,
                        instrument_id,
                        listing_id,
                        exchange_id,
                        source_symbol,
                        previous_status,
                        current_status,
                        previous_symbol,
                        current_symbol,
                        operation,
                        generation: event.generation,
                        record_payload_json,
                    },
                )
            })
            .collect::<Vec<_>>();
        let event_offsets = builder.create_vector(&event_offsets);
        let market_ids = events
            .iter()
            .filter_map(|event| event.market_id.as_ref())
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .map(|value| builder.create_string(value))
            .collect::<Vec<_>>();
        let change_kinds = events
            .iter()
            .map(|event| builder.create_string(&event.event_type))
            .collect::<Vec<_>>();
        let market_ids = builder.create_vector(&market_ids);
        let change_kinds = builder.create_vector(&change_kinds);
        let workspace_id = optional_non_empty(&mut builder, &self.identity.workspace_id);
        let launch_id = optional_non_empty(&mut builder, &self.identity.launch_id);
        let instance_id = optional_non_empty(&mut builder, &self.identity.instance_id);
        let header = MessageHeader::create(
            &mut builder,
            &MessageHeaderArgs {
                message_id: Some(message_id),
                stream_id: Some(stream_id),
                producer_id: Some(producer_id),
                workspace_id,
                launch_id,
                instance_id,
                sequence: catalog.event_sequence,
                event_time_unix_nanos: events
                    .last()
                    .map(|event| event.event_time_unix_nanos)
                    .unwrap_or_else(unix_nanos),
                publish_time_unix_nanos: unix_nanos(),
            },
        );
        let root = FbReferenceChanged::create(
            &mut builder,
            &FbReferenceChangedArgs {
                header: Some(header),
                generation: catalog.generation,
                event_sequence: catalog.event_sequence,
                snapshot_id: Some(snapshot_id),
                events: Some(event_offsets),
                affected_market_ids: Some(market_ids),
                change_kinds: Some(change_kinds),
            },
        );
        finish_reference_changed_buffer(&mut builder, root);
        Ok(builder.finished_data().to_vec())
    }
}

fn optional_string<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut flatbuffers::FlatBufferBuilder<'a, A>,
    value: Option<&str>,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    value.map(|value| builder.create_string(value))
}

fn optional_non_empty<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut flatbuffers::FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}

fn unix_nanos() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::FlatbuffersChangeEncoder;
    use crate::model::{LifecycleEvent, ReferenceCatalog};
    use kairos_protocol::generated::kairos::reference::v_1::root_as_reference_changed;

    #[test]
    fn change_message_contains_full_lifecycle_events() {
        let event = LifecycleEvent {
            event_id: "event:1".into(),
            event_type: "symbol_changed".into(),
            event_time_unix_nanos: 42,
            record_kind: Some("market".into()),
            record_id: Some("market:1".into()),
            market_id: Some("market:1".into()),
            previous_symbol: Some("OLD".into()),
            current_symbol: Some("NEW".into()),
            operation: Some("upsert".into()),
            generation: 3,
            record_payload_json: Some(r#"{"market_id":"market:1"}"#.into()),
            ..Default::default()
        };
        let catalog = ReferenceCatalog {
            generation: 3,
            event_sequence: 1,
            ..Default::default()
        };
        let bytes = FlatbuffersChangeEncoder::new("reference-test", "reference.events")
            .encode_change(&catalog, &[event])
            .unwrap();
        let message = root_as_reference_changed(&bytes).unwrap();
        assert_eq!(
            message.events().unwrap().get(0).current_symbol(),
            Some("NEW")
        );
        let decoded = crate::decode_change(&bytes).unwrap();
        assert_eq!(decoded.events[0].record_id.as_deref(), Some("market:1"));
        assert_eq!(decoded.events[0].operation.as_deref(), Some("upsert"));
        assert_eq!(decoded.events[0].generation, 3);
        assert!(decoded.events[0].record_payload_json.is_some());
        assert_eq!(decoded.event_sequence, 1);
    }
}
