use secrecy::SecretString;

use super::InstrumentQuery;

#[derive(Clone)]
pub struct MassiveRestConfig {
    pub environment: String,
    pub endpoint: String,
    pub api_key: SecretString,
    pub instrument_query: InstrumentQuery,
}

#[derive(Clone)]
pub struct MassiveFuturesRestConfig {
    pub environment: String,
    pub endpoint: String,
    pub api_key: SecretString,
    pub product_code: Option<String>,
    pub as_of: Option<String>,
}

#[derive(Clone)]
pub struct MassiveIndicesRestConfig {
    pub environment: String,
    pub endpoint: String,
    pub api_key: SecretString,
    pub as_of: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MassiveCurrencyMarket {
    Forex,
    Crypto,
}

impl MassiveCurrencyMarket {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Forex => "fx",
            Self::Crypto => "crypto",
        }
    }

    pub const fn domain(self) -> &'static str {
        match self {
            Self::Forex => "forex.rest",
            Self::Crypto => "crypto.rest",
        }
    }
}

#[derive(Clone)]
pub struct MassiveCurrenciesRestConfig {
    pub environment: String,
    pub endpoint: String,
    pub api_key: SecretString,
    pub market: MassiveCurrencyMarket,
    pub as_of: Option<String>,
}

#[derive(Clone)]
pub struct MassiveWebSocketConfig {
    pub environment: String,
    pub endpoint: String,
    pub api_key: SecretString,
    pub event_capacity: usize,
}
