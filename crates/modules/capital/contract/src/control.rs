use std::collections::BTreeMap;

use kairos_primitives::{
    AccountId, BasisPoints, BrokerId, CapitalDemandId, CapitalGroupId, Currency,
    FundingObjectiveId, Generation, IdempotencyKey, Quantity, RequestId, SegmentKey, Sequence,
    StrategyDecisionId, StrategyId, UnixNanos,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FundingLocation {
    pub broker: BrokerId,
    pub account_id: AccountId,
    pub segment: SegmentKey,
    pub asset: Currency,
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
    pub request_id: RequestId,
    pub capital_group_id: CapitalGroupId,
    pub objective_id: FundingObjectiveId,
    pub version: Generation,
    pub strategy_id: StrategyId,
    pub destination: FundingLocation,
    pub desired_available: Quantity,
    pub required_by_unix_nanos: UnixNanos,
    pub expires_at_unix_nanos: UnixNanos,
    pub priority: FundingObjectivePriority,
    pub confidence_bps: BasisPoints,
    pub strategy_decision_id: StrategyDecisionId,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CancelFundingObjectiveRequest {
    pub request_id: RequestId,
    pub capital_group_id: CapitalGroupId,
    pub objective_id: FundingObjectiveId,
    pub expected_version: Generation,
    pub strategy_id: StrategyId,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ObserveCapitalDemandRequest {
    pub request_id: RequestId,
    pub demand_id: CapitalDemandId,
    pub idempotency_key: IdempotencyKey,
    pub capital_group_id: CapitalGroupId,
    pub strategy_id: StrategyId,
    pub destination: FundingLocation,
    pub observed_shortfall: Quantity,
    pub observed_at_unix_nanos: UnixNanos,
    pub required_by_unix_nanos: UnixNanos,
    pub expires_at_unix_nanos: UnixNanos,
    pub priority: FundingObjectivePriority,
    pub confidence_bps: BasisPoints,
    pub account_watermark: Sequence,
    pub risk_watermark: Sequence,
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
    pub request_id: RequestId,
    pub demand_id: CapitalDemandId,
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
    pub request_id: RequestId,
    pub objective_id: FundingObjectiveId,
    pub version: Generation,
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
