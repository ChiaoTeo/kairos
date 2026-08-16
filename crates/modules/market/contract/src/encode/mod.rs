mod bar;
mod greeks;
mod metadata;
mod order_book;
mod quote;

pub use bar::BarEncoder;
pub use greeks::GreeksEncoder;
pub use metadata::{event_metadata, view_metadata, EncodeContext};
pub use order_book::OrderBookEncoder;
pub use quote::QuoteEncoder;

use crate::ContractResult;

pub trait TradeEncoder {
    fn encode_trade_occurred(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
}
