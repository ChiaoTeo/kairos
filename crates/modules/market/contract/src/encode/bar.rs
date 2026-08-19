use super::EncodeContext;
use crate::{ContractResult, MarketViewKey};

pub trait BarEncoder {
    fn encode_bar_completed(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_bar_window_view(
        &self,
        context: &EncodeContext,
        key: &MarketViewKey,
    ) -> ContractResult<Vec<u8>>;
}
