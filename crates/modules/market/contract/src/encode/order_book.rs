use super::EncodeContext;
use crate::{ContractResult, MarketViewKey};

pub trait OrderBookEncoder {
    fn encode_order_book_snapshot_received(
        &self,
        context: &EncodeContext,
    ) -> ContractResult<Vec<u8>>;
    fn encode_order_book_delta_received(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_order_book_resync_required(&self, context: &EncodeContext)
    -> ContractResult<Vec<u8>>;
    fn encode_order_book_latest_view(
        &self,
        context: &EncodeContext,
        key: &MarketViewKey,
    ) -> ContractResult<Vec<u8>>;
}
