//! Subscription conversion for connections polled directly by Conflux.

use kairos_conflux::{
    IntegrationError, MarketDataKind, MarketFeed, MarketSubscriptionId, MarketSubscriptionOutcome,
    MarketSubscriptionRequest,
};

use crate::domain::market::ResolvedMarket;

pub(crate) fn subscription_request(
    market: &ResolvedMarket,
) -> Result<MarketSubscriptionRequest, IntegrationError> {
    let binding = market.runtime_route().ok_or_else(|| {
        IntegrationError::InvalidRequest("resolved Market has no runtime provider binding".into())
    })?;
    let symbol = kairos_primitives::integration::ParticipantSymbol::new(
        binding.subscription_symbol.as_str(),
    )
    .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
    let mut feeds = Vec::new();
    let mut add = |kind, interval| {
        feeds.push(MarketFeed {
            kind,
            symbol: Some(symbol.clone()),
            interval,
            depth: (kind == MarketDataKind::OrderBook).then_some(100),
            update_speed_millis: None,
        });
    };
    for selector in market.observation_requirements() {
        let Some(capability) = selector.kind else {
            continue;
        };
        match capability {
            crate::ObservationKind::Quote => add(MarketDataKind::Quote, None),
            crate::ObservationKind::Trade => add(MarketDataKind::Trade, None),
            crate::ObservationKind::Bar => add(
                MarketDataKind::Bar,
                Some(selector.qualifier.unwrap_or_else(|| "1m".into())),
            ),
            crate::ObservationKind::TradeBar => add(
                MarketDataKind::TradeBar,
                Some(selector.qualifier.unwrap_or_else(|| "1m".into())),
            ),
            crate::ObservationKind::QuoteBar => add(
                MarketDataKind::QuoteBar,
                Some(selector.qualifier.unwrap_or_else(|| "1m".into())),
            ),
            crate::ObservationKind::Ticker24h => add(MarketDataKind::Ticker24h, None),
            crate::ObservationKind::OptionGreeks => add(MarketDataKind::Greeks, None),
            crate::ObservationKind::MarkPrice => add(MarketDataKind::MarkPrice, None),
            crate::ObservationKind::IndexPrice => add(MarketDataKind::IndexPrice, None),
            crate::ObservationKind::FundingRate | crate::ObservationKind::Rate => {
                add(MarketDataKind::FundingRate, None)
            },
            crate::ObservationKind::OpenInterest => add(MarketDataKind::OpenInterest, None),
            crate::ObservationKind::OrderBook => add(MarketDataKind::OrderBook, None),
        }
    }
    if feeds.is_empty() {
        feeds
            .push(MarketFeed::quote(symbol.to_string()).map_err(IntegrationError::InvalidRequest)?);
    }
    MarketSubscriptionRequest::new(feeds).map_err(IntegrationError::InvalidRequest)
}

pub(crate) fn confirmed_subscription(
    outcome: MarketSubscriptionOutcome<kairos_conflux::MarketSubscription>,
) -> Result<MarketSubscriptionId, IntegrationError> {
    match outcome {
        MarketSubscriptionOutcome::Confirmed(subscription) => Ok(subscription.id),
        MarketSubscriptionOutcome::Rejected(rejection) => {
            Err(IntegrationError::InvalidRequest(rejection.message))
        },
        MarketSubscriptionOutcome::Indeterminate { reason, .. } => {
            Err(IntegrationError::ResyncRequired(format!(
                "market subscription outcome is indeterminate: {reason}"
            )))
        },
    }
}

pub(crate) fn confirmed_unsubscription(
    outcome: MarketSubscriptionOutcome<()>,
) -> Result<(), IntegrationError> {
    match outcome {
        MarketSubscriptionOutcome::Confirmed(()) => Ok(()),
        MarketSubscriptionOutcome::Rejected(rejection) => {
            Err(IntegrationError::InvalidRequest(rejection.message))
        },
        MarketSubscriptionOutcome::Indeterminate { reason, .. } => {
            Err(IntegrationError::ResyncRequired(format!(
                "market unsubscription outcome is indeterminate: {reason}"
            )))
        },
    }
}

#[cfg(test)]
mod tests {
    use kairos_primitives::reference::InstrumentKind;

    use super::*;
    use crate::domain::market::ProviderRouteBinding;

    #[test]
    fn bar_qualifier_becomes_provider_interval() {
        let route = ProviderRouteBinding::new("binance", "spot", "BTCUSDT")
            .unwrap()
            .with_observation_capabilities([crate::ObservationKind::Bar]);
        let mut market = ResolvedMarket::new_with_binding(
            "market:exchange:binance:crypto:BTCUSDT",
            "instrument:crypto:BTCUSDT",
            InstrumentKind::Spot,
            "exchange:binance",
            route,
        )
        .unwrap();
        market
            .select_observations(&[crate::ObservationSelector::parse("bar:5m").unwrap()])
            .unwrap();

        let request = subscription_request(&market).unwrap();
        assert_eq!(request.feeds.len(), 1);
        assert_eq!(request.feeds[0].kind, MarketDataKind::Bar);
        assert_eq!(request.feeds[0].interval.as_deref(), Some("5m"));
    }
}
