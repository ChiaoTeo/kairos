use super::EncodeContext;
use crate::ContractResult;
pub trait PositionEncoder {
    fn encode_position_upserted(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_position_removed(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
}
