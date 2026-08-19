use std::collections::{HashMap, HashSet};

use kairos_primitives::{Quantity, Sequence, UnixNanos};

use crate::application::{
    AuthorizeCapitalPlan, BeginCapitalOperation, CancelFundingObjective, CapitalDemandReceipt,
    CapitalEvent, CapitalSnapshot, EvaluateCapitalGroup, FundingObjectiveReceipt,
    MarkCapitalDeliveryStarted, ObserveCapitalDemand, ObserveCapitalFacts,
    ObserveCapitalSettlement, PublishFundingObjective, RecordCapitalParticipantStatus,
    RecordCapitalSubmission, UpdateCapitalPolicy, UpdateCapitalRoute,
};
use crate::domain::{
    CapitalAvailabilityView, CapitalDemandId, CapitalDemandRecord, CapitalDemandStatus,
    CapitalFacts, CapitalGroupConfig, CapitalGroupId, CapitalOperation, CapitalOperationId,
    CapitalOperationStatus, CapitalParticipantOperationState, CapitalPlan, CapitalPlanId,
    CapitalPlanStatus, CapitalPolicy, CapitalReadiness, CapitalReservation, CapitalReservationId,
    CapitalReservationStatus, CapitalRouteId, CapitalSubmissionOutcome, CapitalTransferRoute,
    FundingLocation, FundingObjectiveId, FundingObjectiveRecord, FundingObjectiveStatus,
};
use crate::services::persistence::{CapitalJournalRecord, JournalCapitalStore};

#[derive(Debug)]
pub(crate) enum ActorError {
    Invalid(String),
    Rejected(String),
    State(String),
    Persistence(String),
}

pub(crate) struct CapitalActor {
    config: CapitalGroupConfig,
    event_sequence: Sequence,
    journal_sequence: Sequence,
    objectives: HashMap<FundingObjectiveId, FundingObjectiveRecord>,
    demands: HashMap<CapitalDemandId, CapitalDemandRecord>,
    policies: HashMap<FundingLocation, CapitalPolicy>,
    facts: HashMap<FundingLocation, CapitalFacts>,
    availability: HashMap<FundingLocation, CapitalAvailabilityView>,
    routes: HashMap<CapitalRouteId, CapitalTransferRoute>,
    plans: HashMap<CapitalPlanId, CapitalPlan>,
    reservations: HashMap<CapitalReservationId, CapitalReservation>,
    operations: HashMap<CapitalOperationId, CapitalOperation>,
    pending_events: Vec<CapitalEvent>,
    store: Option<JournalCapitalStore>,
}

impl CapitalActor {
    pub(crate) fn new(
        config: CapitalGroupConfig,
        mut store: Option<JournalCapitalStore>,
    ) -> Result<Self, String> {
        config.validate()?;
        let recovered = store.as_mut().map(JournalCapitalStore::load).transpose()?;
        let mut actor = Self {
            config,
            event_sequence: Sequence::new(0),
            journal_sequence: Sequence::new(0),
            objectives: HashMap::new(),
            demands: HashMap::new(),
            policies: HashMap::new(),
            facts: HashMap::new(),
            availability: HashMap::new(),
            routes: HashMap::new(),
            plans: HashMap::new(),
            reservations: HashMap::new(),
            operations: HashMap::new(),
            pending_events: Vec::new(),
            store,
        };
        if let Some(recovered) = recovered {
            if let Some(snapshot) = recovered.snapshot {
                actor.restore(snapshot)?;
            }
            for record in recovered.records {
                actor.replay(record)?;
            }
        }
        Ok(actor)
    }

    pub(crate) fn publish(
        &mut self,
        command: PublishFundingObjective,
    ) -> Result<FundingObjectiveReceipt, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        command.objective.validate().map_err(ActorError::Invalid)?;
        if command.objective.strategy_id != self.config.strategy_id {
            return Err(ActorError::Rejected(
                "funding objective belongs to another Strategy".into(),
            ));
        }
        if !self.config.contains(&command.objective.destination) {
            return Err(ActorError::Rejected(
                "funding objective destination is outside this CapitalGroup".into(),
            ));
        }
        if command.objective.expires_at <= command.observed_at {
            return Err(ActorError::Rejected(
                "funding objective is already expired".into(),
            ));
        }
        if let Some(existing) = self.objectives.get(&command.objective.objective_id) {
            if command.objective.version < existing.objective.version {
                return Err(ActorError::Rejected(
                    "funding objective version cannot move backwards".into(),
                ));
            }
            if command.objective.version == existing.objective.version {
                if command.objective == existing.objective
                    && existing.status == FundingObjectiveStatus::Active
                {
                    return Ok(FundingObjectiveReceipt::Duplicate(existing.clone()));
                }
                return Err(ActorError::Rejected(
                    "funding objective version already has different state".into(),
                ));
            }
        }
        let record = FundingObjectiveRecord {
            objective: command.objective,
            status: FundingObjectiveStatus::Active,
            updated_at: command.observed_at,
        };
        self.persist_and_apply(record.clone())?;
        Ok(FundingObjectiveReceipt::Accepted(record))
    }

    pub(crate) fn cancel(
        &mut self,
        command: CancelFundingObjective,
    ) -> Result<FundingObjectiveReceipt, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        let existing = self
            .objectives
            .get(&command.objective_id)
            .ok_or_else(|| ActorError::Rejected("funding objective was not found".into()))?;
        if existing.objective.version != command.expected_version {
            return Err(ActorError::Rejected(
                "funding objective expected version does not match".into(),
            ));
        }
        if existing.status == FundingObjectiveStatus::Cancelled {
            return Ok(FundingObjectiveReceipt::Duplicate(existing.clone()));
        }
        if existing.status != FundingObjectiveStatus::Active {
            return Err(ActorError::Rejected(
                "only an active funding objective can be cancelled".into(),
            ));
        }
        let mut record = existing.clone();
        record.status = FundingObjectiveStatus::Cancelled;
        record.updated_at = command.observed_at;
        self.persist_and_apply(record.clone())?;
        Ok(FundingObjectiveReceipt::Cancelled(record))
    }

    pub(crate) fn expire(&mut self, observed_at: UnixNanos) -> Result<usize, ActorError> {
        let expired = self
            .objectives
            .values()
            .filter(|record| {
                record.status == FundingObjectiveStatus::Active
                    && record.objective.expires_at <= observed_at
            })
            .cloned()
            .collect::<Vec<_>>();
        for mut record in expired.iter().cloned() {
            record.status = FundingObjectiveStatus::Expired;
            record.updated_at = observed_at;
            self.persist_and_apply(record)?;
        }
        Ok(expired.len())
    }

    pub(crate) fn observe_demand(
        &mut self,
        command: ObserveCapitalDemand,
    ) -> Result<CapitalDemandReceipt, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        command.demand.validate().map_err(ActorError::Invalid)?;
        if command.demand.strategy_id != self.config.strategy_id {
            return Err(ActorError::Rejected(
                "capital demand belongs to another Strategy".into(),
            ));
        }
        self.validate_location(&command.demand.destination)?;
        if let Some(existing) = self.demands.get(&command.demand.demand_id) {
            if existing.demand == command.demand {
                return Ok(CapitalDemandReceipt::Duplicate(existing.clone()));
            }
            return Err(ActorError::Rejected(
                "capital demand id already has different evidence".into(),
            ));
        }
        if self.demands.values().any(|record| {
            record.demand.idempotency_key == command.demand.idempotency_key
                && record.demand != command.demand
        }) {
            return Err(ActorError::Rejected(
                "capital demand idempotency key already has different evidence".into(),
            ));
        }
        let record = CapitalDemandRecord {
            updated_at: command.demand.observed_at,
            demand: command.demand,
            status: CapitalDemandStatus::Active,
        };
        self.persist_and_apply_demand(record.clone())?;
        Ok(CapitalDemandReceipt::Accepted(record))
    }

    pub(crate) fn expire_demands(&mut self, observed_at: UnixNanos) -> Result<usize, ActorError> {
        let expired = self
            .demands
            .values()
            .filter(|record| {
                record.status == CapitalDemandStatus::Active
                    && record.demand.expires_at <= observed_at
            })
            .cloned()
            .collect::<Vec<_>>();
        for mut record in expired.iter().cloned() {
            record.status = CapitalDemandStatus::Expired;
            record.updated_at = observed_at;
            self.persist_and_apply_demand(record)?;
        }
        Ok(expired.len())
    }

    pub(crate) fn demand(&self, demand_id: &CapitalDemandId) -> Option<&CapitalDemandRecord> {
        self.demands.get(demand_id)
    }

    pub(crate) fn update_policy(&mut self, command: UpdateCapitalPolicy) -> Result<(), ActorError> {
        self.validate_group(&command.capital_group_id)?;
        command.policy.validate().map_err(ActorError::Invalid)?;
        self.validate_location(&command.policy.destination)?;
        if let Some(existing) = self.policies.get(&command.policy.destination) {
            if command.policy.version <= existing.version {
                return Err(ActorError::Rejected(
                    "capital policy version must increase".into(),
                ));
            }
        }
        self.persist_and_apply_policy(command.policy)
    }

    pub(crate) fn observe_facts(&mut self, command: ObserveCapitalFacts) -> Result<(), ActorError> {
        self.validate_group(&command.capital_group_id)?;
        self.validate_location(&command.facts.destination)?;
        if command.facts.account_watermark.get() == 0
            || command.facts.risk_policy_version.get() == 0
            || command.facts.risk_watermark.get() == 0
        {
            return Err(ActorError::Invalid(
                "Capital facts require non-zero Account/Risk watermarks".into(),
            ));
        }
        if let Some(existing) = self.facts.get(&command.facts.destination) {
            if command.facts.account_watermark < existing.account_watermark
                || command.facts.risk_watermark < existing.risk_watermark
                || command.facts.risk_policy_version < existing.risk_policy_version
            {
                return Err(ActorError::Rejected(
                    "Capital facts cannot move Account/Risk watermarks backwards".into(),
                ));
            }
        }
        self.persist_and_apply_facts(command.facts)
    }

    pub(crate) fn evaluate(
        &mut self,
        command: EvaluateCapitalGroup,
    ) -> Result<Vec<CapitalAvailabilityView>, ActorError> {
        let mut policies = self.policies.values().cloned().collect::<Vec<_>>();
        policies.sort_by(|left, right| {
            location_key(&left.destination).cmp(&location_key(&right.destination))
        });
        let availability = policies
            .iter()
            .map(|policy| self.evaluate_location(policy, command.evaluated_at))
            .collect::<Result<Vec<_>, _>>()?;
        if availability.is_empty() {
            return Ok(Vec::new());
        }
        self.persist_and_apply_availability(availability.clone())?;
        Ok(availability)
    }

    pub(crate) fn availability(
        &self,
        location: &FundingLocation,
    ) -> Option<&CapitalAvailabilityView> {
        self.availability.get(location)
    }

    pub(crate) fn update_route(&mut self, command: UpdateCapitalRoute) -> Result<(), ActorError> {
        self.validate_group(&command.capital_group_id)?;
        command.route.validate().map_err(ActorError::Invalid)?;
        self.validate_location(&command.route.source)?;
        self.validate_location(&command.route.destination)?;
        if let Some(existing) = self.routes.get(&command.route.route_id) {
            if command.route.version <= existing.version {
                return Err(ActorError::Rejected(
                    "capital route version must increase".into(),
                ));
            }
            if self.plans.values().any(|plan| {
                plan.route_id == command.route.route_id
                    && !matches!(
                        plan.status,
                        CapitalPlanStatus::Completed
                            | CapitalPlanStatus::Rejected
                            | CapitalPlanStatus::Expired
                            | CapitalPlanStatus::Failed
                    )
            }) {
                return Err(ActorError::Rejected(
                    "capital route cannot change while it has an active plan".into(),
                ));
            }
        }
        self.persist_and_apply_route(command.route)
    }

    pub(crate) fn authorize_plan(
        &mut self,
        command: AuthorizeCapitalPlan,
    ) -> Result<CapitalPlan, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        if command.rebalance_decision_id.is_empty()
            || command.rebalance_decision_id.trim() != command.rebalance_decision_id
        {
            return Err(ActorError::Invalid(
                "rebalance_decision_id is required".into(),
            ));
        }
        if command.expires_at <= command.created_at {
            return Err(ActorError::Invalid(
                "Capital plan expiry must follow creation".into(),
            ));
        }
        if let Some(existing) = self.plans.get(&command.plan_id) {
            if existing.rebalance_decision_id == command.rebalance_decision_id
                && existing.route_id == command.route_id
                && existing.created_at == command.created_at
                && existing.expires_at == command.expires_at
            {
                return Ok(existing.clone());
            }
            return Err(ActorError::Rejected(
                "Capital plan id already has different state".into(),
            ));
        }
        let route = self
            .routes
            .get(&command.route_id)
            .cloned()
            .ok_or_else(|| ActorError::Rejected("Capital route was not found".into()))?;
        if !route.enabled {
            return Err(ActorError::Rejected("Capital route is disabled".into()));
        }
        if route.required_source_authority != command.source_authority {
            return Err(ActorError::Rejected(
                "Capital source authority does not match the route".into(),
            ));
        }
        let view = self
            .availability
            .get(&route.destination)
            .cloned()
            .ok_or_else(|| ActorError::Rejected("Capital destination was not evaluated".into()))?;
        if view.readiness != CapitalReadiness::Ready || view.deficit.is_zero() {
            return Err(ActorError::Rejected(
                "Capital destination has no actionable deficit".into(),
            ));
        }
        let policy = self
            .policies
            .get(&route.destination)
            .ok_or_else(|| ActorError::State("Capital destination policy disappeared".into()))?;
        let source = self
            .facts
            .get(&route.source)
            .ok_or_else(|| ActorError::Rejected("Capital source facts are unavailable".into()))?;
        if !source.account_complete || command.created_at < source.account_observed_at {
            return Err(ActorError::Rejected(
                "Capital source facts are incomplete or from the future".into(),
            ));
        }
        if command.created_at.get() - source.account_observed_at.get() > policy.max_fact_age_nanos {
            return Err(ActorError::Rejected(
                "Capital source facts are stale".into(),
            ));
        }
        let reserved = self.active_reserved_at(&route.source)?;
        let source_available = if source.observed_available > reserved {
            source
                .observed_available
                .checked_sub(reserved)
                .map_err(|error| ActorError::State(error.to_string()))?
        } else {
            Quantity::ZERO
        };
        let daily_used = self.daily_used(&route, command.created_at)?;
        let daily_remaining = if route.daily_limit > daily_used {
            route
                .daily_limit
                .checked_sub(daily_used)
                .map_err(|error| ActorError::State(error.to_string()))?
        } else {
            Quantity::ZERO
        };
        let inbound_reserved = self.active_inbound_to(&route.destination)?;
        let actionable_deficit = if view.deficit > inbound_reserved {
            view.deficit
                .checked_sub(inbound_reserved)
                .map_err(|error| ActorError::State(error.to_string()))?
        } else {
            Quantity::ZERO
        };
        let amount = actionable_deficit
            .min(route.per_operation_limit)
            .min(daily_remaining)
            .min(source_available);
        if amount < policy.minimum_movement || amount.is_zero() {
            return Err(ActorError::Rejected(
                "Capital route has insufficient unreserved capacity".into(),
            ));
        }
        let reservation_id =
            CapitalReservationId::new(format!("capital-reservation:{}", command.plan_id.as_str()))
                .map_err(ActorError::Invalid)?;
        let idempotency_key = kairos_primitives::IdempotencyKey::new(format!(
            "{}:0:transfer",
            command.plan_id.as_str()
        ))
        .map_err(|error| ActorError::Invalid(error.to_string()))?;
        let reservation = CapitalReservation {
            reservation_id: reservation_id.clone(),
            plan_id: command.plan_id.clone(),
            source: route.source.clone(),
            amount,
            source_account_watermark: source.account_watermark,
            status: CapitalReservationStatus::Active,
            created_at: command.created_at,
            expires_at: command.expires_at,
        };
        let plan = CapitalPlan {
            plan_id: command.plan_id,
            rebalance_decision_id: command.rebalance_decision_id,
            route_id: route.route_id,
            route_version: route.version,
            source: route.source,
            destination: route.destination,
            amount,
            objective_ids: view.active_objective_ids,
            demand_ids: view.active_demand_ids,
            reservation_id,
            idempotency_key,
            source_account_watermark: source.account_watermark,
            destination_account_watermark: view.account_watermark,
            source_observed_available: source.observed_available,
            destination_observed_available: view.observed_available,
            status: CapitalPlanStatus::Authorized,
            created_at: command.created_at,
            expires_at: command.expires_at,
        };
        self.persist_and_apply_plan(plan.clone(), reservation)?;
        Ok(plan)
    }

    pub(crate) fn expire_plans(&mut self, observed_at: UnixNanos) -> Result<usize, ActorError> {
        let candidates = self
            .plans
            .values()
            .filter(|plan| {
                plan.expires_at <= observed_at
                    && matches!(
                        plan.status,
                        CapitalPlanStatus::Authorized | CapitalPlanStatus::Transferring
                    )
            })
            .map(|plan| plan.plan_id.clone())
            .collect::<Vec<_>>();
        let mut expired = 0;
        for plan_id in candidates {
            let mut plan = self.plan(&plan_id)?;
            let mut reservation = self.reservation(&plan.reservation_id)?;
            let mut operation = self.operation_for_plan(&plan_id).cloned();
            if operation
                .as_ref()
                .is_some_and(|operation| operation.status != CapitalOperationStatus::Prepared)
            {
                // Once delivery may have started, timeout is a reconciliation
                // concern and cannot release source capacity.
                continue;
            }
            plan.status = CapitalPlanStatus::Expired;
            reservation.status = CapitalReservationStatus::Expired;
            if let Some(operation) = &mut operation {
                operation.status = CapitalOperationStatus::Expired;
                operation.updated_at = observed_at;
            }
            self.persist_and_apply_plan_expired(plan, reservation, operation)?;
            expired += 1;
        }
        Ok(expired)
    }

    pub(crate) fn begin_operation(
        &mut self,
        command: BeginCapitalOperation,
    ) -> Result<CapitalOperation, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        let mut plan = self.plan(&command.plan_id)?;
        let reservation = self.reservation(&plan.reservation_id)?;
        if let Some(operation) = self.operation_for_plan(&plan.plan_id) {
            return Ok(operation.clone());
        }
        if plan.status != CapitalPlanStatus::Authorized {
            return Err(ActorError::Rejected(
                "only an Authorized Capital plan can begin".into(),
            ));
        }
        if command.at >= plan.expires_at {
            return Err(ActorError::Rejected("Capital plan is expired".into()));
        }
        let operation_id = CapitalOperationId::new(format!(
            "capital-operation:{}:0:transfer",
            plan.plan_id.as_str()
        ))
        .map_err(ActorError::Invalid)?;
        let operation = CapitalOperation {
            operation_id,
            plan_id: plan.plan_id.clone(),
            idempotency_key: plan.idempotency_key.clone(),
            status: CapitalOperationStatus::Prepared,
            participant_operation_id: None,
            participant_state: None,
            dispatch_started_at: None,
            attempt_count: 1,
            failure_reason: None,
            updated_at: command.at,
        };
        plan.status = CapitalPlanStatus::Transferring;
        self.persist_and_apply_plan_state(plan, reservation, operation.clone())?;
        Ok(operation)
    }

    pub(crate) fn mark_delivery_started(
        &mut self,
        command: MarkCapitalDeliveryStarted,
    ) -> Result<CapitalOperation, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        let plan = self.plan(&command.plan_id)?;
        let reservation = self.reservation(&plan.reservation_id)?;
        let mut operation = self
            .operation_for_plan(&plan.plan_id)
            .cloned()
            .ok_or_else(|| ActorError::Rejected("Capital operation was not prepared".into()))?;
        if operation.status != CapitalOperationStatus::Prepared {
            return Ok(operation);
        }
        if command.at < operation.updated_at {
            return Err(ActorError::Invalid(
                "Capital operation time cannot move backwards".into(),
            ));
        }
        operation.status = CapitalOperationStatus::Dispatching;
        operation.dispatch_started_at = Some(command.at);
        operation.updated_at = command.at;
        self.persist_and_apply_plan_state(plan, reservation, operation.clone())?;
        Ok(operation)
    }

    pub(crate) fn record_submission(
        &mut self,
        command: RecordCapitalSubmission,
    ) -> Result<CapitalPlan, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        let mut plan = self.plan(&command.plan_id)?;
        let mut reservation = self.reservation(&plan.reservation_id)?;
        let mut operation = self
            .operation_for_plan(&plan.plan_id)
            .cloned()
            .ok_or_else(|| ActorError::Rejected("Capital operation was not prepared".into()))?;
        if operation.status != CapitalOperationStatus::Dispatching {
            return Ok(plan);
        }
        if command.at < operation.updated_at {
            return Err(ActorError::Invalid(
                "Capital operation time cannot move backwards".into(),
            ));
        }
        operation.participant_operation_id = command.participant_operation_id;
        operation.failure_reason = command.failure_reason;
        operation.updated_at = command.at;
        match command.outcome {
            CapitalSubmissionOutcome::Confirmed => {
                plan.status = CapitalPlanStatus::AwaitingTransfer;
                operation.status = CapitalOperationStatus::AwaitingParticipant;
            }
            CapitalSubmissionOutcome::Rejected => {
                plan.status = CapitalPlanStatus::Rejected;
                operation.status = CapitalOperationStatus::Rejected;
                reservation.status = CapitalReservationStatus::Released;
            }
            CapitalSubmissionOutcome::Indeterminate => {
                plan.status = CapitalPlanStatus::Indeterminate;
                operation.status = CapitalOperationStatus::Indeterminate;
            }
        }
        self.persist_and_apply_plan_state(plan.clone(), reservation, operation)?;
        Ok(plan)
    }

    pub(crate) fn record_participant_status(
        &mut self,
        command: RecordCapitalParticipantStatus,
    ) -> Result<CapitalPlan, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        let mut plan = self.plan(&command.plan_id)?;
        let mut reservation = self.reservation(&plan.reservation_id)?;
        let mut operation = self
            .operation_for_plan(&plan.plan_id)
            .cloned()
            .ok_or_else(|| ActorError::Rejected("Capital operation was not prepared".into()))?;
        if !matches!(
            operation.status,
            CapitalOperationStatus::Dispatching
                | CapitalOperationStatus::AwaitingParticipant
                | CapitalOperationStatus::Indeterminate
        ) {
            return Err(ActorError::Rejected(
                "Capital operation is not awaiting participant reconciliation".into(),
            ));
        }
        if command.at < operation.updated_at {
            return Err(ActorError::Invalid(
                "Capital operation time cannot move backwards".into(),
            ));
        }
        if let Some(participant_operation_id) = command.participant_operation_id {
            if operation
                .participant_operation_id
                .as_ref()
                .is_some_and(|existing| existing != &participant_operation_id)
            {
                return Err(ActorError::Rejected(
                    "Capital participant operation id cannot change".into(),
                ));
            }
            operation.participant_operation_id = Some(participant_operation_id);
        }
        operation.participant_state = command.participant_state;
        operation.failure_reason = command.failure_reason;
        operation.updated_at = command.at;
        match command.state {
            CapitalParticipantOperationState::Pending => {
                plan.status = CapitalPlanStatus::AwaitingTransfer;
                operation.status = CapitalOperationStatus::AwaitingParticipant;
            }
            CapitalParticipantOperationState::Succeeded => {
                plan.status = CapitalPlanStatus::Reconciling;
                operation.status = CapitalOperationStatus::AwaitingAccountObservation;
            }
            CapitalParticipantOperationState::Failed
            | CapitalParticipantOperationState::Cancelled => {
                plan.status = CapitalPlanStatus::Failed;
                operation.status = CapitalOperationStatus::Failed;
                reservation.status = CapitalReservationStatus::Released;
            }
            CapitalParticipantOperationState::Unknown => {
                plan.status = CapitalPlanStatus::Indeterminate;
                operation.status = CapitalOperationStatus::Indeterminate;
            }
        }
        self.persist_and_apply_plan_state(plan.clone(), reservation, operation)?;
        Ok(plan)
    }

    pub(crate) fn observe_settlement(
        &mut self,
        command: ObserveCapitalSettlement,
    ) -> Result<CapitalPlan, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        let mut plan = self.plan(&command.plan_id)?;
        let mut reservation = self.reservation(&plan.reservation_id)?;
        let mut operation = self
            .operation_for_plan(&plan.plan_id)
            .cloned()
            .ok_or_else(|| ActorError::Rejected("Capital operation was not prepared".into()))?;
        if plan.status != CapitalPlanStatus::Reconciling
            || operation.status != CapitalOperationStatus::AwaitingAccountObservation
        {
            return Err(ActorError::Rejected(
                "Capital plan is not awaiting Account settlement".into(),
            ));
        }
        if command.source.destination != plan.source
            || command.destination.destination != plan.destination
        {
            return Err(ActorError::Rejected(
                "Capital settlement facts do not match plan endpoints".into(),
            ));
        }
        if !command.source.account_complete || !command.destination.account_complete {
            return Ok(plan);
        }
        let source_expected = plan
            .source_observed_available
            .checked_sub(plan.amount)
            .map_err(|error| ActorError::State(error.to_string()))?;
        let destination_expected = plan
            .destination_observed_available
            .checked_add(plan.amount)
            .map_err(|error| ActorError::State(error.to_string()))?;
        let settled = command.source.account_watermark > plan.source_account_watermark
            && command.destination.account_watermark > plan.destination_account_watermark
            && command.source.observed_available <= source_expected
            && command.destination.observed_available >= destination_expected;
        if !settled {
            return Ok(plan);
        }
        plan.status = CapitalPlanStatus::Completed;
        reservation.status = CapitalReservationStatus::Consumed;
        operation.status = CapitalOperationStatus::Settled;
        operation.updated_at = command.observed_at;
        self.persist_and_apply_plan_state(plan.clone(), reservation, operation)?;
        Ok(plan)
    }

    pub(crate) fn snapshot(&self) -> CapitalSnapshot {
        let mut objectives = self.objectives.values().cloned().collect::<Vec<_>>();
        objectives.sort_by(|left, right| {
            left.objective
                .objective_id
                .cmp(&right.objective.objective_id)
        });
        CapitalSnapshot {
            capital_group_id: self.config.capital_group_id.clone(),
            strategy_id: self.config.strategy_id.clone(),
            environment: self.config.environment.clone(),
            membership_version: self.config.membership_version,
            members: self.config.members.clone(),
            event_sequence: self.event_sequence,
            journal_sequence: self.journal_sequence,
            objectives,
            demands: sorted_id_values(&self.demands),
            policies: sorted_values(&self.policies),
            facts: sorted_values(&self.facts),
            availability: sorted_values(&self.availability),
            routes: sorted_id_values(&self.routes),
            plans: sorted_id_values(&self.plans),
            reservations: sorted_id_values(&self.reservations),
            operations: sorted_id_values(&self.operations),
            pending_events: self.pending_events.clone(),
        }
    }

    pub(crate) fn plan_view(&self, plan_id: &CapitalPlanId) -> Option<&CapitalPlan> {
        self.plans.get(plan_id)
    }

    pub(crate) fn operation_view_for_plan(
        &self,
        plan_id: &CapitalPlanId,
    ) -> Option<&CapitalOperation> {
        self.operation_for_plan(plan_id)
    }

    pub(crate) fn pending_event(&self) -> Option<&CapitalEvent> {
        self.pending_events.first()
    }

    pub(crate) fn acknowledge_event(&mut self) -> Result<(), ActorError> {
        let Some(event) = self.pending_events.first() else {
            return Ok(());
        };
        let event_sequence = match event {
            CapitalEvent::FundingObjectiveChanged { event_sequence, .. }
            | CapitalEvent::CapitalDemandChanged { event_sequence, .. }
            | CapitalEvent::PolicyChanged { event_sequence, .. }
            | CapitalEvent::FactsObserved { event_sequence, .. }
            | CapitalEvent::AvailabilityEvaluated { event_sequence, .. } => *event_sequence,
            CapitalEvent::RouteChanged { event_sequence, .. }
            | CapitalEvent::PlanAuthorized { event_sequence, .. }
            | CapitalEvent::PlanStateChanged { event_sequence, .. }
            | CapitalEvent::PlanExpired { event_sequence, .. } => *event_sequence,
        };
        let journal_sequence = self.next_journal_sequence()?;
        self.persist(&CapitalJournalRecord::PublicationAcknowledged {
            journal_sequence,
            event_sequence: event_sequence.get(),
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.pending_events.remove(0);
        self.checkpoint()?;
        Ok(())
    }

    fn validate_group(&self, group_id: &CapitalGroupId) -> Result<(), ActorError> {
        if group_id != &self.config.capital_group_id {
            return Err(ActorError::Rejected(
                "capital command belongs to another group".into(),
            ));
        }
        Ok(())
    }

    fn validate_location(&self, location: &FundingLocation) -> Result<(), ActorError> {
        if !self.config.contains(location) {
            return Err(ActorError::Rejected(
                "Capital location is outside this CapitalGroup".into(),
            ));
        }
        Ok(())
    }

    fn persist_and_apply(&mut self, record: FundingObjectiveRecord) -> Result<(), ActorError> {
        let next_sequence = self
            .event_sequence
            .get()
            .checked_add(1)
            .ok_or_else(|| ActorError::State("capital event sequence overflow".into()))?;
        let journal_sequence = self.next_journal_sequence()?;
        self.persist(&CapitalJournalRecord::ObjectiveChanged {
            journal_sequence,
            event_sequence: next_sequence,
            objective: Box::new(record.clone()),
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.apply_record(record, Sequence::new(next_sequence));
        self.checkpoint()?;
        Ok(())
    }

    fn persist_and_apply_demand(&mut self, record: CapitalDemandRecord) -> Result<(), ActorError> {
        let (journal_sequence, event_sequence) = self.next_sequences()?;
        self.persist(&CapitalJournalRecord::DemandChanged {
            journal_sequence,
            event_sequence,
            demand: Box::new(record.clone()),
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        self.demands
            .insert(record.demand.demand_id.clone(), record.clone());
        self.pending_events
            .push(CapitalEvent::CapitalDemandChanged {
                record,
                event_sequence: self.event_sequence,
            });
        self.checkpoint()
    }

    fn persist_and_apply_policy(&mut self, policy: CapitalPolicy) -> Result<(), ActorError> {
        let (journal_sequence, event_sequence) = self.next_sequences()?;
        self.persist(&CapitalJournalRecord::PolicyChanged {
            journal_sequence,
            event_sequence,
            policy: Box::new(policy.clone()),
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        self.policies
            .insert(policy.destination.clone(), policy.clone());
        self.pending_events.push(CapitalEvent::PolicyChanged {
            policy,
            event_sequence: self.event_sequence,
        });
        self.checkpoint()
    }

    fn persist_and_apply_facts(&mut self, facts: CapitalFacts) -> Result<(), ActorError> {
        let (journal_sequence, event_sequence) = self.next_sequences()?;
        self.persist(&CapitalJournalRecord::FactsObserved {
            journal_sequence,
            event_sequence,
            facts: Box::new(facts.clone()),
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        self.facts.insert(facts.destination.clone(), facts.clone());
        self.pending_events.push(CapitalEvent::FactsObserved {
            facts,
            event_sequence: self.event_sequence,
        });
        self.checkpoint()
    }

    fn persist_and_apply_availability(
        &mut self,
        availability: Vec<CapitalAvailabilityView>,
    ) -> Result<(), ActorError> {
        let (journal_sequence, event_sequence) = self.next_sequences()?;
        self.persist(&CapitalJournalRecord::AvailabilityBatchEvaluated {
            journal_sequence,
            event_sequence,
            availability: availability.clone(),
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        for view in &availability {
            self.availability
                .insert(view.destination.clone(), view.clone());
        }
        self.pending_events
            .push(CapitalEvent::AvailabilityEvaluated {
                availability,
                event_sequence: self.event_sequence,
            });
        self.checkpoint()
    }

    fn persist_and_apply_route(&mut self, route: CapitalTransferRoute) -> Result<(), ActorError> {
        let (journal_sequence, event_sequence) = self.next_sequences()?;
        self.persist(&CapitalJournalRecord::RouteChanged {
            journal_sequence,
            event_sequence,
            route: Box::new(route.clone()),
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        self.routes.insert(route.route_id.clone(), route.clone());
        self.pending_events.push(CapitalEvent::RouteChanged {
            route,
            event_sequence: self.event_sequence,
        });
        self.checkpoint()
    }

    fn persist_and_apply_plan(
        &mut self,
        plan: CapitalPlan,
        reservation: CapitalReservation,
    ) -> Result<(), ActorError> {
        let (journal_sequence, event_sequence) = self.next_sequences()?;
        self.persist(&CapitalJournalRecord::PlanAuthorized {
            journal_sequence,
            event_sequence,
            plan: Box::new(plan.clone()),
            reservation: Box::new(reservation.clone()),
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        self.plans.insert(plan.plan_id.clone(), plan.clone());
        self.reservations
            .insert(reservation.reservation_id.clone(), reservation.clone());
        self.pending_events.push(CapitalEvent::PlanAuthorized {
            plan: Box::new(plan),
            reservation: Box::new(reservation),
            event_sequence: self.event_sequence,
        });
        self.checkpoint()
    }

    fn persist_and_apply_plan_state(
        &mut self,
        plan: CapitalPlan,
        reservation: CapitalReservation,
        operation: CapitalOperation,
    ) -> Result<(), ActorError> {
        let (journal_sequence, event_sequence) = self.next_sequences()?;
        self.persist(&CapitalJournalRecord::PlanStateChanged {
            journal_sequence,
            event_sequence,
            plan: Box::new(plan.clone()),
            reservation: Box::new(reservation.clone()),
            operation: Box::new(operation.clone()),
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        self.plans.insert(plan.plan_id.clone(), plan.clone());
        self.reservations
            .insert(reservation.reservation_id.clone(), reservation.clone());
        self.operations
            .insert(operation.operation_id.clone(), operation.clone());
        self.pending_events.push(CapitalEvent::PlanStateChanged {
            plan: Box::new(plan),
            reservation: Box::new(reservation),
            operation: Box::new(operation),
            event_sequence: self.event_sequence,
        });
        self.checkpoint()
    }

    fn persist_and_apply_plan_expired(
        &mut self,
        plan: CapitalPlan,
        reservation: CapitalReservation,
        operation: Option<CapitalOperation>,
    ) -> Result<(), ActorError> {
        let (journal_sequence, event_sequence) = self.next_sequences()?;
        self.persist(&CapitalJournalRecord::PlanExpired {
            journal_sequence,
            event_sequence,
            plan: Box::new(plan.clone()),
            reservation: Box::new(reservation.clone()),
            operation: operation.clone().map(Box::new),
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        self.plans.insert(plan.plan_id.clone(), plan.clone());
        self.reservations
            .insert(reservation.reservation_id.clone(), reservation.clone());
        if let Some(operation) = &operation {
            self.operations
                .insert(operation.operation_id.clone(), operation.clone());
        }
        self.pending_events.push(CapitalEvent::PlanExpired {
            plan: Box::new(plan),
            reservation: Box::new(reservation),
            operation: operation.map(Box::new),
            event_sequence: self.event_sequence,
        });
        self.checkpoint()
    }

    fn plan(&self, plan_id: &CapitalPlanId) -> Result<CapitalPlan, ActorError> {
        self.plans
            .get(plan_id)
            .cloned()
            .ok_or_else(|| ActorError::Rejected("Capital plan was not found".into()))
    }

    fn reservation(
        &self,
        reservation_id: &CapitalReservationId,
    ) -> Result<CapitalReservation, ActorError> {
        self.reservations
            .get(reservation_id)
            .cloned()
            .ok_or_else(|| ActorError::State("Capital reservation disappeared".into()))
    }

    fn operation_for_plan(&self, plan_id: &CapitalPlanId) -> Option<&CapitalOperation> {
        self.operations
            .values()
            .find(|operation| operation.plan_id == *plan_id)
    }

    fn active_reserved_at(&self, source: &FundingLocation) -> Result<Quantity, ActorError> {
        self.reservations
            .values()
            .filter(|reservation| {
                reservation.source == *source
                    && reservation.status == CapitalReservationStatus::Active
            })
            .try_fold(Quantity::ZERO, |total, reservation| {
                total
                    .checked_add(reservation.amount)
                    .map_err(|error| ActorError::State(error.to_string()))
            })
    }

    fn active_inbound_to(&self, destination: &FundingLocation) -> Result<Quantity, ActorError> {
        self.plans
            .values()
            .filter(|plan| {
                plan.destination == *destination
                    && !matches!(
                        plan.status,
                        CapitalPlanStatus::Completed
                            | CapitalPlanStatus::Rejected
                            | CapitalPlanStatus::Expired
                            | CapitalPlanStatus::Failed
                    )
            })
            .try_fold(Quantity::ZERO, |total, plan| {
                total
                    .checked_add(plan.amount)
                    .map_err(|error| ActorError::State(error.to_string()))
            })
    }

    fn daily_used(
        &self,
        route: &CapitalTransferRoute,
        at: UnixNanos,
    ) -> Result<Quantity, ActorError> {
        const DAY_NANOS: u64 = 86_400_000_000_000;
        let day = at.get() / DAY_NANOS;
        self.plans
            .values()
            .filter(|plan| {
                plan.route_id == route.route_id
                    && plan.created_at.get() / DAY_NANOS == day
                    && !matches!(
                        plan.status,
                        CapitalPlanStatus::Expired
                            | CapitalPlanStatus::Rejected
                            | CapitalPlanStatus::Failed
                    )
            })
            .try_fold(Quantity::ZERO, |total, plan| {
                total
                    .checked_add(plan.amount)
                    .map_err(|error| ActorError::State(error.to_string()))
            })
    }

    fn evaluate_location(
        &self,
        policy: &CapitalPolicy,
        evaluated_at: UnixNanos,
    ) -> Result<CapitalAvailabilityView, ActorError> {
        let active = self
            .objectives
            .values()
            .filter(|record| {
                record.status == FundingObjectiveStatus::Active
                    && record.objective.destination == policy.destination
                    && record.objective.expires_at > evaluated_at
            })
            .collect::<Vec<_>>();
        let mut objective_ids = active
            .iter()
            .map(|record| record.objective.objective_id.clone())
            .collect::<Vec<_>>();
        objective_ids.sort();
        let objective_target = active
            .iter()
            .map(|record| record.objective.desired_available)
            .max()
            .unwrap_or(Quantity::ZERO);
        let active_demands = self
            .demands
            .values()
            .filter(|record| {
                record.status == CapitalDemandStatus::Active
                    && record.demand.destination == policy.destination
                    && record.demand.expires_at > evaluated_at
            })
            .collect::<Vec<_>>();
        let mut demand_ids = active_demands
            .iter()
            .map(|record| record.demand.demand_id.clone())
            .collect::<Vec<_>>();
        demand_ids.sort();
        let demand_shortfall = active_demands
            .iter()
            .map(|record| record.demand.observed_shortfall)
            .max()
            .unwrap_or(Quantity::ZERO);
        let base_desired = policy.default_target.max(objective_target);

        let Some(facts) = self.facts.get(&policy.destination) else {
            return Ok(CapitalAvailabilityView {
                destination: policy.destination.clone(),
                readiness: CapitalReadiness::WaitingForFacts,
                policy_version: policy.version,
                active_objective_ids: objective_ids,
                active_demand_ids: demand_ids,
                desired_target: base_desired,
                effective_target: base_desired
                    .checked_add(policy.stress_buffer)
                    .map_err(|error| ActorError::Invalid(error.to_string()))?
                    .max(policy.minimum)
                    .min(policy.maximum),
                observed_available: Quantity::ZERO,
                deficit: Quantity::ZERO,
                deficit_observed_since: None,
                cooldown_until: None,
                account_watermark: Sequence::new(0),
                risk_policy_version: 0.into(),
                risk_watermark: Sequence::new(0),
                evaluated_at,
                reason: Some("Account/Risk facts are unavailable".into()),
            });
        };
        let demand_target = facts
            .observed_available
            .checked_add(demand_shortfall)
            .map_err(|error| ActorError::Invalid(error.to_string()))?;
        let desired = base_desired.max(demand_target);
        let buffered = desired
            .checked_add(policy.stress_buffer)
            .map_err(|error| ActorError::Invalid(error.to_string()))?;
        let governed = buffered.max(policy.minimum).min(policy.maximum);
        if evaluated_at < facts.account_observed_at {
            return Err(ActorError::Invalid(
                "Capital evaluation time precedes Account observation".into(),
            ));
        }
        let fact_age = evaluated_at.get() - facts.account_observed_at.get();
        let risk_below_minimum = facts.risk_capacity < policy.minimum;
        let readiness_reason = if !facts.account_complete {
            Some("Account facts are incomplete".to_string())
        } else if fact_age > policy.max_fact_age_nanos {
            Some("Account facts are stale".to_string())
        } else if risk_below_minimum {
            Some("Risk capacity is below the Capital policy minimum".to_string())
        } else {
            None
        };
        let effective = governed.min(facts.risk_capacity);
        let raw_deficit = if effective > facts.observed_available {
            effective
                .checked_sub(facts.observed_available)
                .map_err(|error| ActorError::Invalid(error.to_string()))?
        } else {
            Quantity::ZERO
        };
        let previous_deficit_since = self
            .availability
            .get(&policy.destination)
            .and_then(|view| view.deficit_observed_since);
        let deficit_observed_since = if raw_deficit.is_zero() {
            None
        } else {
            Some(previous_deficit_since.unwrap_or(evaluated_at))
        };
        let cooldown_until = self
            .plans
            .values()
            .filter(|plan| {
                plan.destination == policy.destination
                    && !matches!(
                        plan.status,
                        CapitalPlanStatus::Rejected
                            | CapitalPlanStatus::Expired
                            | CapitalPlanStatus::Failed
                    )
            })
            .map(|plan| {
                UnixNanos::new(plan.created_at.get().saturating_add(policy.cooldown_nanos))
            })
            .max();
        let dwell_satisfied = deficit_observed_since.is_some_and(|since| {
            evaluated_at.get().saturating_sub(since.get()) >= policy.deficit_dwell_nanos
        });
        let cooldown_satisfied = cooldown_until.is_none_or(|until| evaluated_at >= until);
        let mut deficit = raw_deficit;
        if deficit < policy.minimum_movement
            || deficit < policy.hysteresis
            || !dwell_satisfied
            || !cooldown_satisfied
            || readiness_reason.is_some()
        {
            deficit = Quantity::ZERO;
        }
        Ok(CapitalAvailabilityView {
            destination: policy.destination.clone(),
            readiness: if readiness_reason.is_some() {
                CapitalReadiness::Degraded
            } else {
                CapitalReadiness::Ready
            },
            policy_version: policy.version,
            active_objective_ids: objective_ids,
            active_demand_ids: demand_ids,
            desired_target: desired,
            effective_target: effective,
            observed_available: facts.observed_available,
            deficit,
            deficit_observed_since,
            cooldown_until,
            account_watermark: facts.account_watermark,
            risk_policy_version: facts.risk_policy_version,
            risk_watermark: facts.risk_watermark,
            evaluated_at,
            reason: readiness_reason,
        })
    }

    fn apply_record(&mut self, record: FundingObjectiveRecord, event_sequence: Sequence) {
        self.event_sequence = event_sequence;
        self.objectives
            .insert(record.objective.objective_id.clone(), record.clone());
        self.pending_events
            .push(CapitalEvent::FundingObjectiveChanged {
                record,
                event_sequence: self.event_sequence,
            });
    }

    fn restore(&mut self, snapshot: CapitalSnapshot) -> Result<(), String> {
        if snapshot.capital_group_id != self.config.capital_group_id
            || snapshot.strategy_id != self.config.strategy_id
            || snapshot.environment != self.config.environment
            || snapshot.membership_version != self.config.membership_version
            || snapshot.members != self.config.members
        {
            return Err("Capital snapshot identity does not match this Actor".into());
        }
        for record in &snapshot.objectives {
            if !self.config.contains(&record.objective.destination) {
                return Err("Capital snapshot objective is outside configured membership".into());
            }
        }
        for record in &snapshot.demands {
            record.demand.validate()?;
            if record.demand.strategy_id != self.config.strategy_id
                || !self.config.contains(&record.demand.destination)
            {
                return Err("Capital snapshot demand is outside configured ownership".into());
            }
        }
        for policy in &snapshot.policies {
            if !self.config.contains(&policy.destination) {
                return Err("Capital snapshot policy is outside configured membership".into());
            }
        }
        for facts in &snapshot.facts {
            if !self.config.contains(&facts.destination) {
                return Err("Capital snapshot facts are outside configured membership".into());
            }
        }
        for route in &snapshot.routes {
            route.validate()?;
            if !self.config.contains(&route.source) || !self.config.contains(&route.destination) {
                return Err("Capital snapshot route is outside configured membership".into());
            }
        }
        for plan in &snapshot.plans {
            if !self.config.contains(&plan.source) || !self.config.contains(&plan.destination) {
                return Err("Capital snapshot plan is outside configured membership".into());
            }
        }
        for reservation in &snapshot.reservations {
            if !self.config.contains(&reservation.source) {
                return Err("Capital snapshot reservation is outside configured membership".into());
            }
        }
        let mut plan_ids = HashSet::new();
        for plan in &snapshot.plans {
            if !plan_ids.insert(plan.plan_id.clone()) {
                return Err("Capital snapshot contains duplicate plan IDs".into());
            }
            let reservation = snapshot
                .reservations
                .iter()
                .find(|reservation| reservation.reservation_id == plan.reservation_id)
                .ok_or_else(|| "Capital snapshot plan has no reservation".to_string())?;
            let operation = snapshot
                .operations
                .iter()
                .find(|operation| operation.plan_id == plan.plan_id);
            validate_plan_relation(plan, reservation, operation)?;
        }
        let mut operation_ids = HashSet::new();
        for operation in &snapshot.operations {
            if !operation_ids.insert(operation.operation_id.clone()) {
                return Err("Capital snapshot contains duplicate operation IDs".into());
            }
            if !snapshot
                .plans
                .iter()
                .any(|plan| plan.plan_id == operation.plan_id)
            {
                return Err("Capital snapshot operation has no plan".into());
            }
        }
        self.event_sequence = snapshot.event_sequence;
        self.journal_sequence = snapshot.journal_sequence;
        self.objectives = snapshot
            .objectives
            .into_iter()
            .map(|record| (record.objective.objective_id.clone(), record))
            .collect();
        self.demands = snapshot
            .demands
            .into_iter()
            .map(|record| (record.demand.demand_id.clone(), record))
            .collect();
        self.policies = snapshot
            .policies
            .into_iter()
            .map(|policy| (policy.destination.clone(), policy))
            .collect();
        self.facts = snapshot
            .facts
            .into_iter()
            .map(|facts| (facts.destination.clone(), facts))
            .collect();
        self.availability = snapshot
            .availability
            .into_iter()
            .map(|view| (view.destination.clone(), view))
            .collect();
        self.routes = snapshot
            .routes
            .into_iter()
            .map(|route| (route.route_id.clone(), route))
            .collect();
        self.plans = snapshot
            .plans
            .into_iter()
            .map(|plan| (plan.plan_id.clone(), plan))
            .collect();
        self.reservations = snapshot
            .reservations
            .into_iter()
            .map(|reservation| (reservation.reservation_id.clone(), reservation))
            .collect();
        self.operations = snapshot
            .operations
            .into_iter()
            .map(|operation| (operation.operation_id.clone(), operation))
            .collect();
        self.pending_events = snapshot.pending_events;
        Ok(())
    }

    fn replay(&mut self, record: CapitalJournalRecord) -> Result<(), String> {
        let expected_journal = self.journal_sequence.get().saturating_add(1);
        if record.journal_sequence() != expected_journal {
            return Err(format!(
                "Capital journal is not contiguous: expected {expected_journal}, received {}",
                record.journal_sequence()
            ));
        }
        match record {
            CapitalJournalRecord::ObjectiveChanged {
                journal_sequence,
                event_sequence,
                objective,
            } => {
                let expected_event = self.event_sequence.get().saturating_add(1);
                if event_sequence != expected_event {
                    return Err(format!(
                        "Capital event journal is not contiguous: expected {expected_event}, received {event_sequence}"
                    ));
                }
                self.journal_sequence = Sequence::new(journal_sequence);
                self.apply_record(*objective, Sequence::new(event_sequence));
            }
            CapitalJournalRecord::DemandChanged {
                journal_sequence,
                event_sequence,
                demand,
            } => {
                demand.demand.validate()?;
                if demand.demand.strategy_id != self.config.strategy_id
                    || !self.config.contains(&demand.demand.destination)
                {
                    return Err("Capital journal demand is outside configured ownership".into());
                }
                self.replay_event_sequences(journal_sequence, event_sequence)?;
                self.demands
                    .insert(demand.demand.demand_id.clone(), (*demand).clone());
                self.pending_events
                    .push(CapitalEvent::CapitalDemandChanged {
                        record: *demand,
                        event_sequence: self.event_sequence,
                    });
            }
            CapitalJournalRecord::PolicyChanged {
                journal_sequence,
                event_sequence,
                policy,
            } => {
                self.replay_event_sequences(journal_sequence, event_sequence)?;
                self.policies
                    .insert(policy.destination.clone(), (*policy).clone());
                self.pending_events.push(CapitalEvent::PolicyChanged {
                    policy: *policy,
                    event_sequence: self.event_sequence,
                });
            }
            CapitalJournalRecord::FactsObserved {
                journal_sequence,
                event_sequence,
                facts,
            } => {
                self.replay_event_sequences(journal_sequence, event_sequence)?;
                self.facts
                    .insert(facts.destination.clone(), (*facts).clone());
                self.pending_events.push(CapitalEvent::FactsObserved {
                    facts: *facts,
                    event_sequence: self.event_sequence,
                });
            }
            CapitalJournalRecord::AvailabilityBatchEvaluated {
                journal_sequence,
                event_sequence,
                availability,
            } => {
                self.replay_event_sequences(journal_sequence, event_sequence)?;
                for view in &availability {
                    self.availability
                        .insert(view.destination.clone(), view.clone());
                }
                self.pending_events
                    .push(CapitalEvent::AvailabilityEvaluated {
                        availability,
                        event_sequence: self.event_sequence,
                    });
            }
            CapitalJournalRecord::RouteChanged {
                journal_sequence,
                event_sequence,
                route,
            } => {
                route.validate()?;
                if !self.config.contains(&route.source) || !self.config.contains(&route.destination)
                {
                    return Err("Capital journal route is outside configured membership".into());
                }
                self.replay_event_sequences(journal_sequence, event_sequence)?;
                self.routes.insert(route.route_id.clone(), (*route).clone());
                self.pending_events.push(CapitalEvent::RouteChanged {
                    route: *route,
                    event_sequence: self.event_sequence,
                });
            }
            CapitalJournalRecord::PlanAuthorized {
                journal_sequence,
                event_sequence,
                plan,
                reservation,
            } => {
                if !self.config.contains(&plan.source)
                    || !self.config.contains(&plan.destination)
                    || !self.config.contains(&reservation.source)
                {
                    return Err("Capital journal plan is outside configured membership".into());
                }
                validate_plan_relation(&plan, &reservation, None)?;
                self.replay_event_sequences(journal_sequence, event_sequence)?;
                self.plans.insert(plan.plan_id.clone(), (*plan).clone());
                self.reservations
                    .insert(reservation.reservation_id.clone(), (*reservation).clone());
                self.pending_events.push(CapitalEvent::PlanAuthorized {
                    plan,
                    reservation,
                    event_sequence: self.event_sequence,
                });
            }
            CapitalJournalRecord::PlanStateChanged {
                journal_sequence,
                event_sequence,
                plan,
                reservation,
                operation,
            } => {
                if !self.config.contains(&plan.source)
                    || !self.config.contains(&plan.destination)
                    || !self.config.contains(&reservation.source)
                {
                    return Err(
                        "Capital journal plan state is outside configured membership".into(),
                    );
                }
                validate_plan_relation(&plan, &reservation, Some(&operation))?;
                self.replay_event_sequences(journal_sequence, event_sequence)?;
                self.plans.insert(plan.plan_id.clone(), (*plan).clone());
                self.reservations
                    .insert(reservation.reservation_id.clone(), (*reservation).clone());
                self.operations
                    .insert(operation.operation_id.clone(), (*operation).clone());
                self.pending_events.push(CapitalEvent::PlanStateChanged {
                    plan,
                    reservation,
                    operation,
                    event_sequence: self.event_sequence,
                });
            }
            CapitalJournalRecord::PlanExpired {
                journal_sequence,
                event_sequence,
                plan,
                reservation,
                operation,
            } => {
                if !self.config.contains(&plan.source)
                    || !self.config.contains(&plan.destination)
                    || !self.config.contains(&reservation.source)
                {
                    return Err(
                        "Capital journal expired plan is outside configured membership".into(),
                    );
                }
                validate_plan_relation(&plan, &reservation, operation.as_deref())?;
                self.replay_event_sequences(journal_sequence, event_sequence)?;
                self.plans.insert(plan.plan_id.clone(), (*plan).clone());
                self.reservations
                    .insert(reservation.reservation_id.clone(), (*reservation).clone());
                if let Some(operation) = &operation {
                    self.operations
                        .insert(operation.operation_id.clone(), (**operation).clone());
                }
                self.pending_events.push(CapitalEvent::PlanExpired {
                    plan,
                    reservation,
                    operation,
                    event_sequence: self.event_sequence,
                });
            }
            CapitalJournalRecord::PublicationAcknowledged {
                journal_sequence,
                event_sequence,
            } => {
                let Some(front) = self.pending_events.first() else {
                    return Err("Capital journal acknowledges a missing event".into());
                };
                let front_sequence = match front {
                    CapitalEvent::FundingObjectiveChanged { event_sequence, .. }
                    | CapitalEvent::CapitalDemandChanged { event_sequence, .. }
                    | CapitalEvent::PolicyChanged { event_sequence, .. }
                    | CapitalEvent::FactsObserved { event_sequence, .. }
                    | CapitalEvent::AvailabilityEvaluated { event_sequence, .. } => {
                        event_sequence.get()
                    }
                    CapitalEvent::RouteChanged { event_sequence, .. }
                    | CapitalEvent::PlanAuthorized { event_sequence, .. }
                    | CapitalEvent::PlanStateChanged { event_sequence, .. }
                    | CapitalEvent::PlanExpired { event_sequence, .. } => event_sequence.get(),
                };
                if front_sequence != event_sequence {
                    return Err("Capital journal acknowledges events out of order".into());
                }
                self.journal_sequence = Sequence::new(journal_sequence);
                self.pending_events.remove(0);
            }
        }
        Ok(())
    }

    fn next_journal_sequence(&self) -> Result<u64, ActorError> {
        self.journal_sequence
            .get()
            .checked_add(1)
            .ok_or_else(|| ActorError::State("capital journal sequence overflow".into()))
    }

    fn next_sequences(&self) -> Result<(u64, u64), ActorError> {
        let journal = self.next_journal_sequence()?;
        let event = self
            .event_sequence
            .get()
            .checked_add(1)
            .ok_or_else(|| ActorError::State("capital event sequence overflow".into()))?;
        Ok((journal, event))
    }

    fn replay_event_sequences(
        &mut self,
        journal_sequence: u64,
        event_sequence: u64,
    ) -> Result<(), String> {
        let expected_event = self.event_sequence.get().saturating_add(1);
        if event_sequence != expected_event {
            return Err(format!(
                "Capital event journal is not contiguous: expected {expected_event}, received {event_sequence}"
            ));
        }
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        Ok(())
    }

    fn persist(&mut self, record: &CapitalJournalRecord) -> Result<(), ActorError> {
        if let Some(store) = self.store.as_mut() {
            store.append(record).map_err(ActorError::Persistence)?;
        }
        Ok(())
    }

    fn checkpoint(&self) -> Result<(), ActorError> {
        if let Some(store) = self.store.as_ref() {
            store
                .checkpoint(&self.snapshot())
                .map_err(ActorError::Persistence)?;
        }
        Ok(())
    }
}

fn location_key(location: &FundingLocation) -> (&str, &str, &str) {
    (
        location.account_id.as_str(),
        location.segment.as_str(),
        location.asset.as_str(),
    )
}

fn sorted_values<T: Clone>(values: &HashMap<FundingLocation, T>) -> Vec<T> {
    let mut entries = values.iter().collect::<Vec<_>>();
    entries.sort_by(|(left, _), (right, _)| location_key(left).cmp(&location_key(right)));
    entries
        .into_iter()
        .map(|(_, value)| value.clone())
        .collect()
}

fn sorted_id_values<K: Clone + Ord, T: Clone>(values: &HashMap<K, T>) -> Vec<T> {
    let mut entries = values.iter().collect::<Vec<_>>();
    entries.sort_by_key(|(key, _)| (*key).clone());
    entries
        .into_iter()
        .map(|(_, value)| value.clone())
        .collect()
}

fn validate_plan_relation(
    plan: &CapitalPlan,
    reservation: &CapitalReservation,
    operation: Option<&CapitalOperation>,
) -> Result<(), String> {
    if reservation.reservation_id != plan.reservation_id
        || reservation.plan_id != plan.plan_id
        || reservation.source != plan.source
        || reservation.amount != plan.amount
    {
        return Err("Capital plan and reservation do not match".into());
    }
    if let Some(operation) = operation {
        if operation.plan_id != plan.plan_id || operation.idempotency_key != plan.idempotency_key {
            return Err("Capital plan and operation do not match".into());
        }
    }
    Ok(())
}
