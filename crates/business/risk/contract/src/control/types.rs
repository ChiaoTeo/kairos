use serde::Deserialize;
#[derive(Clone, Debug, Deserialize)]
pub struct RiskControlResponse { pub status: Option<String>, pub command_id: Option<String>, pub decision_id: Option<String>, pub request_id: Option<String>, pub outcome: Option<String>, pub error: Option<RiskControlError>, #[serde(flatten)] pub details: std::collections::BTreeMap<String, serde_json::Value> }
#[derive(Clone, Debug, Deserialize)]
pub struct RiskControlError { pub code: String, pub message: String, #[serde(default)] pub retryable: bool, #[serde(default)] pub details: std::collections::BTreeMap<String, serde_json::Value> }
