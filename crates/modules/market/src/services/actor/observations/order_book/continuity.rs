use super::super::super::MarketActor;
use crate::domain::events::{MarketChange, MarketEvent, OrderBookResyncRequired};
use crate::domain::freshness::DataFreshnessStatus;
use crate::domain::observation::ObservationKind;
use crate::domain::source::{MarketFeedId, SourceEpoch, SourceStatus};

impl MarketActor {
    pub(crate) fn begin_orderbook_resync(
        &mut self,
        source_id: &MarketFeedId,
        epoch: SourceEpoch,
        market_id: &kairos_primitives::reference::MarketId,
        reason: String,
    ) -> Result<bool, String> {
        let source = self
            .sources
            .get_mut(source_id)
            .ok_or_else(|| format!("unknown market source: {source_id}"))?;
        let provider = source
            .descriptor
            .provider
            .clone()
            .ok_or("order-book resync requires a live Market provider")?;
        if epoch != source.epoch {
            return Ok(false);
        }
        let first_request = !source.resyncing_markets.contains(market_id);
        if first_request {
            source.resyncing_markets.push(market_id.clone());
        }
        source.status = SourceStatus::WarmingUp;
        source.last_error = Some(reason.clone());
        if let Some(book) = self.order_books.get_mut(&format!("{provider}:{market_id}")) {
            book.synchronized = false;
        }
        for freshness in self.freshness.values_mut().filter(|freshness| {
            freshness.provider == provider
                && freshness.scope.market_id() == Some(market_id)
                && freshness.data_kind == ObservationKind::OrderBook
        }) {
            freshness.status = DataFreshnessStatus::Stale;
        }
        self.refresh_feed_status();
        if first_request {
            let instrument_id = self
                .order_books
                .get(&format!("{provider}:{market_id}"))
                .map(|book| book.instrument_id.clone())
                .unwrap_or_else(|| {
                    kairos_primitives::reference::InstrumentId::new(market_id.as_str())
                        .expect("validated market id is a valid fallback instrument id")
                });
            self.event_sequence += 1;
            self.pending_changes.push(MarketChange {
                sequence: self.event_sequence,
                event: Some(MarketEvent::OrderBookResyncRequired(
                    OrderBookResyncRequired {
                        provider: provider.clone(),
                        market_id: market_id.clone(),
                        instrument_id,
                        expected_sequence: self
                            .order_books
                            .get(&format!("{provider}:{market_id}"))
                            .map(|book| book.sequence.get().saturating_add(1).into())
                            .unwrap_or_else(|| 0.into()),
                        observed_sequence: 0.into(),
                        reason,
                    },
                )),
                view: None,
            });
        }
        Ok(true)
    }
}
