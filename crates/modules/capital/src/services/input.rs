//! Private Actor inputs mapped from application use-case commands.

use kairos_primitives::capital::{
    CapitalGroupId, CapitalPlanId, CapitalRouteId, CapitalSourceAuthority, FundingObjectiveId,
};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::runtime::{IdempotencyKey, StrategyDecisionId};
use kairos_primitives::time::{Generation, UnixNanos};

use crate::domain::{
    CapitalDemand, CapitalFacts, CapitalMemberAccountObservation, CapitalParticipantOperationState,
    CapitalPolicy, CapitalSubmissionOutcome, CapitalTransferRoute, FundingLocation,
    FundingObjective, ManualCapitalTransferPreview,
};

pub(crate) struct PublishFundingObjective {
    pub capital_group_id: CapitalGroupId,
    pub objective: FundingObjective,
    pub observed_at: UnixNanos,
}
pub(crate) struct CancelFundingObjective {
    pub capital_group_id: CapitalGroupId,
    pub objective_id: FundingObjectiveId,
    pub expected_version: Generation,
    pub observed_at: UnixNanos,
}
pub(crate) struct ObserveCapitalDemand {
    pub capital_group_id: CapitalGroupId,
    pub demand: CapitalDemand,
}
pub(crate) struct UpdateCapitalPolicy {
    pub capital_group_id: CapitalGroupId,
    pub policy: CapitalPolicy,
    pub updated_at: UnixNanos,
}
pub(crate) struct ObserveCapitalFacts {
    pub capital_group_id: CapitalGroupId,
    pub facts: CapitalFacts,
}
pub(crate) struct ObserveCapitalMemberAccount {
    pub capital_group_id: CapitalGroupId,
    pub observation: CapitalMemberAccountObservation,
}
pub(crate) struct EvaluateCapitalGroup {
    pub evaluated_at: UnixNanos,
}
pub(crate) struct UpdateCapitalRoute {
    pub capital_group_id: CapitalGroupId,
    pub route: CapitalTransferRoute,
    pub updated_at: UnixNanos,
}
pub(crate) struct AuthorizeCapitalPlan {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub rebalance_decision_id: StrategyDecisionId,
    pub route_id: CapitalRouteId,
    pub source_authority: CapitalSourceAuthority,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}
pub(crate) struct PreviewManualCapitalTransfer {
    pub capital_group_id: CapitalGroupId,
    pub preview_id: String,
    pub plan_id: CapitalPlanId,
    pub idempotency_key: IdempotencyKey,
    pub source: FundingLocation,
    pub destination: FundingLocation,
    pub amount: Quantity,
    pub source_authority: CapitalSourceAuthority,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}
pub(crate) struct ConfirmManualCapitalTransfer {
    pub capital_group_id: CapitalGroupId,
    pub preview: ManualCapitalTransferPreview,
    pub confirmed_at: UnixNanos,
}
pub(crate) struct AuthorizeEarnSubscriptionPlan {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub rebalance_decision_id: StrategyDecisionId,
    pub route_id: CapitalRouteId,
    pub source_authority: CapitalSourceAuthority,
    pub previewed_amount: Quantity,
    pub preview_observed_at: UnixNanos,
    pub eligible: bool,
    pub immediately_redeemable: bool,
    pub redemption_quota_remaining: Option<Quantity>,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}
pub(crate) struct BeginCapitalOperation {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub at: UnixNanos,
}
pub(crate) struct MarkCapitalDeliveryStarted {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub at: UnixNanos,
}
pub(crate) struct RecordCapitalSubmission {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub outcome: CapitalSubmissionOutcome,
    pub participant_operation_id: Option<String>,
    pub failure_reason: Option<String>,
    pub at: UnixNanos,
}
pub(crate) struct RecordCapitalParticipantStatus {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub state: CapitalParticipantOperationState,
    pub participant_operation_id: Option<String>,
    pub participant_state: Option<String>,
    pub failure_reason: Option<String>,
    pub at: UnixNanos,
}
pub(crate) struct RecordCapitalRecoveryRequired {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub reason: String,
    pub at: UnixNanos,
}
pub(crate) struct ObserveCapitalSettlement {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub source: CapitalFacts,
    pub destination: CapitalFacts,
    pub observed_at: UnixNanos,
}
