//! Read-side queries for the Market application.

use crate::domain::observation::order_book::OrderBook;
use crate::domain::observation::{MarketObservation, MarketViewKey, ObservationKind};
use crate::domain::subscription::SubscriptionState;
use crate::domain::view::MarketView;
use rust_decimal::Decimal;
use std::str::FromStr;

use super::model::{ExecutionEstimate, MarketObservationResult, MarketQueryResult, OrderBookSide};

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

    pub fn order_book_from_source(&self, source_id: &str, market_id: &str) -> Option<&OrderBook> {
        self.view
            .order_books
            .get(&format!("{source_id}:{market_id}"))
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
        source_id: &str,
        market_id: &str,
        side: OrderBookSide,
        requested_quantity: &str,
    ) -> Option<ExecutionEstimate> {
        let requested = Decimal::from_str(requested_quantity).ok()?;
        if requested <= Decimal::ZERO {
            return None;
        }
        let book = self.order_book_from_source(source_id, market_id)?;
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
        // the stable projection rather than guessing the provider source.
        let mut matches = self.view.views.values().filter(|value| {
            value.market_id() == market_id && value.kind() == kind && value.qualifier() == qualifier
        });
        let first = matches.next()?;
        // A market can have several provider sources. Do not silently pick a
        // source when the caller used the source-agnostic convenience query.
        matches.next().is_none().then_some(first)
    }
}
