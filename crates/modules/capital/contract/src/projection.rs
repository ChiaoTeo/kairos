use kairos_primitives::{
    BasisPoints, CapitalDemandId, CapitalGroupId, CapitalOperationId, CapitalPlanId,
    CapitalReservationId, CapitalRouteId, FundingObjectiveId, Generation, IdempotencyKey, Quantity,
    Sequence, StrategyDecisionId, StrategyId, UnixNanos,
};

use crate::FundingLocation;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalReadiness {
    WaitingForFacts,
    Degraded,
    Ready,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalOperationStatus {
    Prepared,
    Dispatching,
    AwaitingParticipant,
    Indeterminate,
    AwaitingAccountObservation,
    Settled,
    Expired,
    Rejected,
    Failed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalOperationKind {
    Transfer,
    EarnRedemption,
    EarnSubscription,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FundingPriority {
    Low,
    Normal,
    High,
    Critical,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FundingObjectiveLifecycleStatus {
    Active,
    Cancelled,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FundingObjective {
    pub objective_id: FundingObjectiveId,
    pub version: Generation,
    pub strategy_id: StrategyId,
    pub destination: FundingLocation,
    pub desired_available: Quantity,
    pub required_by: UnixNanos,
    pub expires_at: UnixNanos,
    pub priority: FundingPriority,
    pub confidence_bps: BasisPoints,
    pub strategy_decision_id: StrategyDecisionId,
    pub status: FundingObjectiveLifecycleStatus,
    pub updated_at: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalDemandLifecycleStatus {
    Active,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalDemand {
    pub demand_id: CapitalDemandId,
    pub idempotency_key: IdempotencyKey,
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
    pub launch_id: String,
    pub instance_id: String,
    pub causal_references: Vec<String>,
    pub status: CapitalDemandLifecycleStatus,
    pub updated_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalPolicy {
    pub destination: FundingLocation,
    pub version: Generation,
    pub minimum: Quantity,
    pub default_target: Quantity,
    pub maximum: Quantity,
    pub stress_buffer: Quantity,
    pub minimum_movement: Quantity,
    pub hysteresis: Quantity,
    pub deficit_dwell_nanos: u64,
    pub cooldown_nanos: u64,
    pub max_fact_age_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalFacts {
    pub destination: FundingLocation,
    pub observed_available: Quantity,
    pub account_watermark: Sequence,
    pub account_observed_at: UnixNanos,
    pub account_complete: bool,
    pub risk_capacity: Quantity,
    pub risk_policy_version: Generation,
    pub risk_watermark: Sequence,
    pub earn_holdings: Vec<CapitalEarnHolding>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalEarnHolding {
    pub product_id: String,
    pub principal: Quantity,
    pub redeemable_amount: Quantity,
    pub immediately_redeemable: bool,
    pub active: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalRouteKind {
    InternalTransfer,
    AccountTransfer,
    EarnRedemptionThenTransfer,
    EarnSubscription,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalSettlementClass {
    ImmediateBookTransfer,
    ParticipantHistoryThenAccountObservation,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalRoute {
    pub route_id: CapitalRouteId,
    pub version: Generation,
    pub source: FundingLocation,
    pub destination: FundingLocation,
    pub kind: CapitalRouteKind,
    pub per_operation_limit: Quantity,
    pub daily_limit: Quantity,
    pub required_source_authority: String,
    pub settlement_class: CapitalSettlementClass,
    pub enabled: bool,
    pub earn_product_id: Option<String>,
    pub demand_guard_nanos: u64,
    pub allow_unknown_redemption_quota: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalReservationStatus {
    Active,
    Consumed,
    Released,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq)]
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

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalAvailability {
    pub destination: FundingLocation,
    pub readiness: CapitalReadiness,
    pub policy_version: Generation,
    pub active_objective_ids: Vec<FundingObjectiveId>,
    pub active_demand_ids: Vec<CapitalDemandId>,
    pub funding_horizons: Vec<CapitalFundingHorizon>,
    pub desired_target: Quantity,
    pub effective_target: Quantity,
    pub observed_available: Quantity,
    pub deficit: Quantity,
    pub deficit_observed_since: Option<UnixNanos>,
    pub cooldown_until: Option<UnixNanos>,
    pub account_watermark: Sequence,
    pub risk_policy_version: Generation,
    pub risk_watermark: Sequence,
    pub evaluated_at: UnixNanos,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalFundingHorizon {
    pub required_by: UnixNanos,
    pub objective_ids: Vec<FundingObjectiveId>,
    pub demand_ids: Vec<CapitalDemandId>,
    pub desired_available: Quantity,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalPlan {
    pub plan_id: CapitalPlanId,
    pub rebalance_decision_id: String,
    pub route_id: CapitalRouteId,
    pub route_version: Generation,
    pub route_kind: CapitalRouteKind,
    pub source: FundingLocation,
    pub destination: FundingLocation,
    pub amount: Quantity,
    pub objective_ids: Vec<FundingObjectiveId>,
    pub demand_ids: Vec<CapitalDemandId>,
    pub reservation_id: CapitalReservationId,
    pub idempotency_key: IdempotencyKey,
    pub selected_earn_product_id: Option<String>,
    pub source_account_watermark: Sequence,
    pub destination_account_watermark: Sequence,
    pub source_observed_available: Quantity,
    pub destination_observed_available: Quantity,
    pub redemption_account_watermark: Option<Sequence>,
    pub redemption_observed_available: Option<Quantity>,
    pub earn_principal_before: Quantity,
    pub status: CapitalPlanStatus,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalOperation {
    pub operation_id: CapitalOperationId,
    pub plan_id: CapitalPlanId,
    pub idempotency_key: IdempotencyKey,
    pub operation_index: u32,
    pub kind: CapitalOperationKind,
    pub status: CapitalOperationStatus,
    pub participant_operation_id: Option<String>,
    pub participant_state: Option<String>,
    pub dispatch_started_at: Option<UnixNanos>,
    pub attempt_count: u32,
    pub failure_reason: Option<String>,
    pub account_observation_watermark: Option<Sequence>,
    pub updated_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalCurrentView {
    pub capital_group_id: CapitalGroupId,
    pub strategy_id: StrategyId,
    pub environment: String,
    pub membership_version: Generation,
    pub event_sequence: Sequence,
    pub journal_sequence: Sequence,
    pub objectives: Vec<FundingObjective>,
    pub demands: Vec<CapitalDemand>,
    pub policies: Vec<CapitalPolicy>,
    pub facts: Vec<CapitalFacts>,
    pub availability: Vec<CapitalAvailability>,
    pub routes: Vec<CapitalRoute>,
    pub plans: Vec<CapitalPlan>,
    pub reservations: Vec<CapitalReservation>,
    pub operations: Vec<CapitalOperation>,
}
