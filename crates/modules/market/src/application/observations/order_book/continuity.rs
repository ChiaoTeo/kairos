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
        let provider = update
            .market
            .selected_provider
            .clone()
            .or_else(|| {
                update
                    .market
                    .runtime_route()
                    .map(|route| route.provider.clone())
            })
            .ok_or_else(|| {
                MarketError::Invalid("resolved Market has no selected provider".into())
            })?;
        if update.snapshot {
            let book = crate::domain::observation::order_book::OrderBook::snapshot_with_provider(
                provider,
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
                provider,
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
