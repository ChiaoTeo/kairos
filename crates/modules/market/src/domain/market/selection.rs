use kairos_primitives::decimal::Price;
use kairos_primitives::reference::{AssetClass, InstrumentId, InstrumentKind, MarketId, VenueId};
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

use super::ResolvedMarket;

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSelectionQuery {
    pub market_id: Option<MarketId>,
    pub execution_venue_id: Option<VenueId>,
    pub instrument_kind: Option<InstrumentKind>,
    pub asset_type: Option<AssetClass>,
    #[serde(default)]
    pub underlying_instrument_id: Option<InstrumentId>,
    #[serde(default)]
    pub expiry_from_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub expiry_to_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub strike_lower: Option<Price>,
    #[serde(default)]
    pub strike_upper: Option<Price>,
    #[serde(default)]
    pub option_right: Option<String>,
    pub active_only: bool,
}

impl MarketSelectionQuery {
    pub fn matches(&self, market: &ResolvedMarket) -> bool {
        if self
            .market_id
            .as_deref()
            .is_some_and(|value| market.market_id().map(|id| id.as_str()) != Some(value))
            || self
                .execution_venue_id
                .as_ref()
                .is_some_and(|value| market.execution_venue_id.as_ref() != Some(value))
            || self
                .instrument_kind
                .is_some_and(|value| value != market.instrument_kind)
            || self
                .asset_type
                .is_some_and(|value| market.asset_type.as_ref() != Some(&value))
            || self
                .underlying_instrument_id
                .as_deref()
                .is_some_and(|value| market.underlying_instrument_id.as_deref() != Some(value))
            || self
                .expiry_from_unix_nanos
                .is_some_and(|value| market.expiry_unix_nanos.is_none_or(|expiry| expiry < value))
            || self
                .expiry_to_unix_nanos
                .is_some_and(|value| market.expiry_unix_nanos.is_none_or(|expiry| expiry > value))
            || self
                .strike_lower
                .is_some_and(|value| market.strike.is_none_or(|strike| strike < value))
            || self
                .strike_upper
                .is_some_and(|value| market.strike.is_none_or(|strike| strike > value))
            || self.option_right.as_ref().is_some_and(|value| {
                market
                    .option_right
                    .as_deref()
                    .is_none_or(|right| !option_right_matches(right, value))
            })
        {
            return false;
        }
        !self.active_only || market.is_active()
    }
}

fn option_right_matches(market_right: &str, selected: &str) -> bool {
    let selected = selected.trim();
    if selected.eq_ignore_ascii_case("both") {
        return true;
    }
    market_right.eq_ignore_ascii_case(selected)
        || (selected.eq_ignore_ascii_case("call") && market_right.eq_ignore_ascii_case("c"))
        || (selected.eq_ignore_ascii_case("put") && market_right.eq_ignore_ascii_case("p"))
}
