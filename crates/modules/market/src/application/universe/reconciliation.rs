use crate::domain::market::ResolvedMarket;
use kairos_primitives::{Generation, Sequence};

/// A complete, watermarked replacement of the markets and data routes that
/// Market may use. Composition maps the external Reference contract into this
/// Market-owned input before invoking the application.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReconcileMarketUniverse {
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub markets: Vec<ResolvedMarket>,
}

impl super::super::MarketApplication {
    pub fn reconcile_market_universe(
        &mut self,
        update: ReconcileMarketUniverse,
    ) -> Result<
        std::collections::BTreeMap<
            crate::domain::subscription::SubscriptionId,
            crate::domain::subscription::ReconcileResult,
        >,
        super::super::MarketError,
    > {
        self.actor
            .apply_market_universe(update)
            .map_err(super::super::MarketError::Invalid)
    }
}
