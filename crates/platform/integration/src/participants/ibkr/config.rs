#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IbkrAccountQueryConfig {
    pub environment: String,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
    pub account_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IbkrAccountStreamConfig {
    pub environment: String,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
    pub account_id: String,
    pub segment_key: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IbkrOrderConfig {
    pub environment: String,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
    pub account_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IbkrExecutionStreamConfig {
    pub environment: String,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
    pub account_id: String,
    pub symbol: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IbkrMarketDataConfig {
    pub environment: String,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
    pub exchange: String,
    pub currency: String,
}
