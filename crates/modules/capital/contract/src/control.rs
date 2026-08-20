use std::collections::BTreeMap;

use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::capital::{
    CapitalDemandId, CapitalGroupId, CapitalPlanId, FundingObjectiveId,
};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::reference::Currency;
use kairos_primitives::runtime::{
    IdempotencyKey, InstanceId, LaunchId, RequestId, StrategyDecisionId, StrategyId,
};
use kairos_primitives::time::{BasisPoints, Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
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
    pub launch_id: LaunchId,
    pub instance_id: InstanceId,
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct QueryCapitalAvailabilityRequest {
    pub request_id: RequestId,
    pub capital_group_id: CapitalGroupId,
    pub location: FundingLocation,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapitalReadinessStatus {
    WaitingForFacts,
    WaitingForAccounts,
    Degraded,
    Ready,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalAvailabilityResponse {
    pub request_id: RequestId,
    pub capital_group_id: CapitalGroupId,
    pub location: FundingLocation,
    pub readiness: CapitalReadinessStatus,
    pub policy_minimum: Quantity,
    pub policy_default_target: Quantity,
    pub policy_maximum: Quantity,
    pub policy_version: Generation,
    pub active_objective_ids: Vec<FundingObjectiveId>,
    pub active_demand_ids: Vec<CapitalDemandId>,
    pub desired_target: Quantity,
    pub observed_available: Quantity,
    pub effective_target: Quantity,
    pub deficit: Quantity,
    pub account_watermark: Sequence,
    pub risk_policy_version: Generation,
    pub risk_watermark: Sequence,
    pub evaluated_at_unix_nanos: UnixNanos,
    pub reason: Option<String>,
}

/// Operator request to query and reconcile the participant operation already
/// fenced for a Capital plan. This command never authorizes or submits work.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReconcileCapitalPlanRequest {
    pub request_id: RequestId,
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapitalPlanReconcileStatus {
    Reconciled,
    Unchanged,
    Rejected,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReconcileCapitalPlanResponse {
    pub request_id: RequestId,
    pub plan_id: CapitalPlanId,
    pub status: CapitalPlanReconcileStatus,
    pub error: Option<CapitalControlError>,
}
