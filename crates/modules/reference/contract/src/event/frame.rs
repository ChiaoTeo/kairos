use std::sync::Arc;

use crate::{ContractResult, ReferenceEvent};

pub struct ReferenceEventFrame {
    bytes: Arc<[u8]>,
}
impl ReferenceEventFrame {
    pub(crate) fn new(bytes: Vec<u8>) -> Self {
        Self {
            bytes: bytes.into(),
        }
    }
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }
    pub fn decode(&self) -> ContractResult<ReferenceEvent<'_>> {
        super::decode_event(&self.bytes)
    }
}
