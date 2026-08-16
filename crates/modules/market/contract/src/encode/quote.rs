use crate::{ContractResult, MarketViewKey};

use super::EncodeContext;

pub trait QuoteEncoder {
    fn encode_quote_updated(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_quote_latest_view(
        &self,
        context: &EncodeContext,
        key: &MarketViewKey,
    ) -> ContractResult<Vec<u8>>;
}
