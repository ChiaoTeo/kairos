use kairos_primitives::InstrumentKind;

use super::super::{MarketApplication, MarketError};
use crate::domain::market::ResolvedMarket;
use crate::domain::subscription::{
    SubscriptionId, SubscriptionMemberRequirement, SubscriptionStatus,
};

impl MarketApplication {
    pub fn set_subscription_member_requirement(
        &mut self,
        subscription_id: &SubscriptionId,
        member_id: impl Into<String>,
        requirement: SubscriptionMemberRequirement,
    ) -> Result<(), MarketError> {
        self.actor
            .set_member_requirement(subscription_id, member_id, requirement)
            .map_err(MarketError::InvalidSubscription)
    }

    pub fn subscription_status(&self, id: &SubscriptionId) -> Option<SubscriptionStatus> {
        self.actor
            .current_view()
            .subscriptions
            .into_iter()
            .find(|subscription| subscription.id == *id)
            .map(|subscription| subscription.status)
    }
}

pub(crate) fn resolve_market(
    markets: &[ResolvedMarket],
    exchange_id: &str,
    market_type: &str,
    asset_type: Option<&str>,
    source_symbol: &str,
) -> Result<ResolvedMarket, String> {
    let matches: Vec<_> = markets
        .iter()
        .filter(|market| {
            market
                .exchange_id
                .as_ref()
                .is_some_and(|value| exchange_matches(value.as_str(), exchange_id))
                && market.route.provider_product == market_type
                && asset_type.is_none_or(|value| {
                    market.asset_type.map(|class| class.as_str()) == Some(value)
                })
                && market
                    .route
                    .provider_symbol
                    .eq_ignore_ascii_case(source_symbol)
                && market.is_active()
        })
        .collect();
    let [market] = matches.as_slice() else {
        return Err(if matches.is_empty() {
            format!("market universe has no market for {exchange_id}/{market_type}/{source_symbol}")
        } else {
            format!("market universe is ambiguous for {exchange_id}/{market_type}/{source_symbol}")
        });
    };
    Ok((*market).clone())
}

pub(crate) fn resolve_option_markets(
    markets: &[ResolvedMarket],
    exchange_id: &str,
    asset_type: Option<&str>,
    underlying: &str,
) -> Result<Vec<ResolvedMarket>, String> {
    let normalized_underlying = underlying.trim().to_ascii_uppercase();
    let underlying_id = markets
        .iter()
        .filter(|value| {
            matches!(
                value.instrument_kind,
                InstrumentKind::Spot | InstrumentKind::Index
            )
        })
        .find(|value| {
            value
                .route
                .provider_symbol
                .eq_ignore_ascii_case(&normalized_underlying)
                || (exchange_id.eq_ignore_ascii_case("binance")
                    && (value
                        .route
                        .provider_symbol
                        .eq_ignore_ascii_case(&format!("{normalized_underlying}USDT"))
                        || value
                            .route
                            .provider_symbol
                            .eq_ignore_ascii_case(&format!("{normalized_underlying}/USDT"))))
        })
        .map(|value| value.instrument_id.as_str())
        .ok_or_else(|| format!("market universe has no underlying instrument for {underlying}"))?;

    Ok(markets
        .iter()
        .filter(|market| {
            market.instrument_kind == InstrumentKind::Option
                && market
                    .exchange_id
                    .as_ref()
                    .is_some_and(|value| exchange_matches(value.as_str(), exchange_id))
                && asset_type.is_none_or(|value| {
                    market.asset_type.map(|class| class.as_str()) == Some(value)
                })
                && market.underlying_instrument_id.as_deref() == Some(underlying_id)
                && market.is_active()
        })
        .cloned()
        .collect())
}

fn exchange_matches(left: &str, right: &str) -> bool {
    left.strip_prefix("exchange:")
        .unwrap_or(left)
        .eq_ignore_ascii_case(right.strip_prefix("exchange:").unwrap_or(right))
}

#[cfg(test)]
mod tests {
    use kairos_primitives::InstrumentKind;

    use super::resolve_market;
    use crate::domain::market::{MarketDataRoute, ResolvedMarket};

    #[test]
    fn resolves_canonical_exchange_from_business_selector() {
        let market = ResolvedMarket::new(
            "market:binance:spot:BTCUSDT",
            "instrument:binance:spot:BTCUSDT",
            InstrumentKind::Spot,
            "exchange:binance",
            MarketDataRoute::new("access:btc", "binance", "spot", "BTCUSDT").unwrap(),
        )
        .unwrap();

        let resolved = resolve_market(&[market], "binance", "spot", None, "btcusdt").unwrap();
        assert_eq!(
            resolved.exchange_id.as_ref().unwrap().as_str(),
            "exchange:binance"
        );
    }
}
