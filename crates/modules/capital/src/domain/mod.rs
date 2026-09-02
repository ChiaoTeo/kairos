use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
pub use kairos_primitives::capital::{
    CapitalDemandId, CapitalGroupId, CapitalOperationId, CapitalPlanId, CapitalReservationId,
    CapitalRouteId, CapitalSourceAuthority, EarnProductId, FundingObjectiveId,
};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::reference::Currency;
use kairos_primitives::runtime::{InstanceId, LaunchId, StrategyDecisionId, StrategyId};
use kairos_primitives::time::{BasisPoints, Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

mod outcome;
pub use outcome::{
    CapitalDemandReceipt, CapitalEvent, CapitalSnapshot, CapitalYieldCandidate,
    FundingObjectiveReceipt, ManualCapitalTransferPreview,
};

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct FundingLocation {
    pub broker: BrokerId,
    pub account_id: AccountId,
    pub segment: SegmentKey,
    pub asset: Currency,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum FundingPriority {
    Low,
    #[default]
    Normal,
    High,
    Critical,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FundingObjective {
    pub objective_id: FundingObjectiveId,
    pub version: Generation,
    pub strategy_id: StrategyId,
    pub destination: FundingLocation,
    pub desired_available: Quantity,
    pub required_by: UnixNanos,
    pub expires_at: UnixNanos,
    pub priority: FundingPriority,
    /// Strategy confidence in basis points, inclusive 0..=10_000.
    pub confidence_bps: BasisPoints,
    pub strategy_decision_id: StrategyDecisionId,
}

impl FundingObjective {
    pub fn validate(&self) -> Result<(), String> {
        if self.version.get() == 0 {
            return Err("funding objective version must be positive".into());
        }
        if self.expires_at < self.required_by {
            return Err("funding objective cannot expire before required_by".into());
        }
        if self.confidence_bps.get() > 10_000 {
            return Err("funding objective confidence_bps cannot exceed 10000".into());
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum FundingObjectiveStatus {
    Active,
    Cancelled,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FundingObjectiveRecord {
    pub objective: FundingObjective,
    pub status: FundingObjectiveStatus,
    pub updated_at: UnixNanos,
}

/// Advisory evidence that an Execution admission could not be funded.
///
/// A demand is deliberately not a transfer command. Capital may aggregate,
/// net, defer, or expire it without creating a plan.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalDemand {
    pub demand_id: CapitalDemandId,
    pub idempotency_key: kairos_primitives::runtime::IdempotencyKey,
    pub strategy_id: StrategyId,
    pub destination: FundingLocation,
    pub observed_shortfall: Quantity,
    pub observed_at: UnixNanos,
    pub required_by: UnixNanos,
    pub expires_at: UnixNanos,
    pub priority: FundingPriority,
    pub confidence_bps: BasisPoints,
    pub account_watermark: Sequence,
    pub risk_watermark: Sequence,
    pub launch_id: LaunchId,
    pub instance_id: InstanceId,
    pub destination_lease_fence: String,
    pub causal_references: Vec<String>,
}

impl CapitalDemand {
    pub fn validate(&self) -> Result<(), String> {
        if !self.observed_shortfall.is_positive() {
            return Err("capital demand shortfall must be positive".into());
        }
        if self.observed_at > self.required_by || self.required_by > self.expires_at {
            return Err("capital demand requires observed_at <= required_by <= expires_at".into());
        }
        if self.confidence_bps.get() > 10_000 {
            return Err("capital demand confidence_bps cannot exceed 10000".into());
        }
        if self.account_watermark.get() == 0 || self.risk_watermark.get() == 0 {
            return Err("capital demand requires Account and Risk watermarks".into());
        }
        if self.destination_lease_fence.is_empty()
            || self.destination_lease_fence.trim() != self.destination_lease_fence
        {
            return Err("capital demand destination_lease_fence is required".into());
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalDemandStatus {
    Active,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalDemandRecord {
    pub demand: CapitalDemand,
    pub status: CapitalDemandStatus,
    pub updated_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalPolicy {
    pub destination: FundingLocation,
    pub version: Generation,
    pub minimum: Quantity,
    pub default_target: Quantity,
    pub maximum: Quantity,
    pub stress_buffer: Quantity,
    pub minimum_movement: Quantity,
    /// A deficit smaller than this dead-band is observation only.
    #[serde(default)]
    pub hysteresis: Quantity,
    /// A continuous deficit must survive this long before it becomes actionable.
    #[serde(default)]
    pub deficit_dwell_nanos: u64,
    /// Minimum interval between plans delivering to the same destination.
    #[serde(default)]
    pub cooldown_nanos: u64,
    pub max_fact_age_nanos: u64,
}

impl CapitalPolicy {
    pub fn validate(&self) -> Result<(), String> {
        if self.version.get() == 0 {
            return Err("capital policy version must be positive".into());
        }
        if self.minimum > self.default_target || self.default_target > self.maximum {
            return Err("capital policy requires minimum <= default_target <= maximum".into());
        }
        if self.max_fact_age_nanos == 0 {
            return Err("capital policy max_fact_age_nanos must be positive".into());
        }
        if self.hysteresis > self.maximum {
            return Err("capital policy hysteresis cannot exceed maximum".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalFacts {
    pub destination: FundingLocation,
    pub observed_available: Quantity,
    pub account_watermark: Sequence,
    pub account_observed_at: UnixNanos,
    pub account_complete: bool,
    pub risk_capacity: Quantity,
    pub risk_policy_version: Generation,
    pub risk_watermark: Sequence,
    #[serde(default)]
    pub earn_holdings: Vec<CapitalEarnHoldingFact>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalEarnHoldingFact {
    pub product_id: EarnProductId,
    #[serde(default)]
    pub principal: Quantity,
    pub redeemable_amount: Quantity,
    pub immediately_redeemable: bool,
    pub active: bool,
}

/// Ephemeral liveness evidence for one Account member view. It deliberately
/// does not survive restart: a recovered Capital Actor must observe the
/// current Account view again before opening its write barrier.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalMemberAccountObservation {
    pub broker: BrokerId,
    pub account_id: AccountId,
    pub account_watermark: Sequence,
    pub account_observed_at: UnixNanos,
    pub account_complete: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalReadiness {
    WaitingForFacts,
    WaitingForAccounts,
    Degraded,
    Ready,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalAvailabilityView {
    pub destination: FundingLocation,
    pub readiness: CapitalReadiness,
    pub policy_version: Generation,
    pub active_objective_ids: Vec<FundingObjectiveId>,
    pub active_demand_ids: Vec<CapitalDemandId>,
    /// Deadline buckets retain why a total-liquidity target exists. Within a
    /// bucket overlapping observations are netted by maximum, never summed.
    #[serde(default)]
    pub funding_horizons: Vec<CapitalFundingHorizon>,
    pub desired_target: Quantity,
    pub effective_target: Quantity,
    pub observed_available: Quantity,
    pub deficit: Quantity,
    #[serde(default)]
    pub deficit_observed_since: Option<UnixNanos>,
    #[serde(default)]
    pub cooldown_until: Option<UnixNanos>,
    pub account_watermark: Sequence,
    pub risk_policy_version: Generation,
    pub risk_watermark: Sequence,
    pub evaluated_at: UnixNanos,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalFundingHorizon {
    pub required_by: UnixNanos,
    pub objective_ids: Vec<FundingObjectiveId>,
    pub demand_ids: Vec<CapitalDemandId>,
    pub desired_available: Quantity,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalGroupMember {
    pub broker: BrokerId,
    pub account_id: AccountId,
    pub permitted_segments: Vec<SegmentKey>,
    #[serde(default)]
    pub readiness_role: CapitalMemberReadinessRole,
}

/// Whether loss of an Account member closes the whole Capital write barrier
/// or only degrades the group and freezes routes that touch that member.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalMemberReadinessRole {
    #[default]
    Critical,
    Optional,
}

impl CapitalGroupMember {
    pub fn validate(&self) -> Result<(), String> {
        if self.permitted_segments.is_empty() {
            return Err("capital group member requires at least one Segment".into());
        }
        let mut segments = self.permitted_segments.clone();
        segments.sort();
        segments.dedup();
        if segments.len() != self.permitted_segments.len() {
            return Err("capital group member Segments must be unique".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalGroupConfig {
    pub capital_group_id: CapitalGroupId,
    pub strategy_id: StrategyId,
    pub environment: String,
    pub membership_version: Generation,
    pub members: Vec<CapitalGroupMember>,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalRouteKind {
    #[default]
    InternalTransfer,
    AccountTransfer,
    EarnRedemptionThenTransfer,
    EarnSubscription,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalSettlementClass {
    ImmediateBookTransfer,
    ParticipantHistoryThenAccountObservation,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalTransferRoute {
    pub route_id: CapitalRouteId,
    pub version: Generation,
    pub source: FundingLocation,
    pub destination: FundingLocation,
    pub kind: CapitalRouteKind,
    pub per_operation_limit: Quantity,
    pub daily_limit: Quantity,
    pub required_source_authority: CapitalSourceAuthority,
    pub settlement_class: CapitalSettlementClass,
    pub enabled: bool,
    /// Required for an Earn subscription route and absent for transfer routes.
    #[serde(default)]
    pub earn_product_id: Option<EarnProductId>,
    /// Do not deploy cash when a known funding horizon falls inside this guard.
    #[serde(default)]
    pub demand_guard_nanos: u64,
    /// An unknown redemption quota is unsafe by default. An operator may
    /// explicitly accept that participant limitation for a configured route.
    #[serde(default)]
    pub allow_unknown_redemption_quota: bool,
}

impl CapitalTransferRoute {
    pub fn validate(&self) -> Result<(), String> {
        if self.version.get() == 0 {
            return Err("capital route version must be positive".into());
        }
        if self.kind == CapitalRouteKind::EarnSubscription {
            if self.source != self.destination {
                return Err(
                    "capital Earn subscription route must remain at one balance location".into(),
                );
            }
            if self
                .earn_product_id
                .as_deref()
                .is_none_or(|value| value.is_empty() || value.trim() != value)
            {
                return Err("capital Earn subscription route requires a product id".into());
            }
        } else if self.source == self.destination {
            return Err("capital transfer route source and destination must differ".into());
        } else if self.earn_product_id.is_some() {
            return Err("capital transfer route cannot select an Earn product".into());
        }
        if self.source.asset != self.destination.asset {
            return Err("capital transfer route cannot change assets".into());
        }
        if !self.per_operation_limit.is_positive() || !self.daily_limit.is_positive() {
            return Err("capital route limits must be positive".into());
        }
        if self.per_operation_limit > self.daily_limit {
            return Err("capital route per-operation limit cannot exceed daily limit".into());
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalPlanStatus {
    Authorized,
    Redeeming,
    AwaitingRedemption,
    Transferring,
    AwaitingTransfer,
    Subscribing,
    AwaitingSubscription,
    Reconciling,
    Available,
    Completed,
    Indeterminate,
    Rejected,
    Expired,
    Failed,
}

/// Durable decision about whether Capital may compensate after an abnormal
/// operation outcome. The first implementation never guesses that a reverse
/// movement is safe.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalRecoveryAction {
    #[default]
    None,
    NoCompensationRequired,
    ReconcileOriginalOperation,
    HoldAndReview,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalPlan {
    pub plan_id: CapitalPlanId,
    pub rebalance_decision_id: StrategyDecisionId,
    pub route_id: CapitalRouteId,
    pub route_version: Generation,
    #[serde(default)]
    pub route_kind: CapitalRouteKind,
    pub source: FundingLocation,
    pub destination: FundingLocation,
    pub amount: Quantity,
    pub objective_ids: Vec<FundingObjectiveId>,
    pub demand_ids: Vec<CapitalDemandId>,
    pub reservation_id: CapitalReservationId,
    pub idempotency_key: kairos_primitives::runtime::IdempotencyKey,
    #[serde(default)]
    pub selected_earn_product_id: Option<EarnProductId>,
    pub source_account_watermark: Sequence,
    pub destination_account_watermark: Sequence,
    pub source_observed_available: Quantity,
    pub destination_observed_available: Quantity,
    #[serde(default)]
    pub redemption_account_watermark: Option<Sequence>,
    #[serde(default)]
    pub redemption_observed_available: Option<Quantity>,
    /// Account-observed product principal before an Earn subscription.
    #[serde(default)]
    pub earn_principal_before: Quantity,
    pub status: CapitalPlanStatus,
    #[serde(default)]
    pub recovery_action: CapitalRecoveryAction,
    #[serde(default)]
    pub recovery_reason: Option<String>,
    #[serde(default)]
    pub recovery_decided_at: Option<UnixNanos>,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalReservationStatus {
    Active,
    Consumed,
    Released,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalReservation {
    pub reservation_id: CapitalReservationId,
    pub plan_id: CapitalPlanId,
    pub source: FundingLocation,
    pub amount: Quantity,
    pub source_account_watermark: Sequence,
    pub status: CapitalReservationStatus,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalOperationStatus {
    Prepared,
    /// Dispatch was durably fenced before the participant call. Recovery must
    /// query this operation and must not submit it again.
    Dispatching,
    AwaitingParticipant,
    Indeterminate,
    AwaitingAccountObservation,
    Settled,
    Expired,
    Rejected,
    Failed,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalOperationKind {
    EarnRedemption,
    EarnSubscription,
    #[default]
    Transfer,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalOperation {
    pub operation_id: CapitalOperationId,
    pub plan_id: CapitalPlanId,
    pub idempotency_key: kairos_primitives::runtime::IdempotencyKey,
    #[serde(default)]
    pub operation_index: u32,
    #[serde(default)]
    pub kind: CapitalOperationKind,
    pub status: CapitalOperationStatus,
    pub participant_operation_id: Option<String>,
    pub participant_state: Option<String>,
    pub dispatch_started_at: Option<UnixNanos>,
    pub attempt_count: u32,
    pub failure_reason: Option<String>,
    #[serde(default)]
    pub account_observation_watermark: Option<Sequence>,
    pub updated_at: UnixNanos,
}

impl CapitalPlan {
    pub(crate) fn validate_reservation(
        &self,
        reservation: &CapitalReservation,
    ) -> Result<(), String> {
        if reservation.reservation_id != self.reservation_id
            || reservation.plan_id != self.plan_id
            || reservation.source != self.source
            || reservation.amount != self.amount
        {
            return Err("Capital plan and reservation do not match".into());
        }
        if matches!(
            self.route_kind,
            CapitalRouteKind::EarnRedemptionThenTransfer | CapitalRouteKind::EarnSubscription
        ) && self.selected_earn_product_id.is_none()
        {
            return Err("Capital Earn plan has no selected product".into());
        }
        Ok(())
    }

    pub(crate) fn validate_operation(&self, operation: &CapitalOperation) -> Result<(), String> {
        if operation.plan_id != self.plan_id {
            return Err("Capital plan and operation do not match".into());
        }
        let expected_kind = match (self.route_kind, operation.operation_index) {
            (CapitalRouteKind::EarnRedemptionThenTransfer, 0) => {
                CapitalOperationKind::EarnRedemption
            },
            (CapitalRouteKind::EarnRedemptionThenTransfer, 1) => CapitalOperationKind::Transfer,
            (CapitalRouteKind::EarnSubscription, 0) => CapitalOperationKind::EarnSubscription,
            (CapitalRouteKind::InternalTransfer, 0) | (CapitalRouteKind::AccountTransfer, 0) => {
                CapitalOperationKind::Transfer
            },
            _ => return Err("Capital operation index is invalid for its route".into()),
        };
        if operation.kind != expected_kind {
            return Err("Capital operation kind is invalid for its route".into());
        }
        let kind_name = match expected_kind {
            CapitalOperationKind::EarnRedemption => "earn-redemption",
            CapitalOperationKind::EarnSubscription => "earn-subscription",
            CapitalOperationKind::Transfer => "transfer",
        };
        let expected_key = format!(
            "{}:{}:{kind_name}",
            self.plan_id.as_str(),
            operation.operation_index
        );
        if operation.idempotency_key.as_str() != expected_key {
            return Err("Capital operation idempotency key is not stable".into());
        }
        if operation.operation_index == 0 && operation.idempotency_key != self.idempotency_key {
            return Err("Capital first operation does not match the plan idempotency key".into());
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalSubmissionOutcome {
    Confirmed,
    Rejected,
    Indeterminate,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalParticipantOperationState {
    Pending,
    Succeeded,
    Failed,
    Cancelled,
    Unknown,
}

impl CapitalGroupConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.environment.is_empty() || self.environment.trim() != self.environment {
            return Err("capital group environment must be non-empty and trimmed".into());
        }
        if self.membership_version.get() == 0 {
            return Err("capital group membership version must be positive".into());
        }
        if self.members.is_empty() {
            return Err("capital group requires at least one Account".into());
        }
        for member in &self.members {
            member.validate()?;
        }
        let mut accounts = self
            .members
            .iter()
            .map(|member| (member.broker.clone(), member.account_id.clone()))
            .collect::<Vec<_>>();
        accounts.sort();
        accounts.dedup();
        if accounts.len() != self.members.len() {
            return Err("capital group Accounts must be unique".into());
        }
        Ok(())
    }

    pub fn contains(&self, location: &FundingLocation) -> bool {
        self.members.iter().any(|member| {
            member.broker == location.broker
                && member.account_id == location.account_id
                && member.permitted_segments.contains(&location.segment)
        })
    }
}
