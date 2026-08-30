use kairos_primitives::capital::{
    CapitalGroupId, CapitalPlanId, CapitalRouteId, CapitalSourceAuthority, EarnProductId,
};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::runtime::{IdempotencyKey, StrategyId};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use super::{
    CapitalAvailabilityView, CapitalDemandRecord, CapitalFacts, CapitalGroupMember,
    CapitalOperation, CapitalPlan, CapitalPolicy, CapitalReservation, CapitalRouteKind,
    CapitalTransferRoute, FundingLocation, FundingObjectiveRecord,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CapitalDemandReceipt {
    Accepted(CapitalDemandRecord),
    Duplicate(CapitalDemandRecord),
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize, Serialize)]
pub struct ManualCapitalTransferPreview {
    pub preview_id: String,
    pub plan_id: CapitalPlanId,
    pub idempotency_key: IdempotencyKey,
    pub route_id: CapitalRouteId,
    pub route_version: Generation,
    pub route_kind: CapitalRouteKind,
    pub source: FundingLocation,
    pub destination: FundingLocation,
    pub amount: Quantity,
    pub source_authority: CapitalSourceAuthority,
    pub source_account_watermark: Sequence,
    pub destination_account_watermark: Sequence,
    pub source_observed_available: Quantity,
    pub destination_observed_available: Quantity,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalYieldCandidate {
    pub route_id: CapitalRouteId,
    pub product_id: EarnProductId,
    pub amount: Quantity,
    pub account_watermark: Sequence,
    pub risk_watermark: Sequence,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum FundingObjectiveReceipt {
    Accepted(FundingObjectiveRecord),
    Duplicate(FundingObjectiveRecord),
    Cancelled(FundingObjectiveRecord),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CapitalEvent {
    FundingObjectiveChanged {
        record: FundingObjectiveRecord,
        event_sequence: Sequence,
    },
    CapitalDemandChanged {
        record: CapitalDemandRecord,
        event_sequence: Sequence,
    },
    PolicyChanged {
        policy: CapitalPolicy,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
    FactsObserved {
        facts: CapitalFacts,
        event_sequence: Sequence,
    },
    AvailabilityEvaluated {
        availability: Vec<CapitalAvailabilityView>,
        event_sequence: Sequence,
    },
    RouteChanged {
        route: CapitalTransferRoute,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
    PlanAuthorized {
        plan: Box<CapitalPlan>,
        reservation: Box<CapitalReservation>,
        event_sequence: Sequence,
    },
    PlanStateChanged {
        plan: Box<CapitalPlan>,
        reservation: Box<CapitalReservation>,
        operation: Box<CapitalOperation>,
        event_sequence: Sequence,
    },
    PlanExpired {
        plan: Box<CapitalPlan>,
        reservation: Box<CapitalReservation>,
        operation: Option<Box<CapitalOperation>>,
        event_sequence: Sequence,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapitalSnapshot {
    pub capital_group_id: CapitalGroupId,
    pub strategy_id: StrategyId,
    pub environment: String,
    pub membership_version: Generation,
    pub members: Vec<CapitalGroupMember>,
    pub event_sequence: Sequence,
    pub journal_sequence: Sequence,
    pub objectives: Vec<FundingObjectiveRecord>,
    pub demands: Vec<CapitalDemandRecord>,
    pub policies: Vec<CapitalPolicy>,
    pub facts: Vec<CapitalFacts>,
    pub availability: Vec<CapitalAvailabilityView>,
    pub routes: Vec<CapitalTransferRoute>,
    pub plans: Vec<CapitalPlan>,
    pub reservations: Vec<CapitalReservation>,
    pub operations: Vec<CapitalOperation>,
    pub pending_events: Vec<CapitalEvent>,
}
