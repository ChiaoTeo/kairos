#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MarketObservationError {
    MissingIdentity,
    BarTimeframeRequired,
    TradeBarTimeframeRequired,
    QuoteBarTimeframeRequired,
    RateIdRequired,
    RateBasisRequired,
}

impl MarketObservationError {
    pub const fn code(self) -> &'static str {
        match self {
            Self::MissingIdentity => "market.observation.missing_identity",
            Self::BarTimeframeRequired => "market.observation.bar_timeframe_required",
            Self::TradeBarTimeframeRequired => "market.observation.trade_bar_timeframe_required",
            Self::QuoteBarTimeframeRequired => "market.observation.quote_bar_timeframe_required",
            Self::RateIdRequired => "market.observation.rate_id_required",
            Self::RateBasisRequired => "market.observation.rate_basis_required",
        }
    }
}

impl std::fmt::Display for MarketObservationError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::MissingIdentity => {
                "observation scope, instrument and source identities are required"
            },
            Self::BarTimeframeRequired => "bar timeframe is required",
            Self::TradeBarTimeframeRequired => "trade bar timeframe is required",
            Self::QuoteBarTimeframeRequired => "quote bar timeframe is required",
            Self::RateIdRequired => "rate_id is required",
            Self::RateBasisRequired => "rate basis is required",
        })
    }
}

impl std::error::Error for MarketObservationError {}
