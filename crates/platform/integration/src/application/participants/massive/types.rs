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
    pub as_of: Option<String>,
    pub expiration_date_gte: Option<String>,
    pub expiration_date_lte: Option<String>,
    pub contract_type: Option<String>,
}

impl InstrumentQuery {
    pub const fn equities() -> Self {
        Self {
            instrument_type: InstrumentType::Equity,
            underlying: None,
            as_of: None,
            expiration_date_gte: None,
            expiration_date_lte: None,
            contract_type: None,
        }
    }

    pub fn options(underlying: Option<String>) -> Self {
        Self {
            instrument_type: InstrumentType::Option,
            underlying: underlying.filter(|value| !value.trim().is_empty()),
            as_of: None,
            expiration_date_gte: None,
            expiration_date_lte: None,
            contract_type: None,
        }
    }

    pub fn as_of(mut self, value: impl Into<String>) -> Self {
        self.as_of = non_empty(value);
        self
    }

    pub fn expiration_between(mut self, start: impl Into<String>, end: impl Into<String>) -> Self {
        self.expiration_date_gte = non_empty(start);
        self.expiration_date_lte = non_empty(end);
        self
    }

    pub fn contract_type(mut self, value: impl Into<String>) -> Self {
        self.contract_type = non_empty(value);
        self
    }
}

fn non_empty(value: impl Into<String>) -> Option<String> {
    let value = value.into();
    (!value.trim().is_empty()).then_some(value)
}
