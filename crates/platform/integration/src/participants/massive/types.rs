#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum InstrumentType {
    Equity,
    Option,
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

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MassiveCashDividend {
    pub id: String,
    pub ticker: String,
    pub ex_dividend_date: String,
    pub declaration_date: Option<String>,
    pub record_date: Option<String>,
    pub pay_date: Option<String>,
    pub cash_amount: Option<String>,
    pub split_adjusted_cash_amount: Option<String>,
    pub historical_adjustment_factor: Option<String>,
    pub currency: Option<String>,
    pub distribution_type: Option<String>,
    pub frequency: Option<u32>,
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
