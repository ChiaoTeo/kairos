//! Configuration for concrete OKX transport connections.

use secrecy::SecretString;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxRestConfig {
    pub environment: String,
    pub endpoint: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxWebSocketConfig {
    pub environment: String,
    pub endpoint: String,
    pub event_capacity: usize,
}

#[derive(Clone)]
pub struct OkxCredential {
    pub principal_id: String,
    pub api_key: SecretString,
    pub secret: SecretString,
    pub passphrase: SecretString,
}

#[derive(Clone)]
pub struct OkxPrivateRestConfig {
    pub connection: OkxRestConfig,
    pub credential: OkxCredential,
}

#[derive(Clone)]
pub struct OkxPrivateWebSocketConfig {
    pub connection: OkxWebSocketConfig,
    pub rest_endpoint: String,
    pub credential: OkxCredential,
    pub segment_key: String,
    pub trading_mode: String,
}
