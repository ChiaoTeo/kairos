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

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct AccountControlError {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub details: std::collections::BTreeMap<String, serde_json::Value>,
}

/// Closed REST request set provided by one Account process.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AccountRestRequest {
    Health,
    ApplySimulatedSettlement(super::SimulatedSettlement),
    MarkToMarket(super::MarkToMarketRequest),
    AdvanceTime(super::AdvanceAccountTimeRequest),
    Refresh(AccountSegmentsRequest),
    Reconcile(AccountSegmentsRequest),
}

/// Closed REST response set. Each variant corresponds to exactly one request
/// variant, so the Conflux host never transports an untyped JSON result.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AccountRestResponse {
    Health(Result<super::Health, AccountControlError>),
    ApplySimulatedSettlement(Result<AccountCommandStatus, AccountControlError>),
    MarkToMarket(Result<AccountCommandStatus, AccountControlError>),
    AdvanceTime(Result<AdvanceAccountTimeResponse, AccountControlError>),
    Refresh(Result<AccountRefreshResponse, AccountControlError>),
    Reconcile(Result<AccountRefreshResponse, AccountControlError>),
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountSegmentsRequest {
    #[serde(default)]
    pub segments: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountCommandStatus {
    pub status: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AdvanceAccountTimeResponse {
    pub event_time_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountRefreshResponse {
    pub status: String,
    pub account_id: String,
    pub segments: Vec<String>,
}
