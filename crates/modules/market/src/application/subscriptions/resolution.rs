use kairos_primitives::decimal::Price;
use kairos_primitives::market::SourceId;
use kairos_primitives::reference::{InstrumentId, InstrumentKind, MarketId};
use kairos_primitives::time::UnixNanos;

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
                    .subscription_symbol
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

pub(crate) fn resolve_market_by_id(
    markets: &[ResolvedMarket],
    market_id: &MarketId,
    source_id: Option<&SourceId>,
) -> Result<ResolvedMarket, String> {
    let matches: Vec<_> = markets
        .iter()
        .filter(|market| {
            market.market_id() == Some(market_id)
                && source_id.is_none_or(|selected| market.source_id.as_ref() == Some(selected))
                && market.is_active()
        })
        .collect();
    let [market] = matches.as_slice() else {
        return Err(if matches.is_empty() {
            match source_id {
                Some(source_id) => {
                    format!("market universe has no market {market_id} from source {source_id}")
                },
                None => format!("market universe has no market {market_id}"),
            }
        } else {
            format!("market universe is ambiguous for market {market_id}; select source_id")
        });
    };
    Ok((*market).clone())
}

#[derive(Clone, Debug, Default)]
pub(crate) struct OptionSelectionFilter {
    pub(crate) underlying_market_id: Option<MarketId>,
    pub(crate) underlying_instrument_id: Option<InstrumentId>,
    pub(crate) underlying_symbol: Option<String>,
    pub(crate) expiry_from_unix_nanos: Option<UnixNanos>,
    pub(crate) expiry_to_unix_nanos: Option<UnixNanos>,
    pub(crate) strike_lower: Option<Price>,
    pub(crate) strike_upper: Option<Price>,
    pub(crate) option_right: Option<String>,
    pub(crate) limit: Option<usize>,
    pub(crate) source_ids: Vec<SourceId>,
}

pub(crate) fn resolve_option_markets(
    markets: &[ResolvedMarket],
    exchange_id: &str,
    asset_type: Option<&str>,
    filter: &OptionSelectionFilter,
) -> Result<Vec<ResolvedMarket>, String> {
    let underlying_id = resolve_underlying_instrument_id(markets, exchange_id, filter)?;

    let mut selected = markets
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
                && market.underlying_instrument_id.as_ref() == Some(&underlying_id)
                && filter.expiry_from_unix_nanos.is_none_or(|value| {
                    market
                        .expiry_unix_nanos
                        .is_some_and(|expiry| expiry >= value)
                })
                && filter.expiry_to_unix_nanos.is_none_or(|value| {
                    market
                        .expiry_unix_nanos
                        .is_some_and(|expiry| expiry <= value)
                })
                && filter
                    .strike_lower
                    .is_none_or(|value| market.strike.is_some_and(|strike| strike >= value))
                && filter
                    .strike_upper
                    .is_none_or(|value| market.strike.is_some_and(|strike| strike <= value))
                && filter.option_right.as_ref().is_none_or(|right| {
                    market
                        .option_right
                        .as_deref()
                        .is_some_and(|value| option_right_matches(value, right))
                })
                && (filter.source_ids.is_empty()
                    || market
                        .source_id
                        .as_ref()
                        .is_some_and(|source| filter.source_ids.contains(source)))
                && market.is_active()
        })
        .cloned()
        .collect::<Vec<_>>();
    selected.sort_by(|left, right| {
        left.expiry_unix_nanos
            .cmp(&right.expiry_unix_nanos)
            .then_with(|| left.strike.cmp(&right.strike))
            .then_with(|| left.option_right.cmp(&right.option_right))
            .then_with(|| left.member_id().cmp(&right.member_id()))
    });
    if let Some(limit) = filter.limit {
        selected.truncate(limit);
    }
    Ok(selected)
}

fn resolve_underlying_instrument_id(
    markets: &[ResolvedMarket],
    exchange_id: &str,
    filter: &OptionSelectionFilter,
) -> Result<InstrumentId, String> {
    if let Some(instrument_id) = filter.underlying_instrument_id.as_ref() {
        return Ok(instrument_id.clone());
    }
    if let Some(market_id) = filter.underlying_market_id.as_ref() {
        return markets
            .iter()
            .find(|market| market.market_id() == Some(market_id) && market.is_active())
            .map(|market| market.instrument_id.clone())
            .ok_or_else(|| format!("market universe has no underlying market {market_id}"));
    }
    let Some(underlying) = filter.underlying_symbol.as_deref() else {
        return Err("option selection requires an underlying".into());
    };
    let normalized_underlying = underlying.trim().to_ascii_uppercase();
    markets
        .iter()
        .filter(|value| {
            matches!(
                value.instrument_kind,
                InstrumentKind::Spot | InstrumentKind::Index | InstrumentKind::Equity
            )
        })
        .find(|value| {
            value
                .route
                .subscription_symbol
                .eq_ignore_ascii_case(&normalized_underlying)
                || (exchange_id.eq_ignore_ascii_case("binance")
                    && (value
                        .route
                        .subscription_symbol
                        .eq_ignore_ascii_case(&format!("{normalized_underlying}USDT"))
                        || value
                            .route
                            .subscription_symbol
                            .eq_ignore_ascii_case(&format!("{normalized_underlying}/USDT"))))
        })
        .map(|value| value.instrument_id.clone())
        .ok_or_else(|| format!("market universe has no underlying instrument for {underlying}"))
}

fn option_right_matches(market_right: &str, selected: &str) -> bool {
    selected.eq_ignore_ascii_case("both")
        || market_right.eq_ignore_ascii_case(selected)
        || (selected.eq_ignore_ascii_case("call") && market_right.eq_ignore_ascii_case("c"))
        || (selected.eq_ignore_ascii_case("put") && market_right.eq_ignore_ascii_case("p"))
}

fn exchange_matches(left: &str, right: &str) -> bool {
    left.strip_prefix("exchange:")
        .unwrap_or(left)
        .eq_ignore_ascii_case(right.strip_prefix("exchange:").unwrap_or(right))
}

#[cfg(test)]
mod tests {
    use kairos_primitives::decimal::Price;
    use kairos_primitives::market::SourceId;
    use kairos_primitives::reference::InstrumentKind;
    use kairos_primitives::time::UnixNanos;

    use super::{
        OptionSelectionFilter, resolve_market, resolve_market_by_id, resolve_option_markets,
    };
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

    #[test]
    fn resolves_one_source_leg_for_explicit_market_identity() {
        let base = ResolvedMarket::new(
            "market:exchange:nasdaq:equity:AAPL",
            "instrument:equity:US:AAPL:common",
            InstrumentKind::Equity,
            "exchange:nasdaq",
            MarketDataRoute::new("access:aapl", "massive", "equity", "AAPL").unwrap(),
        )
        .unwrap();
        let massive = base.clone().with_source("massive-equity").unwrap();
        let binance = base.with_source("binance-equity").unwrap();
        let market_id =
            kairos_primitives::reference::MarketId::new("market:exchange:nasdaq:equity:AAPL")
                .unwrap();
        let source_id = kairos_primitives::market::SourceId::new("binance-equity").unwrap();

        let resolved =
            resolve_market_by_id(&[massive, binance], &market_id, Some(&source_id)).unwrap();

        assert_eq!(resolved.source_id.as_ref(), Some(&source_id));
    }

    #[test]
    fn resolves_option_selection_with_filter_and_sources() {
        let underlying_market_id =
            kairos_primitives::reference::MarketId::new("market:exchange:nasdaq:equity:SPY")
                .unwrap();
        let underlying = ResolvedMarket::new(
            underlying_market_id.as_str(),
            "instrument:equity:US:SPY:common",
            InstrumentKind::Equity,
            "exchange:nasdaq",
            MarketDataRoute::new("access:spy", "massive", "equity", "SPY").unwrap(),
        )
        .unwrap();
        let call_450 = option_market("450", "CALL", 450, "massive-options");
        let put_450 = option_market("450", "PUT", 450, "massive-options");
        let call_470 = option_market("470", "CALL", 470, "massive-options");
        let call_450_binance = option_market("450B", "CALL", 450, "binance-options");
        let source = SourceId::new("massive-options").unwrap();

        let selected = resolve_option_markets(
            &[underlying, call_450, put_450, call_470, call_450_binance],
            "nasdaq",
            None,
            &OptionSelectionFilter {
                underlying_market_id: Some(underlying_market_id),
                strike_lower: Some(Price::new(440, 0).unwrap()),
                strike_upper: Some(Price::new(460, 0).unwrap()),
                option_right: Some("call".into()),
                source_ids: vec![source],
                limit: Some(1),
                ..Default::default()
            },
        )
        .unwrap();

        assert_eq!(selected.len(), 1);
        assert_eq!(selected[0].option_right.as_deref(), Some("CALL"));
        assert_eq!(
            selected[0].source_id.as_ref().map(SourceId::as_str),
            Some("massive-options")
        );
        assert_eq!(selected[0].strike, Some(Price::new(450, 0).unwrap()));
    }

    fn option_market(key: &str, right: &str, strike: i64, source_id: &str) -> ResolvedMarket {
        let mut market = ResolvedMarket::new(
            format!("market:opra:option:SPY:{key}"),
            format!("instrument:option:SPY:{key}"),
            InstrumentKind::Option,
            "exchange:nasdaq",
            MarketDataRoute::new(
                format!("access:spy:{key}"),
                "massive",
                "options",
                format!("O:SPY{key}"),
            )
            .unwrap(),
        )
        .unwrap()
        .with_source(source_id)
        .unwrap();
        market.underlying_instrument_id = Some(
            kairos_primitives::reference::InstrumentId::new("instrument:equity:US:SPY:common")
                .unwrap(),
        );
        market.expiry_unix_nanos = Some(UnixNanos::new(1_800_000_000_000_000_000));
        market.strike = Some(Price::new(strike, 0).unwrap());
        market.option_right = Some(right.into());
        market
    }
}
