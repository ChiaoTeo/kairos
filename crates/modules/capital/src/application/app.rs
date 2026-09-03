use kairos_primitives::capital::CapitalSourceAuthority;
use kairos_primitives::decimal::Quantity;
use kairos_primitives::runtime::{IdempotencyKey, StrategyDecisionId};
use kairos_primitives::time::UnixNanos;

use crate::domain::{
    CapitalAvailabilityView, CapitalDemand, CapitalDemandId, CapitalDemandRecord,
    CapitalDomainError, CapitalFacts, CapitalGroupId, CapitalMemberAccountObservation,
    CapitalOperation, CapitalParticipantOperationState, CapitalPlan, CapitalPlanId, CapitalPolicy,
    CapitalRouteId, CapitalSubmissionOutcome, CapitalTransferRoute, FundingLocation,
    FundingObjective, FundingObjectiveId,
};
pub use crate::domain::{
    CapitalDemandReceipt, CapitalEvent, CapitalSnapshot, CapitalYieldCandidate,
    FundingObjectiveReceipt, ManualCapitalTransferPreview,
};
use crate::services::actor::{ActorError, CapitalActor};
use crate::services::input as actor_input;

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
    pub expected_version: kairos_primitives::time::Generation,
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

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObserveCapitalMemberAccount {
    pub capital_group_id: CapitalGroupId,
    pub observation: CapitalMemberAccountObservation,
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
    pub rebalance_decision_id: StrategyDecisionId,
    pub route_id: CapitalRouteId,
    pub source_authority: CapitalSourceAuthority,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PreviewManualCapitalTransfer {
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

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConfirmManualCapitalTransfer {
    pub capital_group_id: CapitalGroupId,
    pub preview: ManualCapitalTransferPreview,
    pub confirmed_at: UnixNanos,
}

/// Participant product evidence used when authorizing an Earn subscription.
/// The Actor rechecks its own balance, policy, horizon, route, and reservation
/// state; this evidence never authorizes a movement by itself.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AuthorizeEarnSubscriptionPlan {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub rebalance_decision_id: StrategyDecisionId,
    pub route_id: CapitalRouteId,
    pub source_authority: CapitalSourceAuthority,
    pub previewed_amount: kairos_primitives::decimal::Quantity,
    pub preview_observed_at: UnixNanos,
    pub eligible: bool,
    pub immediately_redeemable: bool,
    pub redemption_quota_remaining: Option<kairos_primitives::decimal::Quantity>,
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
pub struct RecordCapitalRecoveryRequired {
    pub capital_group_id: CapitalGroupId,
    pub plan_id: CapitalPlanId,
    pub reason: String,
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

#[derive(Debug, thiserror::Error, Eq, PartialEq)]
pub enum CapitalError {
    #[error("invalid capital request: {0}")]
    InvalidDomain(CapitalDomainError),
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
        self.actor
            .publish(actor_input::PublishFundingObjective {
                capital_group_id: command.capital_group_id,
                objective: command.objective,
                observed_at: command.observed_at,
            })
            .map_err(map_actor_error)
    }

    pub fn cancel_funding_objective(
        &mut self,
        command: CancelFundingObjective,
    ) -> Result<FundingObjectiveReceipt, CapitalError> {
        self.actor
            .cancel(actor_input::CancelFundingObjective {
                capital_group_id: command.capital_group_id,
                objective_id: command.objective_id,
                expected_version: command.expected_version,
                observed_at: command.observed_at,
            })
            .map_err(map_actor_error)
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
        self.actor
            .observe_demand(actor_input::ObserveCapitalDemand {
                capital_group_id: command.capital_group_id,
                demand: command.demand,
            })
            .map_err(map_actor_error)
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
        self.actor
            .update_policy(actor_input::UpdateCapitalPolicy {
                capital_group_id: command.capital_group_id,
                policy: command.policy,
                updated_at: command.updated_at,
            })
            .map_err(map_actor_error)
    }

    pub fn observe_facts(&mut self, command: ObserveCapitalFacts) -> Result<(), CapitalError> {
        self.actor
            .observe_facts(actor_input::ObserveCapitalFacts {
                capital_group_id: command.capital_group_id,
                facts: command.facts,
            })
            .map_err(map_actor_error)
    }

    pub fn observe_member_account(
        &mut self,
        command: ObserveCapitalMemberAccount,
    ) -> Result<(), CapitalError> {
        self.actor
            .observe_member_account(actor_input::ObserveCapitalMemberAccount {
                capital_group_id: command.capital_group_id,
                observation: command.observation,
            })
            .map_err(map_actor_error)
    }

    pub fn evaluate(
        &mut self,
        command: EvaluateCapitalGroup,
    ) -> Result<Vec<CapitalAvailabilityView>, CapitalError> {
        self.actor
            .evaluate(actor_input::EvaluateCapitalGroup {
                evaluated_at: command.evaluated_at,
            })
            .map_err(map_actor_error)
    }

    pub fn availability(&self, location: &FundingLocation) -> Option<&CapitalAvailabilityView> {
        self.actor.availability(location)
    }

    pub fn update_route(&mut self, command: UpdateCapitalRoute) -> Result<(), CapitalError> {
        self.actor
            .update_route(actor_input::UpdateCapitalRoute {
                capital_group_id: command.capital_group_id,
                route: command.route,
                updated_at: command.updated_at,
            })
            .map_err(map_actor_error)
    }

    pub fn authorize_plan(
        &mut self,
        command: AuthorizeCapitalPlan,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor
            .authorize_plan(actor_input::AuthorizeCapitalPlan {
                capital_group_id: command.capital_group_id,
                plan_id: command.plan_id,
                rebalance_decision_id: command.rebalance_decision_id,
                route_id: command.route_id,
                source_authority: command.source_authority,
                created_at: command.created_at,
                expires_at: command.expires_at,
            })
            .map_err(map_actor_error)
    }

    pub fn preview_manual_transfer(
        &self,
        command: PreviewManualCapitalTransfer,
    ) -> Result<ManualCapitalTransferPreview, CapitalError> {
        self.actor
            .preview_manual_transfer(actor_input::PreviewManualCapitalTransfer {
                capital_group_id: command.capital_group_id,
                preview_id: command.preview_id,
                plan_id: command.plan_id,
                idempotency_key: command.idempotency_key,
                source: command.source,
                destination: command.destination,
                amount: command.amount,
                source_authority: command.source_authority,
                created_at: command.created_at,
                expires_at: command.expires_at,
            })
            .map_err(map_actor_error)
    }

    pub fn confirm_manual_transfer(
        &mut self,
        command: ConfirmManualCapitalTransfer,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor
            .confirm_manual_transfer(actor_input::ConfirmManualCapitalTransfer {
                capital_group_id: command.capital_group_id,
                preview: command.preview,
                confirmed_at: command.confirmed_at,
            })
            .map_err(map_actor_error)
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
            .authorize_earn_subscription(actor_input::AuthorizeEarnSubscriptionPlan {
                capital_group_id: command.capital_group_id,
                plan_id: command.plan_id,
                rebalance_decision_id: command.rebalance_decision_id,
                route_id: command.route_id,
                source_authority: command.source_authority,
                previewed_amount: command.previewed_amount,
                preview_observed_at: command.preview_observed_at,
                eligible: command.eligible,
                immediately_redeemable: command.immediately_redeemable,
                redemption_quota_remaining: command.redemption_quota_remaining,
                created_at: command.created_at,
                expires_at: command.expires_at,
            })
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
        self.actor
            .begin_operation(actor_input::BeginCapitalOperation {
                capital_group_id: command.capital_group_id,
                plan_id: command.plan_id,
                at: command.at,
            })
            .map_err(map_actor_error)
    }

    pub fn mark_delivery_started(
        &mut self,
        command: MarkCapitalDeliveryStarted,
    ) -> Result<CapitalOperation, CapitalError> {
        self.actor
            .mark_delivery_started(actor_input::MarkCapitalDeliveryStarted {
                capital_group_id: command.capital_group_id,
                plan_id: command.plan_id,
                at: command.at,
            })
            .map_err(map_actor_error)
    }

    pub fn record_submission(
        &mut self,
        command: RecordCapitalSubmission,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor
            .record_submission(actor_input::RecordCapitalSubmission {
                capital_group_id: command.capital_group_id,
                plan_id: command.plan_id,
                outcome: command.outcome,
                participant_operation_id: command.participant_operation_id,
                failure_reason: command.failure_reason,
                at: command.at,
            })
            .map_err(map_actor_error)
    }

    pub fn record_participant_status(
        &mut self,
        command: RecordCapitalParticipantStatus,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor
            .record_participant_status(actor_input::RecordCapitalParticipantStatus {
                capital_group_id: command.capital_group_id,
                plan_id: command.plan_id,
                state: command.state,
                participant_operation_id: command.participant_operation_id,
                participant_state: command.participant_state,
                failure_reason: command.failure_reason,
                at: command.at,
            })
            .map_err(map_actor_error)
    }

    pub fn record_recovery_required(
        &mut self,
        command: RecordCapitalRecoveryRequired,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor
            .record_recovery_required(actor_input::RecordCapitalRecoveryRequired {
                capital_group_id: command.capital_group_id,
                plan_id: command.plan_id,
                reason: command.reason,
                at: command.at,
            })
            .map_err(map_actor_error)
    }

    pub fn observe_settlement(
        &mut self,
        command: ObserveCapitalSettlement,
    ) -> Result<CapitalPlan, CapitalError> {
        self.actor
            .observe_settlement(actor_input::ObserveCapitalSettlement {
                capital_group_id: command.capital_group_id,
                plan_id: command.plan_id,
                source: command.source,
                destination: command.destination,
                observed_at: command.observed_at,
            })
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
        ActorError::Domain(error) => CapitalError::InvalidDomain(error),
        ActorError::Invalid(message) => CapitalError::Invalid(message),
        ActorError::Rejected(message) => CapitalError::Rejected(message),
        ActorError::State(message) => CapitalError::State(message),
        ActorError::Persistence(message) => CapitalError::Persistence(message),
    }
}
