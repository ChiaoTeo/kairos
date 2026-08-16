use super::EncodeContext;
use crate::ContractResult;

pub trait BalanceEncoder {
    fn encode_balance_upserted(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_balance_removed(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_account_current_view(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
}
