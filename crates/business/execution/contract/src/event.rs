#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventEnvelope {
    pub stream_id: String,
    pub sequence: u64,
    pub schema_version: u16,
    pub producer_id: String,
    pub event_time_unix_nanos: u64,
    pub payload: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionEventEnvelope<T> {
    pub stream_id: String,
    pub sequence: u64,
    pub schema_version: u16,
    pub producer_id: String,
    pub event_time_unix_nanos: u64,
    #[serde(default)]
    pub intent_id: Option<String>,
    #[serde(default)]
    pub plan_id: Option<String>,
    #[serde(default)]
    pub leg_id: Option<String>,
    #[serde(default)]
    pub order_id: Option<String>,
    pub payload: T,
}
