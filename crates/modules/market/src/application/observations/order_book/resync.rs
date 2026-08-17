use crate::domain::market::ResolvedMarket;
use crate::domain::source::{SourceEpoch, SourceId};
use crate::services::actor::PendingSourceRequest;
use crate::services::source::messages::SourceCommand;

use super::super::super::{MarketApplication, MarketError};

impl MarketApplication {
    pub(crate) async fn request_orderbook_resync(
        &mut self,
        source_id: SourceId,
        epoch: SourceEpoch,
        market: ResolvedMarket,
        reason: String,
    ) -> Result<(), MarketError> {
        if self.actor.pending_source_requests.values().any(|pending| {
            matches!(
                pending,
                PendingSourceRequest::ResyncOrderBook {
                    source_id: pending_source,
                    market_id,
                } if pending_source == &source_id && market_id == &market.market_id
            )
        }) {
            return Ok(());
        }
        self.actor
            .begin_orderbook_resync(&source_id, epoch, &market.market_id, reason)
            .map_err(MarketError::Invalid)?;
        let request_id = self.next_request_id();
        self.actor
            .attached_sources
            .get(&source_id)
            .ok_or_else(|| MarketError::Invalid(format!("unknown market source: {source_id}")))?
            .commands
            .send(SourceCommand::ResyncOrderBook {
                request_id,
                market: Box::new(market.clone()),
            })
            .await
            .map_err(|_| {
                MarketError::Invalid(format!(
                    "market source command channel closed during resync: {source_id}"
                ))
            })?;
        self.actor.pending_source_requests.insert(
            request_id,
            PendingSourceRequest::ResyncOrderBook {
                source_id,
                market_id: market.market_id,
            },
        );
        Ok(())
    }
}
