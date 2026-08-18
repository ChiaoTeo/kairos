//! Configuration for Hyperliquid API connections.

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidRestConfig {
    pub binding_id: String,
    pub environment: String,
    pub endpoint: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidAccountRestConfig {
    pub connection: HyperliquidRestConfig,
    pub address: String,
}

#[derive(Clone)]
pub struct HyperliquidExchangeRestConfig {
    pub binding_id: String,
    pub environment: String,
    pub endpoint: String,
    pub private_key: secrecy::SecretString,
}

impl std::fmt::Debug for HyperliquidExchangeRestConfig {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("HyperliquidExchangeRestConfig")
            .field("binding_id", &self.binding_id)
            .field("environment", &self.environment)
            .field("endpoint", &self.endpoint)
            .field("private_key", &"[REDACTED]")
            .finish()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidWebSocketConfig {
    pub binding_id: String,
    pub environment: String,
    pub endpoint: String,
    pub event_capacity: usize,
    pub user: Option<HyperliquidUserStreamConfig>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HyperliquidUserStreamConfig {
    pub address: String,
    pub segment_key: String,
}
