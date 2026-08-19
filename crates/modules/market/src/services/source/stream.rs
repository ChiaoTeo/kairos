//! Subscription conversion for connections polled directly by Conflux.

use kairos_conflux::{
    IntegrationError, MarketDataKind, MarketFeed, MarketSubscriptionId, MarketSubscriptionOutcome,
    MarketSubscriptionRequest,
};

use crate::domain::market::ResolvedMarket;

pub(crate) fn subscription_request(
    market: &ResolvedMarket,
) -> Result<MarketSubscriptionRequest, IntegrationError> {
    let symbol = kairos_primitives::ParticipantSymbol::new(market.route.provider_symbol.as_str())
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
    for capability in &market.route.observation_capabilities {
        match capability {
            crate::ObservationKind::Quote => add(MarketDataKind::Quote, None),
            crate::ObservationKind::Trade => add(MarketDataKind::Trade, None),
            crate::ObservationKind::Bar => add(MarketDataKind::Bar, Some("1m".into())),
            crate::ObservationKind::TradeBar => add(MarketDataKind::TradeBar, Some("1m".into())),
            crate::ObservationKind::QuoteBar => add(MarketDataKind::QuoteBar, Some("1m".into())),
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
