//! Reference event contract and transport-independent interfaces.

use crate::error::ContractResult;
use crate::{ContractError, LifecycleEvent};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventEnvelope {
    pub stream_id: String,
    pub sequence: u64,
    pub schema_version: u16,
    pub producer_id: String,
    pub event_time_unix_nanos: u64,
    pub payload: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceChange {
    pub producer_id: String,
    pub stream_id: String,
    pub sequence: u64,
    pub generation: u64,
    pub event_sequence: u64,
    pub snapshot_id: String,
    pub events: Vec<LifecycleEvent>,
    pub affected_market_ids: Vec<String>,
    pub change_kinds: Vec<String>,
}

/// Decode and validate the typed Reference change contract.
pub fn decode_change(payload: &[u8]) -> ContractResult<ReferenceChange> {
    use kairos_protocol::generated::kairos::reference::v_1::{
        reference_changed_buffer_has_identifier, root_as_reference_changed,
    };

    if !reference_changed_buffer_has_identifier(payload) {
        return Err(ContractError::Invalid(
            "Reference change buffer has an invalid file identifier".into(),
        ));
    }
    let message = root_as_reference_changed(payload)
        .map_err(|error| ContractError::Invalid(format!("decode Reference change: {error}")))?;
    let header = message.header();
    if header.stream_id().is_empty()
        || header.producer_id().is_empty()
        || header.sequence() != message.event_sequence()
    {
        return Err(ContractError::Invalid(
            "Reference change header is incomplete or has an inconsistent sequence".into(),
        ));
    }
    if message.snapshot_id() != format!("reference:{}", message.generation()) {
        return Err(ContractError::Invalid(
            "Reference change snapshot_id does not match generation".into(),
        ));
    }
    let events: Vec<LifecycleEvent> = message
        .events()
        .map(|values| {
            values
                .iter()
                .map(|value| LifecycleEvent {
                    event_id: value.event_id().to_owned(),
                    event_type: value.event_type().to_owned(),
                    event_time_unix_nanos: value.event_time_unix_nanos(),
                    record_kind: value.record_kind().map(str::to_owned),
                    record_id: value.record_id().map(str::to_owned),
                    market_id: value.market_id().map(str::to_owned),
                    instrument_id: value.instrument_id().map(str::to_owned),
                    listing_id: value.listing_id().map(str::to_owned),
                    exchange_id: value.exchange_id().map(str::to_owned),
                    source_symbol: value.source_symbol().map(str::to_owned),
                    previous_status: value.previous_status().map(str::to_owned),
                    current_status: value.current_status().map(str::to_owned),
                    previous_symbol: value.previous_symbol().map(str::to_owned),
                    current_symbol: value.current_symbol().map(str::to_owned),
                })
                .collect()
        })
        .unwrap_or_default();
    if events
        .iter()
        .any(|event| event.event_id.is_empty() || event.event_type.is_empty())
    {
        return Err(ContractError::Invalid(
            "Reference change contains an incomplete lifecycle event".into(),
        ));
    }
    let change_kinds = message
        .change_kinds()
        .map(|values| values.iter().map(str::to_owned).collect::<Vec<_>>())
        .unwrap_or_default();
    if change_kinds.len() != events.len() {
        return Err(ContractError::Invalid(
            "Reference change kinds do not match lifecycle event count".into(),
        ));
    }
    Ok(ReferenceChange {
        producer_id: header.producer_id().to_owned(),
        stream_id: header.stream_id().to_owned(),
        sequence: header.sequence(),
        generation: message.generation(),
        event_sequence: message.event_sequence(),
        snapshot_id: message.snapshot_id().to_owned(),
        events,
        affected_market_ids: message
            .affected_market_ids()
            .map(|values| values.iter().map(str::to_owned).collect())
            .unwrap_or_default(),
        change_kinds,
    })
}

pub trait EventPublisher {
    fn publish(&mut self, event: &EventEnvelope) -> ContractResult<()>;
}
