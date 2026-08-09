//! Reference event contract and transport-independent interfaces.

use crate::error::ContractResult;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventEnvelope {
    pub stream_id: String,
    pub sequence: u64,
    pub schema_version: u16,
    pub producer_id: String,
    pub event_time_unix_nanos: u64,
    pub payload: Vec<u8>,
}

pub trait EventPublisher {
    fn publish(&mut self, event: &EventEnvelope) -> ContractResult<()>;
}
