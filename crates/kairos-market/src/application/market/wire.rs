//! FlatBuffers decoding at the Market application boundary.

use kairos_protocol::generated::kairos::reference::v_1::{
    reference_changed_buffer_has_identifier, root_as_reference_changed,
};

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
    if !reference_changed_buffer_has_identifier(payload) {
        return Err("reference change buffer has an invalid file identifier".into());
    }
    let message = root_as_reference_changed(payload)
        .map_err(|error| format!("invalid ReferenceChanged FlatBuffer: {error}"))?;
    let header = message.header();
    Ok(ReferenceChangeNotice {
        producer_id: header.producer_id().to_owned(),
        stream_id: header.stream_id().to_owned(),
        sequence: header.sequence(),
        generation: message.generation(),
        event_sequence: message.event_sequence(),
        snapshot_id: message.snapshot_id().to_owned(),
        affected_market_ids: message
            .affected_market_ids()
            .map(|values| values.iter().map(str::to_owned).collect())
            .unwrap_or_default(),
        change_kinds: message
            .change_kinds()
            .map(|values| values.iter().map(str::to_owned).collect())
            .unwrap_or_default(),
    })
}
