use std::sync::Arc;

use super::view::ExecutionEvent;
use crate::ContractResult;
pub struct ExecutionEventFrame {
    bytes: Arc<[u8]>,
}
impl ExecutionEventFrame {
    pub(crate) fn new(bytes: Vec<u8>) -> Self {
        Self {
            bytes: bytes.into(),
        }
    }
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }
    pub fn decode(&self) -> ContractResult<ExecutionEvent<'_>> {
        super::decode::decode_event(&self.bytes)
    }
}
