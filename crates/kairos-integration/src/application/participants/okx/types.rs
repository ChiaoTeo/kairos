use crate::application::ConnectionDomainRef;

/// OKX service domain. It is participant-owned and intentionally distinct
/// from OKX's `instType` request filter.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum ConnectionDomain {
    Trading,
    Funding,
    MarketData,
}

impl ConnectionDomain {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Trading => "trading",
            Self::Funding => "funding",
            Self::MarketData => "market-data",
        }
    }
}

impl From<ConnectionDomain> for ConnectionDomainRef {
    fn from(value: ConnectionDomain) -> Self {
        ConnectionDomainRef::new(value.as_str()).expect("static OKX connection domain")
    }
}

/// Exact OKX `instType` vocabulary used by trading-account operations. This
/// is a provider request parameter, not a cross-provider product family.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum InstrumentType {
    Spot,
    Margin,
    Swap,
    Futures,
    Option,
}

impl InstrumentType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Spot => "spot",
            Self::Margin => "margin",
            Self::Swap => "swap",
            Self::Futures => "futures",
            Self::Option => "option",
        }
    }

    pub(super) const fn api_value(self) -> &'static str {
        match self {
            Self::Spot => "SPOT",
            Self::Margin => "MARGIN",
            Self::Swap => "SWAP",
            Self::Futures => "FUTURES",
            Self::Option => "OPTION",
        }
    }
}

impl From<InstrumentType> for crate::domain::ParticipantInstrumentTypeRef {
    fn from(value: InstrumentType) -> Self {
        Self::new(value.as_str()).expect("static OKX instrument type")
    }
}

/// Exact OKX `tdMode` vocabulary. It is kept separate from `instType`
/// because OKX validates the two dimensions independently.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum TradingMode {
    Cash,
    Cross,
    Isolated,
}

impl TradingMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Cash => "cash",
            Self::Cross => "cross",
            Self::Isolated => "isolated",
        }
    }
}
