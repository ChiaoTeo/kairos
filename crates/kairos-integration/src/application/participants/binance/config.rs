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

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceConnectionConfig {
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
