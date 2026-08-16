use std::sync::Arc;

use crate::{ContractResult, MarketEvent};

pub struct MarketEventFrame {
    bytes: Arc<[u8]>,
}

impl MarketEventFrame {
    pub(crate) fn new(bytes: Vec<u8>) -> Self {
        Self {
            bytes: bytes.into(),
        }
    }

    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn decode(&self) -> ContractResult<MarketEvent<'_>> {
        super::decode::decode_event(&self.bytes)
    }
}
