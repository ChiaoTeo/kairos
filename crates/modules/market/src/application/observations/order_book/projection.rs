use super::super::super::{MarketApplication, MarketError};
use crate::domain::observation::order_book::OrderBook;

impl MarketApplication {
    pub fn ingest_orderbook_snapshot(&mut self, book: OrderBook) -> Result<u64, MarketError> {
        self.actor
            .apply_order_book_snapshot(book)
            .map_err(MarketError::Invalid)
    }
}
