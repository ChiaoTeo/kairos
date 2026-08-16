use crate::{AccountEvent, ContractResult};
use std::sync::Arc;

pub struct AccountEventFrame {
    bytes: Arc<[u8]>,
}

impl AccountEventFrame {
    pub(crate) fn new(bytes: Vec<u8>) -> Self {
        Self {
            bytes: bytes.into(),
        }
    }
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }
    pub fn decode(&self) -> ContractResult<AccountEvent<'_>> {
        super::decode::decode_event(&self.bytes)
    }
}
