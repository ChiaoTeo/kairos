use std::path::PathBuf;

use secrecy::SecretString;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BinanceQuotaAllocation {
    pub request_weight_per_minute: u32,
    pub cancel_reserve_weight: u32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceSharedQuotaConfig {
    /// One host-local mmap ledger shared by every business process using the
    /// same provider egress scope.
    pub ledger_path: PathBuf,
    /// Stable deployment identity for the public NAT/proxy egress seen by
    /// Binance. This is deliberately not a network-interface name.
    pub egress_scope_id: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BinancePrincipalOrderQuotaAllocation {
    pub orders_per_10_seconds: u32,
    pub orders_per_day: u32,
}

/// Spot API-family endpoint and quota configuration.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceSpotConnectionConfig {
    pub environment: String,
    pub rest_base_url: String,
    pub quota: BinanceQuotaAllocation,
    pub shared_quota: Option<BinanceSharedQuotaConfig>,
}

/// USD-M or COIN-M Futures API-family endpoint and quota configuration.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceFuturesConnectionConfig {
    pub environment: String,
    pub rest_base_url: String,
    pub quota: BinanceQuotaAllocation,
    pub shared_quota: Option<BinanceSharedQuotaConfig>,
}

/// Options API-family endpoint and quota configuration.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceOptionsConnectionConfig {
    pub environment: String,
    pub rest_base_url: String,
    pub quota: BinanceQuotaAllocation,
    pub shared_quota: Option<BinanceSharedQuotaConfig>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceSpotChannelConfig {
    pub websocket_api_url: String,
    /// Maximum unread events buffered by one projected private channel before
    /// it fails explicitly with backpressure/resync required.
    pub event_queue_capacity: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceFuturesChannelConfig {
    pub websocket_stream_url: String,
    pub event_queue_capacity: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceMarginChannelConfig {
    pub websocket_stream_url: String,
    /// Required for isolated margin because Binance issues one listen key per
    /// symbol; absent for cross margin.
    pub isolated_symbol: Option<String>,
    pub event_queue_capacity: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceOptionsChannelConfig {
    /// Base private-stream endpoint. The listen key is appended as the final
    /// path segment (for example `wss://nbstream.binance.com/eoptions/private/stream`).
    pub websocket_stream_url: String,
    pub event_queue_capacity: usize,
}

#[derive(Clone)]
pub struct BinancePrincipalConfig {
    pub binding_id: String,
    pub principal_id: Option<String>,
    pub api_key: SecretString,
    pub secret: SecretString,
    /// Account-scoped unfilled order limits. Values are deployment
    /// allocations discovered/configured from Binance rate-limit metadata.
    pub principal_quota: Option<BinancePrincipalOrderQuotaAllocation>,
}
