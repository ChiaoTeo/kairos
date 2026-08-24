//! Read-side queries for the Market application.

use std::str::FromStr;

use kairos_primitives::market::Provider;
use rust_decimal::Decimal;

use super::MarketApplication;
use super::model::{
    ExecutionEstimate, MarketDataAvailability, MarketDataAvailabilityQuery, MarketDataRouteState,
    MarketObservationResult, MarketQueryResult, OrderBookSide,
};
use crate::domain::observation::order_book::OrderBook;
use crate::domain::observation::{MarketObservation, MarketViewKey, ObservationKind};
use crate::domain::subscription::SubscriptionState;
use crate::domain::view::MarketView;

impl MarketApplication {
    pub fn available_market_data(
        &self,
        query: &MarketDataAvailabilityQuery,
    ) -> Vec<MarketDataAvailability> {
        let view = self.actor.current_view();
        let mut result = Vec::new();
        for market in self.actor.market_universe() {
            let Some(market_id) = market.market_id() else {
                continue;
            };
            if query
                .market_id
                .as_ref()
                .is_some_and(|value| value != market_id)
                || query
                    .instrument_id
                    .as_ref()
                    .is_some_and(|value| value != &market.instrument_id)
            {
                continue;
            }
            for route in &market.data_routes {
                if query
                    .provider
                    .as_ref()
                    .is_some_and(|value| value != &route.provider)
                    || query
                        .observation_kind
                        .is_some_and(|kind| !route.observation_kinds.contains(&kind))
                {
                    continue;
                }
                let matching_sources = self
                    .actor
                    .source_states()
                    .filter(|source| source.descriptor.provider.as_ref() == Some(&route.provider))
                    .collect::<Vec<_>>();
                let configured = !matching_sources.is_empty();
                if query.configured_only && !configured {
                    continue;
                }
                let supported = query
                    .observation_kind
                    .is_none_or(|kind| route.observation_kinds.contains(&kind));
                let ready = matching_sources
                    .iter()
                    .any(|source| source.status == crate::domain::source::SourceStatus::Ready);
                if query.ready_only && !ready {
                    continue;
                }
                let freshness = view
                    .freshness
                    .values()
                    .filter(|value| {
                        value.provider == route.provider
                            && value.scope.market_id() == Some(market_id)
                            && query
                                .observation_kind
                                .is_none_or(|kind| kind == value.data_kind)
                    })
                    .map(|value| (value.data_kind, value.status))
                    .collect();
                result.push(MarketDataAvailability {
                    market_id: market_id.clone(),
                    instrument_id: market.instrument_id.clone(),
                    provider: route.provider.clone(),
                    observation_capabilities: route.observation_kinds.iter().copied().collect(),
                    supported_by_adapter: supported,
                    configured_in_workspace: configured,
                    state: if ready {
                        MarketDataRouteState::Ready
                    } else if matching_sources.iter().any(|source| {
                        source.status == crate::domain::source::SourceStatus::Degraded
                    }) {
                        MarketDataRouteState::Degraded
                    } else if configured {
                        MarketDataRouteState::Stopped
                    } else {
                        MarketDataRouteState::Supported
                    },
                    selected: market.selected_provider.as_ref() == Some(&route.provider),
                    pending_reason: (!ready).then(|| {
                        if configured {
                            "provider feed is not ready"
                        } else {
                            "provider is not configured"
                        }
                        .to_string()
                    }),
                    freshness,
                });
            }
        }
        result.sort_by(|left, right| {
            left.market_id
                .cmp(&right.market_id)
                .then_with(|| left.provider.cmp(&right.provider))
        });
        result
    }
}

impl MarketQueryResult {
    pub(crate) fn new(view: MarketView) -> Self {
        Self::from_view(view)
    }

    pub fn latest_quote(&self, market_id: &str) -> Option<&crate::domain::observation::Quote> {
        match self.latest_view(market_id, ObservationKind::Quote, None) {
            Some(MarketObservation::Quote(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_trade(&self, market_id: &str) -> Option<&crate::domain::observation::Trade> {
        match self.latest_view(market_id, ObservationKind::Trade, None) {
            Some(MarketObservation::Trade(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_bar(
        &self,
        market_id: &str,
        timeframe: &str,
    ) -> Option<&crate::domain::observation::Bar> {
        match self.latest_view(market_id, ObservationKind::Bar, Some(timeframe)) {
            Some(MarketObservation::Bar(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_trade_bar(
        &self,
        market_id: &str,
        timeframe: &str,
    ) -> Option<&crate::domain::observation::TradeBar> {
        match self.latest_view(market_id, ObservationKind::TradeBar, Some(timeframe)) {
            Some(MarketObservation::TradeBar(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_quote_bar(
        &self,
        market_id: &str,
        timeframe: &str,
    ) -> Option<&crate::domain::observation::QuoteBar> {
        match self.latest_view(market_id, ObservationKind::QuoteBar, Some(timeframe)) {
            Some(MarketObservation::QuoteBar(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_greeks(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observation::OptionGreeks> {
        match self.latest_view(market_id, ObservationKind::OptionGreeks, None) {
            Some(MarketObservation::OptionGreeks(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_rate(
        &self,
        market_id: &str,
        rate_id: &str,
    ) -> Option<&crate::domain::observation::Rate> {
        match self.latest_view(market_id, ObservationKind::Rate, Some(rate_id)) {
            Some(MarketObservation::Rate(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_ticker_24h(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observation::Ticker24h> {
        match self.latest_view(market_id, ObservationKind::Ticker24h, None) {
            Some(MarketObservation::Ticker24h(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_mark_price(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observation::MarkPrice> {
        match self.latest_view(market_id, ObservationKind::MarkPrice, None) {
            Some(MarketObservation::MarkPrice(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_index_price(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observation::IndexPrice> {
        match self.latest_view(market_id, ObservationKind::IndexPrice, None) {
            Some(MarketObservation::IndexPrice(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_funding_rate(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observation::FundingRate> {
        match self.latest_view(market_id, ObservationKind::FundingRate, None) {
            Some(MarketObservation::FundingRate(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_open_interest(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observation::OpenInterest> {
        match self.latest_view(market_id, ObservationKind::OpenInterest, None) {
            Some(MarketObservation::OpenInterest(value)) => Some(value),
            _ => None,
        }
    }

    pub fn view(&self, key: &MarketViewKey) -> Option<MarketObservationResult> {
        self.view
            .views
            .get(&key.as_str())
            .cloned()
            .map(|observation| MarketObservationResult {
                key: key.clone(),
                observation,
            })
    }

    pub fn order_book(&self, market_id: &str) -> Option<&OrderBook> {
        let mut matches = self
            .view
            .order_books
            .values()
            .filter(|book| book.market_id == market_id);
        let first = matches.next()?;
        matches.next().is_none().then_some(first)
    }

    pub fn order_book_from_provider(
        &self,
        provider: &Provider,
        market_id: &str,
    ) -> Option<&OrderBook> {
        self.view
            .order_books
            .get(&format!("{provider}:{market_id}"))
    }

    pub fn subscriptions(&self) -> &[SubscriptionState] {
        &self.view.subscriptions
    }

    pub fn freshness(
        &self,
    ) -> &std::collections::BTreeMap<String, crate::domain::view::MarketViewFreshness> {
        &self.view.freshness
    }

    pub fn mid_price(&self, market_id: &str) -> Option<String> {
        let quote = self.latest_quote(market_id)?;
        let bid = Decimal::try_new(
            quote.bid_price?.mantissa(),
            u32::from(quote.bid_price?.scale()),
        )
        .ok()?;
        let ask = Decimal::try_new(
            quote.ask_price?.mantissa(),
            u32::from(quote.ask_price?.scale()),
        )
        .ok()?;
        Some(((bid + ask) / Decimal::TWO).normalize().to_string())
    }

    pub fn spread(&self, market_id: &str) -> Option<String> {
        let quote = self.latest_quote(market_id)?;
        let bid = Decimal::try_new(
            quote.bid_price?.mantissa(),
            u32::from(quote.bid_price?.scale()),
        )
        .ok()?;
        let ask = Decimal::try_new(
            quote.ask_price?.mantissa(),
            u32::from(quote.ask_price?.scale()),
        )
        .ok()?;
        Some((ask - bid).normalize().to_string())
    }

    /// Estimate a market order against the selected current order book.
    /// Decimal arithmetic is used deliberately so this read model never
    /// introduces floating-point price or quantity errors.
    pub fn estimate_execution(
        &self,
        provider: &Provider,
        market_id: &str,
        side: OrderBookSide,
        requested_quantity: &str,
    ) -> Option<ExecutionEstimate> {
        let requested = Decimal::from_str(requested_quantity).ok()?;
        if requested <= Decimal::ZERO {
            return None;
        }
        let book = self.order_book_from_provider(provider, market_id)?;
        if !book.synchronized {
            return None;
        }
        let mut remaining = requested;
        let mut filled = Decimal::ZERO;
        let mut notional = Decimal::ZERO;
        let levels = match side {
            OrderBookSide::Buy => &book.asks,
            OrderBookSide::Sell => &book.bids,
        };
        let best_price = Decimal::from_str(&levels.first()?.price.to_string()).ok()?;
        for level in levels {
            if remaining <= Decimal::ZERO {
                break;
            }
            let price = Decimal::from_str(&level.price.to_string()).ok()?;
            let available = Decimal::from_str(&level.quantity.to_string()).ok()?;
            if available <= Decimal::ZERO {
                continue;
            }
            let amount = remaining.min(available);
            filled += amount;
            notional += amount * price;
            remaining -= amount;
        }
        if filled <= Decimal::ZERO {
            return None;
        }
        let vwap = notional / filled;
        let slippage_abs = match side {
            OrderBookSide::Buy => vwap - best_price,
            OrderBookSide::Sell => best_price - vwap,
        };
        let slippage_pct = if best_price.is_zero() {
            Decimal::ZERO
        } else {
            slippage_abs / best_price * Decimal::ONE_HUNDRED
        };
        let to_domain_decimal = |value: Decimal| value.round_dp(18).normalize().to_string();
        let to_price_decimal = |value: Decimal| value.round_dp(16).normalize().to_string();
        let requested_quantity = to_domain_decimal(requested);
        let filled_quantity = to_domain_decimal(filled);
        let notional = to_domain_decimal(notional);
        let vwap = to_price_decimal(vwap);
        let slippage_abs = to_domain_decimal(slippage_abs);
        let slippage_pct = to_domain_decimal(slippage_pct);
        Some(ExecutionEstimate {
            requested_quantity: requested_quantity.parse().ok()?,
            filled_quantity: filled_quantity.parse().ok()?,
            notional: notional.parse().ok()?,
            vwap: vwap.parse().ok()?,
            slippage_abs: slippage_abs.parse().ok()?,
            slippage_pct: slippage_pct.parse().ok()?,
        })
    }

    fn latest_view(
        &self,
        market_id: &str,
        kind: ObservationKind,
        qualifier: Option<&str>,
    ) -> Option<&MarketObservation> {
        // View keys include source identity. A typed query therefore scans
        // the stable current view rather than guessing the provider source.
        let mut matches = self.view.views.values().filter(|value| {
            value.market_id().map(|value| value.as_str()) == Some(market_id)
                && value.kind() == kind
                && value.qualifier() == qualifier
        });
        let first = matches.next()?;
        // A market can have several provider sources. Do not silently pick a
        // source when the caller used the source-agnostic convenience query.
        matches.next().is_none().then_some(first)
    }
}

#[cfg(test)]
mod availability_tests {
    use super::*;
    use crate::domain::source::{FeedDescriptor, MarketFeedId};
    use crate::{ObservationKind, ProviderRouteBinding, ReconcileMarketUniverse, ResolvedMarket};

    fn market() -> ResolvedMarket {
        ResolvedMarket::new_with_binding(
            "market:binance:spot:BTCUSDT",
            "instrument:spot:BTC",
            kairos_primitives::reference::InstrumentKind::Spot,
            "binance",
            ProviderRouteBinding::new("binance", "spot", "BTCUSDT")
                .unwrap()
                .with_observation_capabilities([ObservationKind::Quote, ObservationKind::Trade]),
        )
        .unwrap()
    }

    #[test]
    fn availability_never_fabricates_a_runtime_feed_identity() {
        let mut application = MarketApplication::new("market", 8).unwrap();
        application
            .reconcile_market_universe(ReconcileMarketUniverse {
                generation: 1.into(),
                event_sequence: 1.into(),
                markets: vec![market()],
            })
            .unwrap();
        let unavailable = application.available_market_data(&Default::default());
        assert_eq!(unavailable.len(), 1);
        assert_eq!(unavailable[0].state, MarketDataRouteState::Supported);
        assert!(!unavailable[0].configured_in_workspace);

        application
            .actor
            .register_source(
                FeedDescriptor::for_provider(
                    MarketFeedId::new("binance-primary").unwrap(),
                    "binance",
                    kairos_primitives::reference::ExchangeId::new("binance").unwrap(),
                    "spot",
                    None,
                )
                .unwrap()
                .with_observation_capabilities([ObservationKind::Quote]),
            )
            .unwrap();
        let quote = application.available_market_data(&MarketDataAvailabilityQuery {
            observation_kind: Some(ObservationKind::Quote),
            configured_only: true,
            ..Default::default()
        });
        assert_eq!(quote.len(), 1);
        assert!(quote[0].supported_by_adapter);
        assert_eq!(quote[0].state, MarketDataRouteState::Stopped);

        let trade = application.available_market_data(&MarketDataAvailabilityQuery {
            observation_kind: Some(ObservationKind::Trade),
            configured_only: true,
            ..Default::default()
        });
        assert_eq!(trade.len(), 1);
        assert!(trade[0].supported_by_adapter);
    }
}
