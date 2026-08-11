//! Decoder for the Reference change event consumed by Market.

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceChangeNotice {
    pub producer_id: String,
    pub stream_id: String,
    pub sequence: u64,
    pub generation: u64,
    pub event_sequence: u64,
    pub snapshot_id: String,
    pub affected_market_ids: Vec<String>,
    pub change_kinds: Vec<String>,
}

pub fn decode_reference_changed(payload: &[u8]) -> Result<ReferenceChangeNotice, String> {
    let message =
        kairos_reference_contract::decode_change(payload).map_err(|error| error.to_string())?;
    Ok(ReferenceChangeNotice {
        producer_id: message.producer_id,
        stream_id: message.stream_id,
        sequence: message.sequence,
        generation: message.generation,
        event_sequence: message.event_sequence,
        snapshot_id: message.snapshot_id,
        affected_market_ids: message.affected_market_ids,
        change_kinds: message.change_kinds,
    })
}
