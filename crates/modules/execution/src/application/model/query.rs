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

/// Filters the current Execution-owned route candidates.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRouteQuery {
    pub account_id: Option<AccountId>,
    pub segment_key: Option<SegmentKey>,
    pub instrument_id: Option<InstrumentId>,
    pub market_id: Option<MarketId>,
    pub order_type: Option<OrderType>,
    #[serde(default)]
    pub required_options: Vec<String>,
}

/// One currently configured route that may be selected for order submission.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRouteCandidate {
    pub route_id: ExecutionRouteId,
    /// Optional account constraint. `None` means the connection is shared.
    pub account_id: Option<AccountId>,
    /// Optional segment constraint. `None` means the connection is shared.
    pub segment_key: Option<SegmentKey>,
    /// Optional instrument constraint. `None` means the connection can route
    /// any instrument resolved by its provider address mapping.
    pub instrument_id: Option<InstrumentId>,
    pub market_id: Option<MarketId>,
    pub participant_id: String,
    pub provider_product: kairos_primitives::integration::ProviderProductCode,
    pub provider_symbol: kairos_primitives::integration::ProviderSymbol,
    /// Order forms that the configured adapter currently advertises.
    pub supported_order_types: Vec<OrderType>,
    /// Optional order-entry fields accepted by this concrete route.
    pub supported_options: Vec<String>,
    pub ready: bool,
    #[serde(default)]
    pub initial_margin_rate_bps: Option<u32>,
    #[serde(default)]
    pub margin_rule_id: Option<String>,
}

impl ExecutionRouteQuery {
    pub fn matches(&self, candidate: &ExecutionRouteCandidate) -> bool {
        self.account_id.as_ref().is_none_or(|value| {
            candidate
                .account_id
                .as_ref()
                .is_none_or(|item| item == value)
        }) && self.segment_key.as_ref().is_none_or(|value| {
            candidate
                .segment_key
                .as_ref()
                .is_none_or(|item| item == value)
        }) && self.instrument_id.as_ref().is_none_or(|value| {
            candidate
                .instrument_id
                .as_ref()
                .is_none_or(|item| item == value)
        }) && self
            .market_id
            .as_ref()
            .is_none_or(|value| candidate.market_id.as_ref() == Some(value))
            && self
                .order_type
                .is_none_or(|value| candidate.supported_order_types.contains(&value))
            && self.required_options.iter().all(|required| {
                candidate
                    .supported_options
                    .iter()
                    .any(|supported| supported == required)
            })
    }
}
