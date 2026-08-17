use kairos_primitives::{
    AssetClass, Exchange, InstrumentId, InstrumentKind, MarketId, ProviderProductCode,
    ProviderSymbol,
};
use serde::{Deserialize, Serialize};

use super::ResolvedMarket;
use crate::domain::source::SourceId;

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSelectionQuery {
    pub market_id: Option<MarketId>,
    pub exchange_id: Option<Exchange>,
    pub instrument_kind: Option<InstrumentKind>,
    pub provider_product: Option<ProviderProductCode>,
    pub asset_type: Option<AssetClass>,
    pub provider_symbol: Option<ProviderSymbol>,
    #[serde(default)]
    pub source_id: Option<SourceId>,
    #[serde(default)]
    pub underlying_instrument_id: Option<InstrumentId>,
    pub active_only: bool,
}

impl MarketSelectionQuery {
    pub fn matches(&self, market: &ResolvedMarket) -> bool {
        if self
            .market_id
            .as_deref()
            .is_some_and(|value| market.market_id != *value)
            || self
                .exchange_id
                .as_ref()
                .is_some_and(|value| value != &market.exchange_id)
            || self
                .instrument_kind
                .is_some_and(|value| value != market.instrument_kind)
            || self
                .provider_product
                .as_ref()
                .is_some_and(|value| value != &market.route.provider_product)
            || self
                .asset_type
                .is_some_and(|value| market.asset_type.as_ref() != Some(&value))
            || self
                .provider_symbol
                .as_ref()
                .is_some_and(|value| value != &market.route.provider_symbol)
            || self
                .source_id
                .as_ref()
                .is_some_and(|value| market.source_id.as_ref() != Some(value))
            || self
                .underlying_instrument_id
                .as_deref()
                .is_some_and(|value| market.underlying_instrument_id.as_deref() != Some(value))
        {
            return false;
        }
        !self.active_only || market.is_active()
    }
}
