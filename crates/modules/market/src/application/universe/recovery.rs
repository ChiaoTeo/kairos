use crate::domain::market::ResolvedMarket;

impl super::super::MarketApplication {
    pub(crate) fn market_universe(&self) -> Vec<ResolvedMarket> {
        self.actor.market_universe()
    }
}
