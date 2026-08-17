//! Public Execution queries.

use super::*;

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct RemoteOrderQuery {
    /// Integration binding selected for a provider query. When omitted, a
    /// business-owned route set may query every configured binding.
    pub binding_id: Option<String>,
    pub symbol: Option<Symbol>,
    pub order_id: Option<OrderId>,
    pub limit: Option<u32>,
    pub since_unix_nanos: Option<UnixNanos>,
}
