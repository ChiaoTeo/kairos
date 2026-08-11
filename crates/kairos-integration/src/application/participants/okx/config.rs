use std::path::PathBuf;

use secrecy::SecretString;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxSharedQuotaConfig {
    pub ledger_path: PathBuf,
    /// Stable identity for the host/NAT/proxy egress seen by OKX.
    pub egress_scope_id: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct OkxPrincipalQuotaAllocation {
    /// Conservative aggregate allocation for private REST queries. Endpoint-
    /// specific order quotas are projected separately when order entry moves
    /// to this facade.
    pub private_requests_per_two_seconds: u32,
}

impl Default for OkxPrincipalQuotaAllocation {
    fn default() -> Self {
        Self {
            private_requests_per_two_seconds: 10,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct OkxPrincipalOrderQuotaAllocation {
    /// Aggregate allocation for order submit/cancel requests in one OKX
    /// two-second window.
    pub requests_per_two_seconds: u32,
    /// Capacity unavailable to ordinary submits but available to cancels.
    pub cancel_reserve: u32,
}

impl Default for OkxPrincipalOrderQuotaAllocation {
    fn default() -> Self {
        Self {
            requests_per_two_seconds: 60,
            cancel_reserve: 10,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxConnectionConfig {
    pub environment: String,
    pub rest_base_url: String,
    pub shared_quota: Option<OkxSharedQuotaConfig>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxPrivateChannelConfig {
    pub websocket_url: String,
    pub event_queue_capacity: usize,
}

#[derive(Clone)]
pub struct OkxPrincipalConfig {
    pub binding_id: String,
    pub principal_id: Option<String>,
    pub api_key: SecretString,
    pub secret: SecretString,
    pub passphrase: SecretString,
    pub quota: Option<OkxPrincipalQuotaAllocation>,
    pub order_quota: Option<OkxPrincipalOrderQuotaAllocation>,
}
