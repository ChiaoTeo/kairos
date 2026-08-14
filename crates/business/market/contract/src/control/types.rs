use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize)]
pub struct MarketControlRequest {
    pub command_id: String,
    pub idempotency_key: String,
    pub caller_id: String,
    pub workspace_id: String,
    pub payload: serde_json::Value,
}

#[derive(Clone, Debug, Deserialize)]
pub struct MarketControlResponse {
    pub status: Option<String>,
    pub operation: Option<String>,
    pub resource_id: Option<String>,
    pub error: Option<MarketControlError>,
    #[serde(flatten)]
    pub details: std::collections::BTreeMap<String, serde_json::Value>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct MarketControlError {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub details: std::collections::BTreeMap<String, serde_json::Value>,
}
