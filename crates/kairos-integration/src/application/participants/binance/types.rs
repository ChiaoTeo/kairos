use crate::application::ConnectionDomainRef;

/// Binance-native service/account domain served by a connection. This is not
/// a financial product taxonomy and does not classify canonical instruments.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum ConnectionDomain {
    Equity,
    Spot,
    CrossMargin,
    IsolatedMargin,
    UsdMFutures,
    CoinMFutures,
    Options,
    Funding,
}

impl ConnectionDomain {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Equity => "equity",
            Self::Spot => "spot",
            Self::CrossMargin => "cross-margin",
            Self::IsolatedMargin => "isolated-margin",
            Self::UsdMFutures => "usd-m-futures",
            Self::CoinMFutures => "coin-m-futures",
            Self::Options => "options",
            Self::Funding => "funding",
        }
    }
}

impl From<ConnectionDomain> for ConnectionDomainRef {
    fn from(value: ConnectionDomain) -> Self {
        ConnectionDomainRef::new(value.as_str()).expect("static Binance connection domain")
    }
}

impl From<ConnectionDomain> for crate::domain::ParticipantInstrumentTypeRef {
    fn from(value: ConnectionDomain) -> Self {
        Self::new(value.as_str()).expect("static Binance participant domain")
    }
}

/// Binance instrument endpoint vocabulary. This is participant-owned request
/// syntax, not a global product family used for registry selection.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum InstrumentType {
    Spot,
    UsdMFutures,
    CoinMFutures,
    Option,
}

impl InstrumentType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Spot => "spot",
            Self::UsdMFutures => "usd-m-futures",
            Self::CoinMFutures => "coin-m-futures",
            Self::Option => "options",
        }
    }

    pub(super) const fn path(self) -> &'static str {
        match self {
            Self::Spot => "/api/v3/exchangeInfo",
            Self::UsdMFutures => "/fapi/v1/exchangeInfo",
            Self::CoinMFutures => "/dapi/v1/exchangeInfo",
            Self::Option => "/eapi/v1/exchangeInfo",
        }
    }
}
