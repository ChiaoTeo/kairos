use super::EncodeContext;
use crate::ContractResult;
pub trait StatusEncoder {
    fn encode_account_status_changed(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
}
