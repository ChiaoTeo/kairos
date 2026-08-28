use kairos_primitives::time::{Generation, Sequence};

use crate::domain::market::ResolvedMarket;

/// An explicit universe supplied by replay and same-package fixtures.
/// Live Market runtime resolves current Reference facts on demand instead.
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
