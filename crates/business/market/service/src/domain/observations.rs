use kairos_domain_types::{
    InstrumentId, MarketId, Money, Price, Quantity, Rate as FixedRate, UnixNanos,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Quote {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub bid_price: Option<Price>,
    pub bid_quantity: Option<Quantity>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Trade {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub trade_id: Option<String>,
    pub price: Price,
    pub quantity: Quantity,
    pub cost: Option<Money>,
    pub aggressor_side: Option<String>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Bar {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub timeframe: String,
    pub open: Price,
    pub high: Price,
    pub low: Price,
    pub close: Price,
    pub volume: Option<Quantity>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
    pub derivation: String,
}

/// Bar derived from executed trades. The wrapped Bar keeps the existing OHLCV
/// representation while making the aggregation source explicit in the type.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TradeBar {
    pub bar: Bar,
}

/// Bar derived from quote/BBO observations rather than executed trades.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct QuoteBar {
    pub bar: Bar,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OptionGreeks {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<Price>,
    pub delta: Option<FixedRate>,
    pub gamma: Option<FixedRate>,
    pub vega: Option<FixedRate>,
    pub theta: Option<FixedRate>,
    pub implied_volatility: Option<FixedRate>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
    pub derivation: String,
}

/// A normalized rate observation.
///
/// Rates are deliberately separate from quotes and greeks.  A rate may be
/// attached to a market or instrument, but its identity is the rate kind and
/// `rate_id` (for example a funding rate or an interest-rate curve point).
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Rate {
    pub rate_id: String,
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub basis: String,
    pub value: FixedRate,
    pub mark_price: Option<Price>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Ticker24h {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub last_price: Option<Price>,
    pub bid_price: Option<Price>,
    pub bid_quantity: Option<Quantity>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub open_price: Option<Price>,
    pub high_price: Option<Price>,
    pub low_price: Option<Price>,
    pub volume_base: Option<Quantity>,
    pub volume_quote: Option<Money>,
    pub price_change_abs: Option<Money>,
    pub price_change_pct: Option<FixedRate>,
    pub vwap: Option<Price>,
    pub mark_price: Option<Price>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarkPrice {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub mark_price: Price,
    pub index_price: Option<Price>,
    pub estimated_settlement_price: Option<Price>,
    pub funding_rate: Option<FixedRate>,
    pub next_funding_time_unix_nanos: Option<UnixNanos>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IndexPrice {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub spot_index_price: Option<Price>,
    pub contract_index_price: Option<Price>,
    pub index_price: Option<Price>,
    pub funding_rate: Option<FixedRate>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FundingRate {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub funding_rate: FixedRate,
    pub funding_period_seconds: Option<u64>,
    pub next_funding_time_unix_nanos: Option<UnixNanos>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OpenInterest {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub contracts: Quantity,
    pub quote_value: Option<Money>,
    pub change_24h: Option<Money>,
    pub change_pct_24h: Option<FixedRate>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct InstrumentStatus {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub status: kairos_domain_types::ReferenceStatus,
    pub reason: Option<String>,
    pub effective_at_unix_nanos: Option<UnixNanos>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}

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
    InstrumentStatus(InstrumentStatus),
}

impl MarketObservation {
    pub fn validate(&self) -> Result<(), String> {
        if self.market_id().trim().is_empty()
            || self.instrument_id().trim().is_empty()
            || self.source_id().trim().is_empty()
        {
            return Err("observation market, instrument and source identities are required".into());
        }
        match self {
            Self::Bar(value) if value.timeframe.trim().is_empty() => {
                return Err("bar timeframe is required".into());
            }
            Self::TradeBar(value) if value.bar.timeframe.trim().is_empty() => {
                return Err("trade bar timeframe is required".into());
            }
            Self::QuoteBar(value) if value.bar.timeframe.trim().is_empty() => {
                return Err("quote bar timeframe is required".into());
            }
            Self::Rate(value) => {
                if value.rate_id.trim().is_empty() {
                    return Err("rate_id is required".into());
                }
                if value.basis.trim().is_empty() {
                    return Err("rate basis is required".into());
                }
            }
            Self::InstrumentStatus(value)
                if value.status == kairos_domain_types::ReferenceStatus::Unknown =>
            {
                return Err("instrument status is required".into());
            }
            _ => {}
        }
        Ok(())
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
            Self::InstrumentStatus(value) => &value.instrument_id,
        }
    }

    pub fn market_id(&self) -> &str {
        match self {
            Self::Quote(value) => &value.market_id,
            Self::Trade(value) => &value.market_id,
            Self::Bar(value) => &value.market_id,
            Self::TradeBar(value) => &value.bar.market_id,
            Self::QuoteBar(value) => &value.bar.market_id,
            Self::OptionGreeks(value) => &value.market_id,
            Self::Rate(value) => &value.market_id,
            Self::Ticker24h(value) => &value.market_id,
            Self::MarkPrice(value) => &value.market_id,
            Self::IndexPrice(value) => &value.market_id,
            Self::FundingRate(value) => &value.market_id,
            Self::OpenInterest(value) => &value.market_id,
            Self::InstrumentStatus(value) => &value.market_id,
        }
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
            Self::InstrumentStatus(value) => value.observed_at_unix_nanos,
        }
    }

    pub fn view_kind(&self) -> &'static str {
        match self {
            Self::Quote(_) => "quote",
            Self::Trade(_) => "trade",
            Self::Bar(_) => "bar",
            Self::TradeBar(_) => "trade_bar",
            Self::QuoteBar(_) => "quote_bar",
            Self::OptionGreeks(_) => "greek",
            Self::Rate(_) => "rate",
            Self::Ticker24h(_) => "ticker_24h",
            Self::MarkPrice(_) => "mark_price",
            Self::IndexPrice(_) => "index_price",
            Self::FundingRate(_) => "funding_rate",
            Self::OpenInterest(_) => "open_interest",
            Self::InstrumentStatus(_) => "instrument_status",
        }
    }

    pub fn view_qualifier(&self) -> Option<&str> {
        match self {
            Self::Bar(value) => Some(&value.timeframe),
            Self::TradeBar(value) => Some(&value.bar.timeframe),
            Self::QuoteBar(value) => Some(&value.bar.timeframe),
            Self::Rate(value) => Some(&value.rate_id),
            _ => None,
        }
    }

    pub fn view_key(&self) -> Result<crate::MarketViewKey, String> {
        match self.view_qualifier() {
            Some(qualifier) => crate::MarketViewKey::with_qualifier(
                self.source_id(),
                self.market_id(),
                self.view_kind(),
                qualifier,
            ),
            None => crate::MarketViewKey::new(self.source_id(), self.market_id(), self.view_kind()),
        }
    }

    pub fn source_id(&self) -> &str {
        match self {
            Self::Quote(value) => &value.source_id,
            Self::Trade(value) => &value.source_id,
            Self::Bar(value) => &value.source_id,
            Self::TradeBar(value) => &value.bar.source_id,
            Self::QuoteBar(value) => &value.bar.source_id,
            Self::OptionGreeks(value) => &value.source_id,
            Self::Rate(value) => &value.source_id,
            Self::Ticker24h(value) => &value.source_id,
            Self::MarkPrice(value) => &value.source_id,
            Self::IndexPrice(value) => &value.source_id,
            Self::FundingRate(value) => &value.source_id,
            Self::OpenInterest(value) => &value.source_id,
            Self::InstrumentStatus(value) => &value.source_id,
        }
    }
}
