use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

pub mod bar;
pub mod funding_rate;
pub mod identity;
pub mod index_price;
pub mod mark_price;
pub mod open_interest;
pub mod option_greeks;
pub mod order_book;
pub mod quote;
pub mod quote_bar;
pub mod rate;
pub mod ticker_24h;
pub mod trade;
pub mod trade_bar;

pub use bar::Bar;
pub use funding_rate::FundingRate;
pub use identity::{MarketViewKey, ObservationKind, ObservationQualifier, ObservationScope};
pub use index_price::IndexPrice;
pub use mark_price::MarkPrice;
pub use open_interest::OpenInterest;
pub use option_greeks::OptionGreeks;
pub use quote::Quote;
pub use quote_bar::QuoteBar;
pub use rate::Rate;
pub use ticker_24h::Ticker24h;
pub use trade::Trade;
pub use trade_bar::TradeBar;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MarketObservation {
    Quote(Quote),
    Trade(Trade),
    Bar(Bar),
    TradeBar(TradeBar),
    QuoteBar(QuoteBar),
    OptionGreeks(OptionGreeks),
    Rate(Rate),
    Ticker24h(Ticker24h),
    MarkPrice(MarkPrice),
    IndexPrice(IndexPrice),
    FundingRate(FundingRate),
    OpenInterest(OpenInterest),
}

impl MarketObservation {
    pub fn validate(&self) -> Result<(), String> {
        if self.instrument_id().trim().is_empty() || self.provider().trim().is_empty() {
            return Err("observation scope, instrument and source identities are required".into());
        }
        match self {
            Self::Bar(value) if value.timeframe.trim().is_empty() => {
                return Err("bar timeframe is required".into());
            },
            Self::TradeBar(value) if value.bar.timeframe.trim().is_empty() => {
                return Err("trade bar timeframe is required".into());
            },
            Self::QuoteBar(value) if value.bar.timeframe.trim().is_empty() => {
                return Err("quote bar timeframe is required".into());
            },
            Self::Rate(value) => {
                if value.rate_id.trim().is_empty() {
                    return Err("rate_id is required".into());
                }
                if value.basis.trim().is_empty() {
                    return Err("rate basis is required".into());
                }
            },
            _ => {},
        }
        Ok(())
    }

    pub fn kind(&self) -> ObservationKind {
        match self {
            Self::Quote(_) => ObservationKind::Quote,
            Self::Trade(_) => ObservationKind::Trade,
            Self::Bar(_) => ObservationKind::Bar,
            Self::TradeBar(_) => ObservationKind::TradeBar,
            Self::QuoteBar(_) => ObservationKind::QuoteBar,
            Self::OptionGreeks(_) => ObservationKind::OptionGreeks,
            Self::Rate(_) => ObservationKind::Rate,
            Self::Ticker24h(_) => ObservationKind::Ticker24h,
            Self::MarkPrice(_) => ObservationKind::MarkPrice,
            Self::IndexPrice(_) => ObservationKind::IndexPrice,
            Self::FundingRate(_) => ObservationKind::FundingRate,
            Self::OpenInterest(_) => ObservationKind::OpenInterest,
        }
    }

    pub fn instrument_id(&self) -> &str {
        match self {
            Self::Quote(value) => &value.instrument_id,
            Self::Trade(value) => &value.instrument_id,
            Self::Bar(value) => &value.instrument_id,
            Self::TradeBar(value) => &value.bar.instrument_id,
            Self::QuoteBar(value) => &value.bar.instrument_id,
            Self::OptionGreeks(value) => &value.instrument_id,
            Self::Rate(value) => &value.instrument_id,
            Self::Ticker24h(value) => &value.instrument_id,
            Self::MarkPrice(value) => &value.instrument_id,
            Self::IndexPrice(value) => &value.instrument_id,
            Self::FundingRate(value) => &value.instrument_id,
            Self::OpenInterest(value) => &value.instrument_id,
        }
    }

    pub fn scope(&self) -> &ObservationScope {
        match self {
            Self::Quote(value) => &value.scope,
            Self::Trade(value) => &value.scope,
            Self::Bar(value) => &value.scope,
            Self::TradeBar(value) => &value.bar.scope,
            Self::QuoteBar(value) => &value.bar.scope,
            Self::OptionGreeks(value) => &value.scope,
            Self::Rate(value) => &value.scope,
            Self::Ticker24h(value) => &value.scope,
            Self::MarkPrice(value) => &value.scope,
            Self::IndexPrice(value) => &value.scope,
            Self::FundingRate(value) => &value.scope,
            Self::OpenInterest(value) => &value.scope,
        }
    }

    pub fn market_id(&self) -> Option<&kairos_primitives::reference::MarketId> {
        self.scope().market_id()
    }

    pub fn observed_at_unix_nanos(&self) -> UnixNanos {
        match self {
            Self::Quote(value) => value.observed_at_unix_nanos,
            Self::Trade(value) => value.observed_at_unix_nanos,
            Self::Bar(value) => value.observed_at_unix_nanos,
            Self::TradeBar(value) => value.bar.observed_at_unix_nanos,
            Self::QuoteBar(value) => value.bar.observed_at_unix_nanos,
            Self::OptionGreeks(value) => value.observed_at_unix_nanos,
            Self::Rate(value) => value.observed_at_unix_nanos,
            Self::Ticker24h(value) => value.observed_at_unix_nanos,
            Self::MarkPrice(value) => value.observed_at_unix_nanos,
            Self::IndexPrice(value) => value.observed_at_unix_nanos,
            Self::FundingRate(value) => value.observed_at_unix_nanos,
            Self::OpenInterest(value) => value.observed_at_unix_nanos,
        }
    }

    pub fn qualifier(&self) -> Option<&str> {
        match self {
            Self::Bar(value) => Some(&value.timeframe),
            Self::TradeBar(value) => Some(&value.bar.timeframe),
            Self::QuoteBar(value) => Some(&value.bar.timeframe),
            Self::Rate(value) => Some(&value.rate_id),
            _ => None,
        }
    }

    pub fn view_key(&self) -> Result<crate::MarketViewKey, String> {
        match self.qualifier() {
            Some(qualifier) => crate::MarketViewKey::with_qualifier(
                self.provider(),
                self.scope().key(),
                self.kind(),
                qualifier,
            ),
            None => crate::MarketViewKey::new(self.provider(), self.scope().key(), self.kind()),
        }
    }

    pub fn provider(&self) -> &kairos_primitives::market::Provider {
        match self {
            Self::Quote(value) => &value.provider,
            Self::Trade(value) => &value.provider,
            Self::Bar(value) => &value.provider,
            Self::TradeBar(value) => &value.bar.provider,
            Self::QuoteBar(value) => &value.bar.provider,
            Self::OptionGreeks(value) => &value.provider,
            Self::Rate(value) => &value.provider,
            Self::Ticker24h(value) => &value.provider,
            Self::MarkPrice(value) => &value.provider,
            Self::IndexPrice(value) => &value.provider,
            Self::FundingRate(value) => &value.provider,
            Self::OpenInterest(value) => &value.provider,
        }
    }
}
