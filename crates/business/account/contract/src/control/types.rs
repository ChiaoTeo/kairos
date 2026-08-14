use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize)]
pub struct AccountControlRequest {
    pub command_id: String,
    pub idempotency_key: String,
    pub caller_id: String,
    pub workspace_id: String,
    pub payload: serde_json::Value,
}

#[derive(Clone, Debug, Deserialize)]
pub struct AccountControlResponse {
    pub status: Option<String>,
    pub operation: Option<String>,
    pub resource_id: Option<String>,
    pub error: Option<AccountControlError>,
    #[serde(flatten)]
    pub details: std::collections::BTreeMap<String, serde_json::Value>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct AccountControlError {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub details: std::collections::BTreeMap<String, serde_json::Value>,
}
