use kairos_primitives::{
    ExecutionRouteId, MarketId, ProviderProductCode, ProviderSymbol, UnixNanos,
};
use serde::{Deserialize, Serialize};

/// Immutable route facts selected before an order-entry command can be sent.
///
/// The provider address is copied into the order so historical execution does
/// not depend on a mutable capability catalog. The route identity belongs to
/// Execution and never addresses a Reference entity.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SelectedExecutionRoute {
    pub route_id: ExecutionRouteId,
    pub participant_id: String,
    pub provider_product: ProviderProductCode,
    pub provider_symbol: ProviderSymbol,
    #[serde(default)]
    pub destination_market_id: Option<MarketId>,
    pub selected_at_unix_nanos: UnixNanos,
    pub selection_kind: RouteSelectionKind,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RouteSelectionKind {
    Explicit,
    UniqueCandidate,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryCertainty {
    NotSent,
    Indeterminate,
    Confirmed,
    Rejected,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAttempt {
    pub attempt_id: String,
    pub selected_route: SelectedExecutionRoute,
    /// Stable identity of the configured connection binding used by this
    /// attempt. It is copied rather than resolved from a mutable route view.
    pub provider_connection_id: String,
    pub command_started_at_unix_nanos: UnixNanos,
    pub delivery_certainty: DeliveryCertainty,
    #[serde(default)]
    pub remote_order_id: Option<kairos_primitives::RemoteOrderId>,
}
