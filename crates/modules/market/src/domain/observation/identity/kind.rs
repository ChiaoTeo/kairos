use serde::{Deserialize, Serialize};

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

    /// Parse aliases only at the public selector boundary. Domain state keeps
    /// the canonical enum.
    pub fn parse_selector(value: &str) -> Result<Self, String> {
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
            other => Err(format!("unsupported market observation kind: {other}")),
        }
    }
}

impl std::fmt::Display for ObservationKind {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}
