use kairos_primitives::account::BrokerId;
use kairos_primitives::execution::{
    ExecutionAttemptId, ExecutionChannelCode, ExecutionRouteId, OrderEntrySymbol,
};
use kairos_primitives::integration::IntegrationSourceId;
use kairos_primitives::reference::MarketId;
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

/// Immutable route facts selected before an order-entry command can be sent.
///
/// The provider address is copied into the order so historical execution does
/// not depend on a mutable capability catalog. The route identity belongs to
/// Execution and never addresses a Reference entity.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SelectedExecutionRoute {
    pub route_id: ExecutionRouteId,
    pub broker_id: BrokerId,
    pub execution_channel: ExecutionChannelCode,
    pub order_entry_symbol: OrderEntrySymbol,
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
    Reconciled,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionCommandKind {
    #[default]
    Submit,
    Cancel,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAttempt {
    pub attempt_id: ExecutionAttemptId,
    #[serde(default)]
    pub command: ExecutionCommandKind,
    pub selected_route: SelectedExecutionRoute,
    /// Stable identity of the configured connection binding used by this
    /// attempt. It is copied rather than resolved from a mutable route view.
    pub provider_connection_id: IntegrationSourceId,
    pub command_started_at_unix_nanos: UnixNanos,
    pub delivery_certainty: DeliveryCertainty,
    #[serde(default)]
    pub remote_order_id: Option<kairos_primitives::integration::RemoteOrderId>,
}
