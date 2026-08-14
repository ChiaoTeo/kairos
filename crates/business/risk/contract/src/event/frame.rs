use std::sync::Arc;
use crate::{ContractResult};
use super::view::DecodedRiskEvent;
pub struct RiskEventFrame { bytes: Arc<[u8]> }
impl RiskEventFrame { pub(crate) fn new(bytes:Vec<u8>)->Self{Self{bytes:bytes.into()}} pub fn bytes(&self)->&[u8]{&self.bytes} pub fn decode(&self)->ContractResult<DecodedRiskEvent<'_>>{super::decode::decode_event(&self.bytes)} }
