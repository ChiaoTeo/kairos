use super::EncodeContext;
use crate::ContractResult;
pub trait ValuationEncoder {
    fn encode_valuation_changed(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
}
