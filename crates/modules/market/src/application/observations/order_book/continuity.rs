use super::super::super::{MarketApplication, MarketError};
use crate::domain::observation::order_book::OrderBookDelta;
use crate::services::source::messages::SourceOrderBookUpdate;

impl MarketApplication {
    pub fn ingest_orderbook_delta(&mut self, delta: OrderBookDelta) -> Result<u64, MarketError> {
        self.actor
            .apply_order_book_delta(delta)
            .map_err(MarketError::Invalid)
    }

    pub(crate) fn apply_source_orderbook(
        &mut self,
        update: SourceOrderBookUpdate,
    ) -> Result<(), MarketError> {
        if update.snapshot {
            let book = crate::domain::observation::order_book::OrderBook::snapshot_with_source(
                update.source_id,
                update.market_id.to_string(),
                update.instrument_id.to_string(),
                update.last_sequence,
                update.event_time_unix_nanos,
                update.bids,
                update.asks,
            )
            .map_err(MarketError::Invalid)?;
            self.ingest_orderbook_snapshot(book).map(|_| ())
        } else {
            self.ingest_orderbook_delta(OrderBookDelta {
                source_id: update.source_id,
                market_id: update.market_id,
                instrument_id: update.instrument_id,
                first_sequence: update.first_sequence,
                last_sequence: update.last_sequence,
                event_time_unix_nanos: update.event_time_unix_nanos,
                bids: update.bids,
                asks: update.asks,
                checksum: None,
            })
            .map(|_| ())
        }
    }
}
