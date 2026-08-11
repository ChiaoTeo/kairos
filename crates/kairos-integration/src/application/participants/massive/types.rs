#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum InstrumentType {
    Equity,
    Option,
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum MarketType {
    Equity,
    Option,
}

impl MarketType {
    pub(super) const fn service_type(
        self,
    ) -> crate::services::participants::massive::market_data::MarketType {
        match self {
            Self::Equity => crate::services::participants::massive::market_data::MarketType::Equity,
            Self::Option => crate::services::participants::massive::market_data::MarketType::Option,
        }
    }
}

impl InstrumentType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Equity => "equity",
            Self::Option => "options",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InstrumentQuery {
    pub instrument_type: InstrumentType,
    pub underlying: Option<String>,
}

impl InstrumentQuery {
    pub const fn equities() -> Self {
        Self {
            instrument_type: InstrumentType::Equity,
            underlying: None,
        }
    }

    pub fn options(underlying: Option<String>) -> Self {
        Self {
            instrument_type: InstrumentType::Option,
            underlying: underlying.filter(|value| !value.trim().is_empty()),
        }
    }
}
