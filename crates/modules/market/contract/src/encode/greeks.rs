use crate::{ContractResult, MarketViewKey};

use super::EncodeContext;

pub trait GreeksEncoder {
    fn encode_greeks_updated(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_greeks_latest_view(
        &self,
        context: &EncodeContext,
        key: &MarketViewKey,
    ) -> ContractResult<Vec<u8>>;
}
