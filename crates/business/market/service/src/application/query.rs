//! Read-side queries for the Market application.

use crate::domain::freshness::MarketFreshness;
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::OrderBook;
use crate::domain::snapshot::{MarketSnapshot, SubscriptionState};
use crate::domain::view::MarketViewKey;
use kairos_domain_types::{Money, Price, PriceDelta, Quantity, Rate};
use rust_decimal::Decimal;
use std::str::FromStr;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OrderBookSide {
    Buy,
    Sell,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionEstimate {
    pub requested_quantity: Quantity,
    pub filled_quantity: Quantity,
    pub notional: Money,
    pub vwap: Price,
    pub slippage_abs: PriceDelta,
    pub slippage_pct: Rate,
}

/// Stable read model for the latest value of one market view.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketObservationResult {
    pub key: MarketViewKey,
    pub observation: MarketObservation,
}

/// Stable read-side access to current Market state.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketQueryResult {
    pub snapshot: MarketSnapshot,
}

impl MarketQueryResult {
    pub(crate) fn new(snapshot: MarketSnapshot) -> Self {
        Self { snapshot }
    }

    pub fn latest(&self, market_id: &str) -> Option<&MarketObservation> {
        self.snapshot
            .latest
            .iter()
            .find_map(|(key, value)| key.ends_with(&format!(":{market_id}")).then_some(value))
    }

    pub fn latest_from_source(
        &self,
        source_id: &str,
        market_id: &str,
    ) -> Option<&MarketObservation> {
        self.snapshot
            .latest
            .get(&format!("{source_id}:{market_id}"))
    }

    pub fn latest_quote(&self, market_id: &str) -> Option<&crate::domain::observations::Quote> {
        match self.latest_view(market_id, "quote", None) {
            Some(MarketObservation::Quote(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_trade(&self, market_id: &str) -> Option<&crate::domain::observations::Trade> {
        match self.latest_view(market_id, "trade", None) {
            Some(MarketObservation::Trade(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_bar(
        &self,
        market_id: &str,
        timeframe: &str,
    ) -> Option<&crate::domain::observations::Bar> {
        match self.latest_view(market_id, "bar", Some(timeframe)) {
            Some(MarketObservation::Bar(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_trade_bar(
        &self,
        market_id: &str,
        timeframe: &str,
    ) -> Option<&crate::domain::observations::TradeBar> {
        match self.latest_view(market_id, "trade_bar", Some(timeframe)) {
            Some(MarketObservation::TradeBar(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_quote_bar(
        &self,
        market_id: &str,
        timeframe: &str,
    ) -> Option<&crate::domain::observations::QuoteBar> {
        match self.latest_view(market_id, "quote_bar", Some(timeframe)) {
            Some(MarketObservation::QuoteBar(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_greeks(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observations::OptionGreeks> {
        match self.latest_view(market_id, "greek", None) {
            Some(MarketObservation::OptionGreeks(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_rate(
        &self,
        market_id: &str,
        rate_id: &str,
    ) -> Option<&crate::domain::observations::Rate> {
        match self.latest_view(market_id, "rate", Some(rate_id)) {
            Some(MarketObservation::Rate(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_ticker_24h(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observations::Ticker24h> {
        match self.latest_view(market_id, "ticker_24h", None) {
            Some(MarketObservation::Ticker24h(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_mark_price(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observations::MarkPrice> {
        match self.latest_view(market_id, "mark_price", None) {
            Some(MarketObservation::MarkPrice(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_index_price(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observations::IndexPrice> {
        match self.latest_view(market_id, "index_price", None) {
            Some(MarketObservation::IndexPrice(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_funding_rate(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observations::FundingRate> {
        match self.latest_view(market_id, "funding_rate", None) {
            Some(MarketObservation::FundingRate(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_open_interest(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observations::OpenInterest> {
        match self.latest_view(market_id, "open_interest", None) {
            Some(MarketObservation::OpenInterest(value)) => Some(value),
            _ => None,
        }
    }

    pub fn latest_instrument_status(
        &self,
        market_id: &str,
    ) -> Option<&crate::domain::observations::InstrumentStatus> {
        match self.latest_view(market_id, "instrument_status", None) {
            Some(MarketObservation::InstrumentStatus(value)) => Some(value),
            _ => None,
        }
    }

    pub fn view(&self, key: &MarketViewKey) -> Option<MarketObservationResult> {
        self.snapshot
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
            .snapshot
            .order_books
            .values()
            .filter(|book| book.market_id == market_id);
        let first = matches.next()?;
        matches.next().is_none().then_some(first)
    }

    pub fn order_book_from_source(&self, source_id: &str, market_id: &str) -> Option<&OrderBook> {
        self.snapshot
            .order_books
            .get(&format!("{source_id}:{market_id}"))
    }

    pub fn subscriptions(&self) -> &[SubscriptionState] {
        &self.snapshot.subscriptions
    }

    pub fn freshness(&self) -> &std::collections::BTreeMap<String, MarketFreshness> {
        &self.snapshot.freshness
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
        kind: &str,
        qualifier: Option<&str>,
    ) -> Option<&MarketObservation> {
        // View keys include source identity. A typed query therefore scans
        // the stable projection rather than guessing the provider source.
        let mut matches = self.snapshot.views.values().filter(|value| {
            value.market_id() == market_id
                && value.view_kind() == kind
                && value.view_qualifier() == qualifier
        });
        let first = matches.next()?;
        // A market can have several provider sources. Do not silently pick a
        // source when the caller used the source-agnostic convenience query.
        matches.next().is_none().then_some(first)
    }
}
