use kairos_primitives::{
    AccountId, ExecutionRouteId, InstrumentId, MarketId, OrderId, OrderOptionCode, OrderType,
    ParticipantId, Price, ProviderProductCode, ProviderSymbol, Quantity, SegmentKey,
};
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionControlResponse {
    pub status: Option<String>,
    pub command_id: Option<String>,
    pub intent_id: Option<String>,
    pub order_id: Option<String>,
    pub accepted: Option<bool>,
    pub error: Option<String>,
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionControlError {
    pub code: String,
    pub message: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRouteCandidateResponse {
    pub route_id: ExecutionRouteId,
    pub account_id: Option<AccountId>,
    pub segment_key: Option<SegmentKey>,
    pub instrument_id: Option<InstrumentId>,
    pub market_id: Option<MarketId>,
    pub participant_id: ParticipantId,
    pub provider_product: ProviderProductCode,
    pub provider_symbol: ProviderSymbol,
    pub supported_order_types: Vec<OrderType>,
    pub supported_options: Vec<OrderOptionCode>,
    pub ready: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRoutesResponse {
    pub routes: Vec<ExecutionRouteCandidateResponse>,
}

/// JSON control-plane values belong to UDS; they are not event or view models.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CommandEnvelope {
    pub command_id: Option<String>,
    pub idempotency_key: Option<String>,
    pub caller_id: Option<String>,
    pub workspace_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SubmitIntentRequest {
    #[serde(flatten)]
    pub envelope: CommandEnvelope,
    pub intent: serde_json::Value,
    #[serde(default)]
    pub admission_evidence: Option<serde_json::Value>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CancelOrderRequest {
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReplaceOrderRequest {
    pub quantity: Option<Quantity>,
    pub limit_price: Option<Price>,
    pub options: Option<serde_json::Value>,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReconcileExecutionRequest {
    pub account_id: Option<AccountId>,
    pub execution_route_id: Option<ExecutionRouteId>,
    pub order_id: Option<OrderId>,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionRestRequest {
    Health,
    Routes(ExecutionRoutesQuery),
    SubmitIntent(SubmitIntentRequest),
    CancelOrder {
        order_id: OrderId,
        request: CancelOrderRequest,
    },
    ReplaceOrder {
        order_id: String,
        request: ReplaceOrderRequest,
    },
    Reconcile(ReconcileExecutionRequest),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionRestResponse {
    Health(Result<ExecutionHealthResponse, ExecutionControlError>),
    Routes(Result<ExecutionRoutesResponse, ExecutionControlError>),
    SubmitIntent(Result<ExecutionCommandStatus, ExecutionControlError>),
    CancelOrder(Result<ExecutionCommandStatus, ExecutionControlError>),
    ReplaceOrder(Result<ExecutionCommandStatus, ExecutionControlError>),
    Reconcile(Result<ExecutionReconcileResponse, ExecutionControlError>),
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRoutesQuery {
    pub account_id: Option<AccountId>,
    pub segment_key: Option<SegmentKey>,
    pub instrument_id: Option<InstrumentId>,
    pub market_id: Option<MarketId>,
    pub participant_id: Option<ParticipantId>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRouteHealth {
    pub route_id: ExecutionRouteId,
    pub status: String,
    pub required: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionHealthResponse {
    pub status: String,
    pub writer_recovery_ready: bool,
    pub outbox_backlog: u64,
    pub oldest_outbox_event_age_ms: Option<u64>,
    pub outbox_error: Option<String>,
    pub routes: Vec<ExecutionRouteHealth>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionCommandStatus {
    pub status: String,
    pub command_id: Option<String>,
    pub intent_id: Option<String>,
    pub order_id: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionReconcileResponse {
    pub changed: u64,
}
