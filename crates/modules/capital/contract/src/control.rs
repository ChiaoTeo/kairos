use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FundingLocation {
    pub broker: String,
    pub account_id: String,
    pub segment: String,
    pub asset: String,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FundingObjectivePriority {
    Low,
    #[default]
    Normal,
    High,
    Critical,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PublishFundingObjectiveRequest {
    pub request_id: String,
    pub capital_group_id: String,
    pub objective_id: String,
    pub version: u64,
    pub strategy_id: String,
    pub destination: FundingLocation,
    /// Exact non-negative decimal string; decoded into a domain Quantity.
    pub desired_available: String,
    pub required_by_unix_nanos: u64,
    pub expires_at_unix_nanos: u64,
    pub priority: FundingObjectivePriority,
    pub confidence_bps: u16,
    pub strategy_decision_id: String,
    pub observed_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CancelFundingObjectiveRequest {
    pub request_id: String,
    pub capital_group_id: String,
    pub objective_id: String,
    pub expected_version: u64,
    pub strategy_id: String,
    pub observed_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ObserveCapitalDemandRequest {
    pub request_id: String,
    pub demand_id: String,
    pub idempotency_key: String,
    pub capital_group_id: String,
    pub strategy_id: String,
    pub destination: FundingLocation,
    pub observed_shortfall: String,
    pub observed_at_unix_nanos: u64,
    pub required_by_unix_nanos: u64,
    pub expires_at_unix_nanos: u64,
    pub priority: FundingObjectivePriority,
    pub confidence_bps: u16,
    pub account_watermark: u64,
    pub risk_watermark: u64,
    pub launch_id: String,
    pub instance_id: String,
    pub destination_lease_fence: String,
    #[serde(default)]
    pub causal_references: Vec<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapitalDemandStatus {
    Accepted,
    Duplicate,
    Rejected,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalDemandResponse {
    pub request_id: String,
    pub demand_id: String,
    pub status: CapitalDemandStatus,
    pub error: Option<CapitalControlError>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FundingObjectiveStatus {
    Accepted,
    Duplicate,
    Cancelled,
    Rejected,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalControlResponse {
    pub request_id: String,
    pub objective_id: String,
    pub version: u64,
    pub status: FundingObjectiveStatus,
    pub error: Option<CapitalControlError>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalControlError {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub details: BTreeMap<String, serde_json::Value>,
}
