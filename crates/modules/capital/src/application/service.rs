use kairos_primitives::{Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use crate::domain::{
    CapitalAvailabilityView, CapitalDemand, CapitalDemandId, CapitalDemandRecord, CapitalFacts,
    CapitalGroupId, CapitalOperation, CapitalParticipantOperationState, CapitalPlan, CapitalPlanId,
    CapitalPolicy, CapitalReservation, CapitalRouteId, CapitalSubmissionOutcome,
    CapitalTransferRoute, FundingLocation, FundingObjective, FundingObjectiveId,
    FundingObjectiveRecord,
};
use crate::services::actor::{ActorError, CapitalActor};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PublishFundingObjective {
    pub capital_group_id: CapitalGroupId,
    pub objective: FundingObjective,
    pub observed_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CancelFundingObjective {
    pub capital_group_id: CapitalGroupId,
    pub objective_id: FundingObjectiveId,
    pub expected_version: kairos_primitives::Generation,
    pub observed_at: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ExpireFundingObjectives {
    pub observed_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObserveCapitalDemand {
    pub capital_group_id: CapitalGroupId,
    pub demand: CapitalDemand,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ExpireCapitalDemands {
    pub observed_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CapitalDemandReceipt {
    Accepted(CapitalDemandRecord),
    Duplicate(CapitalDemandRecord),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct UpdateCapitalPolicy {
    pub capital_group_id: CapitalGroupId,
    pub policy: CapitalPolicy,
    pub updated_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObserveCapitalFacts {
    pub capital_group_id: CapitalGroupId,
    pub facts: CapitalFacts,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EvaluateCapitalGroup {
    pub evaluated_at: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ExpireCapitalPlans {
    pub observed_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct UpdateCapitalRoute {
    pub capital_group_id: CapitalGroupId,
    pub route: CapitalTransferRoute,
    pub updated_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AuthorizeCapitalPlan {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub rebalance_decision_id: String,
    pub route_id: CapitalRouteId,
    pub source_authority: String,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}

/// Deterministic amount that may leave a liquid balance location without
/// crossing its effective liquidity target or an active Capital reservation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalYieldCandidate {
    pub route_id: CapitalRouteId,
    pub product_id: String,
    pub amount: kairos_primitives::Quantity,
    pub account_watermark: Sequence,
    pub risk_watermark: Sequence,
}

/// Participant product evidence used when authorizing an Earn subscription.
/// The Actor rechecks its own balance, policy, horizon, route, and reservation
/// state; this evidence never authorizes a movement by itself.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AuthorizeEarnSubscriptionPlan {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub rebalance_decision_id: String,
    pub route_id: CapitalRouteId,
    pub source_authority: String,
    pub previewed_amount: kairos_primitives::Quantity,
    pub preview_observed_at: UnixNanos,
    pub eligible: bool,
    pub immediately_redeemable: bool,
    pub redemption_quota_remaining: Option<kairos_primitives::Quantity>,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BeginCapitalOperation {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarkCapitalDeliveryStarted {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RecordCapitalSubmission {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub outcome: CapitalSubmissionOutcome,
    pub participant_operation_id: Option<String>,
    pub failure_reason: Option<String>,
    pub at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RecordCapitalParticipantStatus {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub state: CapitalParticipantOperationState,
    pub participant_operation_id: Option<String>,
    pub participant_state: Option<String>,
    pub failure_reason: Option<String>,
    pub at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObserveCapitalSettlement {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub source: CapitalFacts,
    pub destination: CapitalFacts,
    pub observed_at: UnixNanos,
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
    pub strategy_id: kairos_primitives::StrategyId,
    pub environment: String,
    pub membership_version: kairos_primitives::Generation,
    pub members: Vec<crate::domain::CapitalGroupMember>,
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

#[derive(Debug, thiserror::Error, Eq, PartialEq)]
pub enum CapitalError {
    #[error("invalid capital request: {0}")]
    Invalid(String),
    #[error("capital request rejected: {0}")]
    Rejected(String),
    #[error("capital state failed: {0}")]
    State(String),
    #[error("capital persistence failed: {0}")]
    Persistence(String),
}

pub struct CapitalApplication {
    actor: CapitalActor,
}

impl CapitalApplication {
    pub(crate) fn new(actor: CapitalActor) -> Self {
        Self { actor }
    }

    pub fn publish_funding_objective(
        &mut self,
        command: PublishFundingObjective,
    ) -> Result<FundingObjectiveReceipt, CapitalError> {
        self.actor.publish(command).map_err(map_actor_error)
    }

    pub fn cancel_funding_objective(
        &mut self,
        command: CancelFundingObjective,
    ) -> Result<FundingObjectiveReceipt, CapitalError> {
        self.actor.cancel(command).map_err(map_actor_error)
    }

    pub fn expire_funding_objectives(
        &mut self,
        command: ExpireFundingObjectives,
    ) -> Result<usize, CapitalError> {
        self.actor
            .expire(command.observed_at)
            .map_err(map_actor_error)
    }

    pub fn observe_demand(
        &mut self,
        command: ObserveCapitalDemand,
    ) -> Result<CapitalDemandReceipt, CapitalError> {
        self.actor.observe_demand(command).map_err(map_actor_error)
    }

    pub fn expire_demands(&mut self, command: ExpireCapitalDemands) -> Result<usize, CapitalError> {
        self.actor
            .expire_demands(command.observed_at)
            .map_err(map_actor_error)
    }

    pub fn demand(&self, demand_id: &CapitalDemandId) -> Option<&CapitalDemandRecord> {
        self.actor.demand(demand_id)
    }

    pub fn update_policy(&mut self, command: UpdateCapitalPolicy) -> Result<(), CapitalError> {
        self.actor.update_policy(command).map_err(map_actor_error)
    }

    pub fn observe_facts(&mut self, command: ObserveCapitalFacts) -> Result<(), CapitalError> {
        self.actor.observe_facts(command).map_err(map_actor_error)
    }

    pub fn evaluate(
        &mut self,
        command: EvaluateCapitalGroup,
    ) -> Result<Vec<CapitalAvailabilityView>, CapitalError> {
        self.actor.evaluate(command).map_err(map_actor_error)
    }

    pub fn availability(&self, location: &FundingLocation) -> Option<&CapitalAvailabilityView> {
        self.actor.availability(location)
    }

    pub fn update_route(&mut self, command: UpdateCapitalRoute) -> Result<(), CapitalError> {
        self.actor.update_route(command).map_err(map_actor_error)
    }

    pub fn authorize_plan(
        &mut self,
        command: AuthorizeCapitalPlan,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor.authorize_plan(command).map_err(map_actor_error)
    }

    pub fn yield_candidate(
        &self,
        route_id: &CapitalRouteId,
        evaluated_at: UnixNanos,
    ) -> Result<Option<CapitalYieldCandidate>, CapitalError> {
        self.actor
            .yield_candidate(route_id, evaluated_at)
            .map_err(map_actor_error)
    }

    pub fn authorize_earn_subscription(
        &mut self,
        command: AuthorizeEarnSubscriptionPlan,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor
            .authorize_earn_subscription(command)
            .map_err(map_actor_error)
    }

    pub fn expire_plans(&mut self, command: ExpireCapitalPlans) -> Result<usize, CapitalError> {
        self.actor
            .expire_plans(command.observed_at)
            .map_err(map_actor_error)
    }

    pub fn begin_operation(
        &mut self,
        command: BeginCapitalOperation,
    ) -> Result<CapitalOperation, CapitalError> {
        self.actor.begin_operation(command).map_err(map_actor_error)
    }

    pub fn mark_delivery_started(
        &mut self,
        command: MarkCapitalDeliveryStarted,
    ) -> Result<CapitalOperation, CapitalError> {
        self.actor
            .mark_delivery_started(command)
            .map_err(map_actor_error)
    }

    pub fn record_submission(
        &mut self,
        command: RecordCapitalSubmission,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor
            .record_submission(command)
            .map_err(map_actor_error)
    }

    pub fn record_participant_status(
        &mut self,
        command: RecordCapitalParticipantStatus,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor
            .record_participant_status(command)
            .map_err(map_actor_error)
    }

    pub fn observe_settlement(
        &mut self,
        command: ObserveCapitalSettlement,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor
            .observe_settlement(command)
            .map_err(map_actor_error)
    }

    pub fn snapshot(&self) -> CapitalSnapshot {
        self.actor.snapshot()
    }

    pub fn plan(&self, plan_id: &CapitalPlanId) -> Option<&CapitalPlan> {
        self.actor.plan_view(plan_id)
    }

    pub fn operation_for_plan(&self, plan_id: &CapitalPlanId) -> Option<&CapitalOperation> {
        self.actor.operation_view_for_plan(plan_id)
    }

    pub fn pending_event(&self) -> Option<&CapitalEvent> {
        self.actor.pending_event()
    }

    pub fn acknowledge_event(&mut self) -> Result<(), CapitalError> {
        self.actor.acknowledge_event().map_err(map_actor_error)
    }
}

fn map_actor_error(error: ActorError) -> CapitalError {
    match error {
        ActorError::Invalid(message) => CapitalError::Invalid(message),
        ActorError::Rejected(message) => CapitalError::Rejected(message),
        ActorError::State(message) => CapitalError::State(message),
        ActorError::Persistence(message) => CapitalError::Persistence(message),
    }
}
