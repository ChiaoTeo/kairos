#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SnapshotEnvelope {
    pub view_key: String,
    pub producer_id: String,
    pub event_stream_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub published_at_unix_nanos: u64,
    pub payload: Vec<u8>,
}
