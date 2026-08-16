use super::EncodeContext;
use crate::ContractResult;
pub trait ObservedOrderEncoder {
    fn encode_observed_order_upserted(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_observed_order_removed(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_observed_orders_current_view(
        &self,
        context: &EncodeContext,
    ) -> ContractResult<Vec<u8>>;
}
