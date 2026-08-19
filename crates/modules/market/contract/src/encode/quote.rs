use super::EncodeContext;
use crate::{ContractResult, MarketViewKey};

pub trait QuoteEncoder {
    fn encode_quote_updated(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_quote_latest_view(
        &self,
        context: &EncodeContext,
        key: &MarketViewKey,
    ) -> ContractResult<Vec<u8>>;
}
