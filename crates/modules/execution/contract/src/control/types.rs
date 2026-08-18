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
    pub route_id: String,
    pub account_id: Option<String>,
    pub segment_key: Option<String>,
    pub instrument_id: Option<String>,
    pub market_id: Option<String>,
    pub participant_id: String,
    pub provider_product: String,
    pub provider_symbol: String,
    pub supported_order_types: Vec<String>,
    pub supported_options: Vec<String>,
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
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CancelOrderRequest {
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReplaceOrderRequest {
    pub quantity: Option<String>,
    pub limit_price: Option<String>,
    pub options: Option<serde_json::Value>,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReconcileExecutionRequest {
    pub account_id: Option<String>,
    pub execution_route_id: Option<String>,
    pub order_id: Option<String>,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionRestRequest {
    Health,
    Routes(ExecutionRoutesQuery),
    SubmitIntent(SubmitIntentRequest),
    CancelOrder {
        order_id: String,
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
    pub account_id: Option<String>,
    pub segment_key: Option<String>,
    pub instrument_id: Option<String>,
    pub market_id: Option<String>,
    pub participant_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRouteHealth {
    pub route_id: String,
    pub status: String,
    pub required: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionHealthResponse {
    pub status: String,
    pub writer_recovery_ready: bool,
    pub outbox_backlog: usize,
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
    pub changed: usize,
}
