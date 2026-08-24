use serde::{Deserialize, Serialize};

use crate::DomainTypeError;
use crate::text::text_type;

text_type!(SubscriptionId);
text_type!(SubscriptionSymbol);

/// Stable identity of a Market/Reference data provider.
///
/// A provider is the party a strategy may select and audit (for example
/// Massive or Binance). It deliberately does not identify an adapter,
/// connection, feed instance, transport, or provider-native product.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(transparent)]
pub struct Provider(String);

impl Provider {
    pub const MASSIVE: &'static str = "massive";
    pub const BINANCE: &'static str = "binance";
    pub const OKX: &'static str = "okx";
    pub const HYPERLIQUID: &'static str = "hyperliquid";
    pub const IBKR: &'static str = "ibkr";

    pub fn new(value: impl Into<String>) -> Result<Self, DomainTypeError> {
        let value = value.into();
        if value.is_empty() {
            return Err(DomainTypeError::Empty {
                type_name: "Provider",
            });
        }
        if value.trim() != value {
            return Err(DomainTypeError::Invalid {
                type_name: "Provider",
                reason: "leading or trailing whitespace is not allowed",
            });
        }
        if value.chars().any(char::is_whitespace) {
            return Err(DomainTypeError::Invalid {
                type_name: "Provider",
                reason: "whitespace is not allowed",
            });
        }
        if value != value.to_ascii_lowercase() {
            return Err(DomainTypeError::Invalid {
                type_name: "Provider",
                reason: "provider identity must use lowercase canonical spelling",
            });
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn into_inner(self) -> String {
        self.0
    }
}

impl<'de> Deserialize<'de> for Provider {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        Self::new(String::deserialize(deserializer)?).map_err(serde::de::Error::custom)
    }
}

impl AsRef<str> for Provider {
    fn as_ref(&self) -> &str {
        self.as_str()
    }
}

impl std::ops::Deref for Provider {
    type Target = str;

    fn deref(&self) -> &Self::Target {
        self.as_str()
    }
}

impl std::fmt::Display for Provider {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl TryFrom<String> for Provider {
    type Error = DomainTypeError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl TryFrom<&str> for Provider {
    type Error = DomainTypeError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

/// Closed vocabulary for observations published by Market data sources.
///
/// This value crosses the Market domain/contract boundary, so it lives with
/// the shared Market primitives rather than being duplicated by each model.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ObservationKind {
    Quote,
    Trade,
    Bar,
    TradeBar,
    QuoteBar,
    Ticker24h,
    OptionGreeks,
    Rate,
    MarkPrice,
    IndexPrice,
    FundingRate,
    OpenInterest,
    OrderBook,
}

impl ObservationKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Quote => "quote",
            Self::Trade => "trade",
            Self::Bar => "bar",
            Self::TradeBar => "trade_bar",
            Self::QuoteBar => "quote_bar",
            Self::Ticker24h => "ticker_24h",
            Self::OptionGreeks => "option_greeks",
            Self::Rate => "rate",
            Self::MarkPrice => "mark_price",
            Self::IndexPrice => "index_price",
            Self::FundingRate => "funding_rate",
            Self::OpenInterest => "open_interest",
            Self::OrderBook => "order_book",
        }
    }

    pub fn parse_selector(value: &str) -> Result<Self, DomainTypeError> {
        match value.trim().to_ascii_lowercase().as_str() {
            "quote" => Ok(Self::Quote),
            "trade" => Ok(Self::Trade),
            "bar" => Ok(Self::Bar),
            "trade_bar" => Ok(Self::TradeBar),
            "quote_bar" => Ok(Self::QuoteBar),
            "ticker_24h" => Ok(Self::Ticker24h),
            "greek" | "greeks" | "option_greeks" => Ok(Self::OptionGreeks),
            "rate" => Ok(Self::Rate),
            "mark_price" => Ok(Self::MarkPrice),
            "index_price" => Ok(Self::IndexPrice),
            "funding_rate" => Ok(Self::FundingRate),
            "open_interest" => Ok(Self::OpenInterest),
            "orderbook" | "order_book" => Ok(Self::OrderBook),
            _ => Err(DomainTypeError::Invalid {
                type_name: "ObservationKind",
                reason: "unsupported market observation kind",
            }),
        }
    }
}

impl std::fmt::Display for ObservationKind {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}
