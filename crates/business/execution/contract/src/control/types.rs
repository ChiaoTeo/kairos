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
    pub execution_access_id: Option<String>,
    pub order_id: Option<String>,
    pub reason: Option<String>,
}
