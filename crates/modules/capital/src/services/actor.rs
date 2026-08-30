use std::collections::{BTreeMap, HashMap, HashSet};

use kairos_primitives::account::{AccountId, BrokerId};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::runtime::StrategyDecisionId;
use kairos_primitives::time::{Sequence, UnixNanos};

use crate::domain::{
    CapitalAvailabilityView, CapitalDemandId, CapitalDemandReceipt, CapitalDemandRecord,
    CapitalDemandStatus, CapitalEvent, CapitalFacts, CapitalFundingHorizon, CapitalGroupConfig,
    CapitalGroupId, CapitalMemberAccountObservation, CapitalMemberReadinessRole, CapitalOperation,
    CapitalOperationId, CapitalOperationKind, CapitalOperationStatus,
    CapitalParticipantOperationState, CapitalPlan, CapitalPlanId, CapitalPlanStatus, CapitalPolicy,
    CapitalReadiness, CapitalRecoveryAction, CapitalReservation, CapitalReservationId,
    CapitalReservationStatus, CapitalRouteId, CapitalRouteKind, CapitalSnapshot,
    CapitalSubmissionOutcome, CapitalTransferRoute, CapitalYieldCandidate, FundingLocation,
    FundingObjectiveId, FundingObjectiveReceipt, FundingObjectiveRecord, FundingObjectiveStatus,
    ManualCapitalTransferPreview,
};
use crate::services::input::{
    AuthorizeCapitalPlan, AuthorizeEarnSubscriptionPlan, BeginCapitalOperation,
    CancelFundingObjective, ConfirmManualCapitalTransfer, EvaluateCapitalGroup,
    MarkCapitalDeliveryStarted, ObserveCapitalDemand, ObserveCapitalFacts,
    ObserveCapitalMemberAccount, ObserveCapitalSettlement, PreviewManualCapitalTransfer,
    PublishFundingObjective, RecordCapitalParticipantStatus, RecordCapitalRecoveryRequired,
    RecordCapitalSubmission, UpdateCapitalPolicy, UpdateCapitalRoute,
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
    member_account_observations: HashMap<(BrokerId, AccountId), CapitalMemberAccountObservation>,
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
            member_account_observations: HashMap::new(),
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
            if command.policy == *existing {
                return Ok(());
            }
            if command.policy.version <= existing.version {
                return Err(ActorError::Rejected(
                    "capital policy version must increase".into(),
                ));
            }
        }
        self.persist_and_apply_policy(command.policy, command.updated_at)
    }

    pub(crate) fn observe_facts(&mut self, command: ObserveCapitalFacts) -> Result<(), ActorError> {
        self.validate_group(&command.capital_group_id)?;
        self.validate_location(&command.facts.destination)?;
        let member_observation = CapitalMemberAccountObservation {
            broker: command.facts.destination.broker.clone(),
            account_id: command.facts.destination.account_id.clone(),
            account_watermark: command.facts.account_watermark,
            account_observed_at: command.facts.account_observed_at,
            account_complete: command.facts.account_complete,
        };
        if command.facts.account_watermark.get() == 0
            || command.facts.risk_policy_version.get() == 0
            || command.facts.risk_watermark.get() == 0
        {
            return Err(ActorError::Invalid(
                "Capital facts require non-zero Account/Risk watermarks".into(),
            ));
        }
        if let Some(existing) = self.facts.get(&command.facts.destination) {
            if command.facts == *existing {
                self.apply_member_account_observation(member_observation);
                return Ok(());
            }
            if command.facts.account_watermark < existing.account_watermark
                || command.facts.risk_watermark < existing.risk_watermark
                || command.facts.risk_policy_version < existing.risk_policy_version
            {
                return Err(ActorError::Rejected(
                    "Capital facts cannot move Account/Risk watermarks backwards".into(),
                ));
            }
        }
        self.persist_and_apply_facts(command.facts)?;
        self.apply_member_account_observation(member_observation);
        Ok(())
    }

    pub(crate) fn observe_member_account(
        &mut self,
        command: ObserveCapitalMemberAccount,
    ) -> Result<(), ActorError> {
        self.validate_group(&command.capital_group_id)?;
        if !self.config.members.iter().any(|member| {
            member.broker == command.observation.broker
                && member.account_id == command.observation.account_id
        }) {
            return Err(ActorError::Rejected(
                "Capital Account observation is outside this CapitalGroup".into(),
            ));
        }
        self.apply_member_account_observation(command.observation);
        Ok(())
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
            if command.route == *existing {
                return Ok(());
            }
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
        self.persist_and_apply_route(command.route, command.updated_at)
    }

    pub(crate) fn authorize_plan(
        &mut self,
        command: AuthorizeCapitalPlan,
    ) -> Result<CapitalPlan, ActorError> {
        self.validate_group(&command.capital_group_id)?;
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
        if route.kind == CapitalRouteKind::EarnSubscription {
            return Err(ActorError::Rejected(
                "Earn subscription routes require product preview authorization".into(),
            ));
        }
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
        if view.deficit.is_zero() {
            return Err(ActorError::Rejected(
                "Capital destination has no actionable deficit".into(),
            ));
        }
        let policy = self
            .policies
            .get(&route.destination)
            .ok_or_else(|| ActorError::State("Capital destination policy disappeared".into()))?;
        self.ensure_route_accounts_ready(&route, command.created_at, policy.max_fact_age_nanos)?;
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
        let (source_available, selected_earn_product_id) =
            if route.kind == CapitalRouteKind::EarnRedemptionThenTransfer {
                let holding = source
                    .earn_holdings
                    .iter()
                    .filter(|holding| {
                        holding.active
                            && holding.immediately_redeemable
                            && holding.redeemable_amount.is_positive()
                    })
                    .max_by_key(|holding| holding.redeemable_amount)
                    .ok_or_else(|| {
                        ActorError::Rejected(
                            "Capital Earn route has no immediately redeemable holding".into(),
                        )
                    })?;
                if holding.product_id.trim().is_empty() {
                    return Err(ActorError::State(
                        "Capital Earn holding has no product identity".into(),
                    ));
                }
                let available = if holding.redeemable_amount > reserved {
                    holding
                        .redeemable_amount
                        .checked_sub(reserved)
                        .map_err(|error| ActorError::State(error.to_string()))?
                } else {
                    Quantity::ZERO
                };
                (available, Some(holding.product_id.clone()))
            } else {
                let available = if source.observed_available > reserved {
                    source
                        .observed_available
                        .checked_sub(reserved)
                        .map_err(|error| ActorError::State(error.to_string()))?
                } else {
                    Quantity::ZERO
                };
                (available, None)
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
                .map_err(|error| ActorError::Invalid(error.to_string()))?;
        let first_operation_kind = if route.kind == CapitalRouteKind::EarnRedemptionThenTransfer {
            "earn-redemption"
        } else {
            "transfer"
        };
        let idempotency_key = kairos_primitives::runtime::IdempotencyKey::new(format!(
            "{}:0:{first_operation_kind}",
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
            route_kind: route.kind,
            source: route.source,
            destination: route.destination,
            amount,
            objective_ids: view.active_objective_ids,
            demand_ids: view.active_demand_ids,
            reservation_id,
            idempotency_key,
            selected_earn_product_id,
            source_account_watermark: source.account_watermark,
            destination_account_watermark: view.account_watermark,
            source_observed_available: source.observed_available,
            destination_observed_available: view.observed_available,
            redemption_account_watermark: None,
            redemption_observed_available: None,
            earn_principal_before: Quantity::ZERO,
            status: CapitalPlanStatus::Authorized,
            recovery_action: CapitalRecoveryAction::None,
            recovery_reason: None,
            recovery_decided_at: None,
            created_at: command.created_at,
            expires_at: command.expires_at,
        };
        self.persist_and_apply_plan(plan.clone(), reservation)?;
        Ok(plan)
    }

    pub(crate) fn preview_manual_transfer(
        &self,
        command: PreviewManualCapitalTransfer,
    ) -> Result<ManualCapitalTransferPreview, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        if command.preview_id.is_empty() || command.preview_id.trim() != command.preview_id {
            return Err(ActorError::Invalid(
                "manual transfer preview_id is required".into(),
            ));
        }
        if !command.amount.is_positive() {
            return Err(ActorError::Invalid(
                "manual transfer amount must be positive".into(),
            ));
        }
        if command.expires_at <= command.created_at {
            return Err(ActorError::Invalid(
                "manual transfer preview expiry must follow creation".into(),
            ));
        }
        if command.source == command.destination {
            return Err(ActorError::Invalid(
                "manual transfer source and destination must differ".into(),
            ));
        }
        if command.source.asset != command.destination.asset {
            return Err(ActorError::Invalid(
                "manual transfer cannot change assets".into(),
            ));
        }
        let mut routes = self.routes.values().filter(|route| {
            route.enabled
                && matches!(
                    route.kind,
                    CapitalRouteKind::InternalTransfer | CapitalRouteKind::AccountTransfer
                )
                && route.source == command.source
                && route.destination == command.destination
        });
        let route = routes
            .next()
            .cloned()
            .ok_or_else(|| ActorError::Rejected("manual transfer route was not found".into()))?;
        if routes.next().is_some() {
            return Err(ActorError::Rejected(
                "manual transfer route is ambiguous".into(),
            ));
        }
        if route.required_source_authority != command.source_authority {
            return Err(ActorError::Rejected(
                "manual transfer source authority does not match the route".into(),
            ));
        }
        let policy = self
            .policies
            .get(&route.destination)
            .ok_or_else(|| ActorError::Rejected("manual transfer policy was not found".into()))?;
        self.ensure_route_accounts_ready(&route, command.created_at, policy.max_fact_age_nanos)?;
        let source = self.facts.get(&route.source).ok_or_else(|| {
            ActorError::Rejected("manual transfer source facts are unavailable".into())
        })?;
        let destination = self.facts.get(&route.destination).ok_or_else(|| {
            ActorError::Rejected("manual transfer destination facts are unavailable".into())
        })?;
        for facts in [source, destination] {
            if !facts.account_complete || command.created_at < facts.account_observed_at {
                return Err(ActorError::Rejected(
                    "manual transfer Account facts are incomplete or from the future".into(),
                ));
            }
            if command.created_at.get() - facts.account_observed_at.get()
                > policy.max_fact_age_nanos
            {
                return Err(ActorError::Rejected(
                    "manual transfer Account facts are stale".into(),
                ));
            }
        }
        if command.amount < policy.minimum_movement {
            return Err(ActorError::Rejected(
                "manual transfer amount is below the route minimum movement".into(),
            ));
        }
        if command.amount > route.per_operation_limit {
            return Err(ActorError::Rejected(
                "manual transfer amount exceeds the per-operation limit".into(),
            ));
        }
        let daily_used = self.daily_used(&route, command.created_at)?;
        let daily_remaining = if route.daily_limit > daily_used {
            route
                .daily_limit
                .checked_sub(daily_used)
                .map_err(|error| ActorError::State(error.to_string()))?
        } else {
            Quantity::ZERO
        };
        if command.amount > daily_remaining {
            return Err(ActorError::Rejected(
                "manual transfer amount exceeds the remaining daily limit".into(),
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
        if command.amount > source_available {
            return Err(ActorError::Rejected(
                "manual transfer amount exceeds unreserved source availability".into(),
            ));
        }
        Ok(ManualCapitalTransferPreview {
            preview_id: command.preview_id,
            plan_id: command.plan_id,
            idempotency_key: command.idempotency_key,
            route_id: route.route_id,
            route_version: route.version,
            route_kind: route.kind,
            source: route.source,
            destination: route.destination,
            amount: command.amount,
            source_authority: command.source_authority,
            source_account_watermark: source.account_watermark,
            destination_account_watermark: destination.account_watermark,
            source_observed_available: source.observed_available,
            destination_observed_available: destination.observed_available,
            created_at: command.created_at,
            expires_at: command.expires_at,
        })
    }

    pub(crate) fn confirm_manual_transfer(
        &mut self,
        command: ConfirmManualCapitalTransfer,
    ) -> Result<CapitalPlan, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        let preview = command.preview;
        if let Some(existing) = self.plans.get(&preview.plan_id) {
            if existing.rebalance_decision_id.as_str()
                == format!("manual-transfer:{}", preview.preview_id)
                && existing.route_id == preview.route_id
                && existing.amount == preview.amount
                && existing.idempotency_key == preview.idempotency_key
            {
                return Ok(existing.clone());
            }
            return Err(ActorError::Rejected(
                "manual transfer idempotency key already has different state".into(),
            ));
        }
        if command.confirmed_at < preview.created_at || command.confirmed_at >= preview.expires_at {
            return Err(ActorError::Rejected(
                "manual transfer preview has expired".into(),
            ));
        }
        let current = self.preview_manual_transfer(PreviewManualCapitalTransfer {
            capital_group_id: command.capital_group_id,
            preview_id: preview.preview_id.clone(),
            plan_id: preview.plan_id.clone(),
            idempotency_key: preview.idempotency_key.clone(),
            source: preview.source.clone(),
            destination: preview.destination.clone(),
            amount: preview.amount,
            source_authority: preview.source_authority.clone(),
            created_at: command.confirmed_at,
            expires_at: preview.expires_at,
        })?;
        if current.route_id != preview.route_id
            || current.route_version != preview.route_version
            || current.route_kind != preview.route_kind
            || current.source_account_watermark != preview.source_account_watermark
            || current.destination_account_watermark != preview.destination_account_watermark
            || current.source_observed_available != preview.source_observed_available
            || current.destination_observed_available != preview.destination_observed_available
        {
            return Err(ActorError::Rejected(
                "manual transfer preview is stale; request a new preview".into(),
            ));
        }
        let reservation_id =
            CapitalReservationId::new(format!("capital-reservation:{}", preview.plan_id.as_str()))
                .map_err(|error| ActorError::Invalid(error.to_string()))?;
        let reservation = CapitalReservation {
            reservation_id: reservation_id.clone(),
            plan_id: preview.plan_id.clone(),
            source: preview.source.clone(),
            amount: preview.amount,
            source_account_watermark: preview.source_account_watermark,
            status: CapitalReservationStatus::Active,
            created_at: command.confirmed_at,
            expires_at: preview.expires_at,
        };
        let plan = CapitalPlan {
            plan_id: preview.plan_id,
            rebalance_decision_id: StrategyDecisionId::new(format!(
                "manual-transfer:{}",
                preview.preview_id
            ))
            .map_err(|error| ActorError::Invalid(error.to_string()))?,
            route_id: preview.route_id,
            route_version: preview.route_version,
            route_kind: preview.route_kind,
            source: preview.source,
            destination: preview.destination,
            amount: preview.amount,
            objective_ids: Vec::new(),
            demand_ids: Vec::new(),
            reservation_id,
            idempotency_key: preview.idempotency_key,
            selected_earn_product_id: None,
            source_account_watermark: preview.source_account_watermark,
            destination_account_watermark: preview.destination_account_watermark,
            source_observed_available: preview.source_observed_available,
            destination_observed_available: preview.destination_observed_available,
            redemption_account_watermark: None,
            redemption_observed_available: None,
            earn_principal_before: Quantity::ZERO,
            status: CapitalPlanStatus::Authorized,
            recovery_action: CapitalRecoveryAction::None,
            recovery_reason: None,
            recovery_decided_at: None,
            created_at: command.confirmed_at,
            expires_at: preview.expires_at,
        };
        self.persist_and_apply_plan(plan.clone(), reservation)?;
        Ok(plan)
    }

    pub(crate) fn yield_candidate(
        &self,
        route_id: &CapitalRouteId,
        evaluated_at: UnixNanos,
    ) -> Result<Option<CapitalYieldCandidate>, ActorError> {
        let route = self
            .routes
            .get(route_id)
            .ok_or_else(|| ActorError::Rejected("Capital route was not found".into()))?;
        if !route.enabled || route.kind != CapitalRouteKind::EarnSubscription {
            return Ok(None);
        }
        if self.plans.values().any(|plan| {
            plan.route_id == *route_id
                && !matches!(
                    plan.status,
                    CapitalPlanStatus::Completed
                        | CapitalPlanStatus::Rejected
                        | CapitalPlanStatus::Expired
                        | CapitalPlanStatus::Failed
                )
        }) {
            return Ok(None);
        }
        let view = self
            .availability
            .get(&route.source)
            .ok_or_else(|| ActorError::Rejected("Capital source was not evaluated".into()))?;
        if !view.deficit.is_zero() {
            return Ok(None);
        }
        let policy = self
            .policies
            .get(&route.source)
            .ok_or_else(|| ActorError::State("Capital source policy disappeared".into()))?;
        if self
            .ensure_route_accounts_ready(route, evaluated_at, policy.max_fact_age_nanos)
            .is_err()
        {
            return Ok(None);
        }
        let facts = self
            .facts
            .get(&route.source)
            .ok_or_else(|| ActorError::Rejected("Capital source facts are unavailable".into()))?;
        if !facts.account_complete || evaluated_at < facts.account_observed_at {
            return Ok(None);
        }
        if evaluated_at.get() - facts.account_observed_at.get() > policy.max_fact_age_nanos {
            return Ok(None);
        }
        let demand_guard_until = evaluated_at.get().saturating_add(route.demand_guard_nanos);
        if view
            .funding_horizons
            .iter()
            .any(|horizon| horizon.required_by.get() <= demand_guard_until)
        {
            return Ok(None);
        }
        let reserved = self.active_reserved_at(&route.source)?;
        let protected = view
            .effective_target
            .checked_add(reserved)
            .map_err(|error| ActorError::State(error.to_string()))?;
        let surplus = if facts.observed_available > protected {
            facts
                .observed_available
                .checked_sub(protected)
                .map_err(|error| ActorError::State(error.to_string()))?
        } else {
            Quantity::ZERO
        };
        let daily_used = self.daily_used(route, evaluated_at)?;
        let daily_remaining = if route.daily_limit > daily_used {
            route
                .daily_limit
                .checked_sub(daily_used)
                .map_err(|error| ActorError::State(error.to_string()))?
        } else {
            Quantity::ZERO
        };
        let amount = surplus.min(route.per_operation_limit).min(daily_remaining);
        if amount.is_zero() || amount < policy.minimum_movement {
            return Ok(None);
        }
        Ok(Some(CapitalYieldCandidate {
            route_id: route.route_id.clone(),
            product_id: route
                .earn_product_id
                .clone()
                .ok_or_else(|| ActorError::State("Earn route lost its product id".into()))?,
            amount,
            account_watermark: facts.account_watermark,
            risk_watermark: facts.risk_watermark,
        }))
    }

    pub(crate) fn authorize_earn_subscription(
        &mut self,
        command: AuthorizeEarnSubscriptionPlan,
    ) -> Result<CapitalPlan, ActorError> {
        self.validate_group(&command.capital_group_id)?;
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
        if route.required_source_authority != command.source_authority {
            return Err(ActorError::Rejected(
                "Capital source authority does not match the route".into(),
            ));
        }
        if !command.eligible || !command.immediately_redeemable {
            return Err(ActorError::Rejected(
                "Earn product is not eligible and immediately redeemable".into(),
            ));
        }
        let policy = self
            .policies
            .get(&route.source)
            .ok_or_else(|| ActorError::State("Capital source policy disappeared".into()))?;
        if command.preview_observed_at > command.created_at
            || command.created_at.get() - command.preview_observed_at.get()
                > policy.max_fact_age_nanos
        {
            return Err(ActorError::Rejected("Earn product preview is stale".into()));
        }
        let candidate = self
            .yield_candidate(&command.route_id, command.created_at)?
            .ok_or_else(|| {
                ActorError::Rejected("Capital location has no deployable surplus".into())
            })?;
        if candidate.amount != command.previewed_amount {
            return Err(ActorError::Rejected(
                "Earn preview amount no longer matches deployable surplus".into(),
            ));
        }
        match command.redemption_quota_remaining {
            Some(quota) if quota < candidate.amount => {
                return Err(ActorError::Rejected(
                    "Earn redemption quota is below the subscription amount".into(),
                ));
            },
            None if !route.allow_unknown_redemption_quota => {
                return Err(ActorError::Rejected(
                    "Earn redemption quota is unknown".into(),
                ));
            },
            _ => {},
        }
        let facts = self
            .facts
            .get(&route.source)
            .ok_or_else(|| ActorError::State("Capital source facts disappeared".into()))?;
        let earn_principal_before = facts
            .earn_holdings
            .iter()
            .filter(|holding| holding.product_id == candidate.product_id && holding.active)
            .try_fold(Quantity::ZERO, |total, holding| {
                total
                    .checked_add(holding.principal)
                    .map_err(|error| ActorError::State(error.to_string()))
            })?;
        let reservation_id =
            CapitalReservationId::new(format!("capital-reservation:{}", command.plan_id.as_str()))
                .map_err(|error| ActorError::Invalid(error.to_string()))?;
        let idempotency_key = kairos_primitives::runtime::IdempotencyKey::new(format!(
            "{}:0:earn-subscription",
            command.plan_id.as_str()
        ))
        .map_err(|error| ActorError::Invalid(error.to_string()))?;
        let reservation = CapitalReservation {
            reservation_id: reservation_id.clone(),
            plan_id: command.plan_id.clone(),
            source: route.source.clone(),
            amount: candidate.amount,
            source_account_watermark: facts.account_watermark,
            status: CapitalReservationStatus::Active,
            created_at: command.created_at,
            expires_at: command.expires_at,
        };
        let view = self
            .availability
            .get(&route.source)
            .ok_or_else(|| ActorError::State("Capital source availability disappeared".into()))?;
        let plan = CapitalPlan {
            plan_id: command.plan_id,
            rebalance_decision_id: command.rebalance_decision_id,
            route_id: route.route_id,
            route_version: route.version,
            route_kind: route.kind,
            source: route.source.clone(),
            destination: route.destination,
            amount: candidate.amount,
            objective_ids: view.active_objective_ids.clone(),
            demand_ids: view.active_demand_ids.clone(),
            reservation_id,
            idempotency_key,
            selected_earn_product_id: Some(candidate.product_id),
            source_account_watermark: facts.account_watermark,
            destination_account_watermark: facts.account_watermark,
            source_observed_available: facts.observed_available,
            destination_observed_available: facts.observed_available,
            redemption_account_watermark: None,
            redemption_observed_available: None,
            earn_principal_before,
            status: CapitalPlanStatus::Authorized,
            recovery_action: CapitalRecoveryAction::None,
            recovery_reason: None,
            recovery_decided_at: None,
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
                        CapitalPlanStatus::Authorized
                            | CapitalPlanStatus::Redeeming
                            | CapitalPlanStatus::Subscribing
                            | CapitalPlanStatus::Available
                            | CapitalPlanStatus::Transferring
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
            plan.recovery_action = CapitalRecoveryAction::NoCompensationRequired;
            plan.recovery_reason = Some("plan expired before participant delivery".into());
            plan.recovery_decided_at = Some(observed_at);
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
            if operation.status != CapitalOperationStatus::Settled
                || plan.route_kind != CapitalRouteKind::EarnRedemptionThenTransfer
                || plan.status != CapitalPlanStatus::Available
            {
                return Ok(operation.clone());
            }
        }
        let (operation_index, kind) = match (plan.route_kind, plan.status) {
            (CapitalRouteKind::EarnRedemptionThenTransfer, CapitalPlanStatus::Authorized) => {
                (0, CapitalOperationKind::EarnRedemption)
            },
            (CapitalRouteKind::EarnRedemptionThenTransfer, CapitalPlanStatus::Available) => {
                (1, CapitalOperationKind::Transfer)
            },
            (CapitalRouteKind::EarnSubscription, CapitalPlanStatus::Authorized) => {
                (0, CapitalOperationKind::EarnSubscription)
            },
            (_, CapitalPlanStatus::Authorized) => (0, CapitalOperationKind::Transfer),
            _ => {
                return Err(ActorError::Rejected(
                    "Capital plan cannot begin its next operation".into(),
                ));
            },
        };
        if plan.status != CapitalPlanStatus::Authorized
            && plan.status != CapitalPlanStatus::Available
        {
            return Err(ActorError::Rejected(
                "only an Authorized or redemption-Available Capital plan can begin".into(),
            ));
        }
        if command.at >= plan.expires_at {
            return Err(ActorError::Rejected("Capital plan is expired".into()));
        }
        let kind_name = match kind {
            CapitalOperationKind::EarnRedemption => "earn-redemption",
            CapitalOperationKind::EarnSubscription => "earn-subscription",
            CapitalOperationKind::Transfer => "transfer",
        };
        let operation_id = CapitalOperationId::new(format!(
            "capital-operation:{}:{operation_index}:{kind_name}",
            plan.plan_id.as_str()
        ))
        .map_err(|error| ActorError::Invalid(error.to_string()))?;
        let idempotency_key = kairos_primitives::runtime::IdempotencyKey::new(format!(
            "{}:{operation_index}:{kind_name}",
            plan.plan_id.as_str()
        ))
        .map_err(|error| ActorError::Invalid(error.to_string()))?;
        let operation = CapitalOperation {
            operation_id,
            plan_id: plan.plan_id.clone(),
            idempotency_key,
            operation_index,
            kind,
            status: CapitalOperationStatus::Prepared,
            participant_operation_id: None,
            participant_state: None,
            dispatch_started_at: None,
            attempt_count: 1,
            failure_reason: None,
            account_observation_watermark: None,
            updated_at: command.at,
        };
        plan.status = match kind {
            CapitalOperationKind::EarnRedemption => CapitalPlanStatus::Redeeming,
            CapitalOperationKind::EarnSubscription => CapitalPlanStatus::Subscribing,
            CapitalOperationKind::Transfer => CapitalPlanStatus::Transferring,
        };
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
                plan.status = match operation.kind {
                    CapitalOperationKind::EarnRedemption => CapitalPlanStatus::AwaitingRedemption,
                    CapitalOperationKind::EarnSubscription => {
                        CapitalPlanStatus::AwaitingSubscription
                    },
                    CapitalOperationKind::Transfer => CapitalPlanStatus::AwaitingTransfer,
                };
                operation.status = CapitalOperationStatus::AwaitingParticipant;
            },
            CapitalSubmissionOutcome::Rejected => {
                plan.status = CapitalPlanStatus::Rejected;
                plan.recovery_action = CapitalRecoveryAction::NoCompensationRequired;
                plan.recovery_reason = Some(
                    operation
                        .failure_reason
                        .clone()
                        .unwrap_or_else(|| "participant command was definitely rejected".into()),
                );
                plan.recovery_decided_at = Some(command.at);
                operation.status = CapitalOperationStatus::Rejected;
                reservation.status = CapitalReservationStatus::Released;
            },
            CapitalSubmissionOutcome::Indeterminate => {
                plan.status = CapitalPlanStatus::Indeterminate;
                plan.recovery_action = CapitalRecoveryAction::ReconcileOriginalOperation;
                plan.recovery_reason = Some(
                    operation
                        .failure_reason
                        .clone()
                        .unwrap_or_else(|| "participant delivery is indeterminate".into()),
                );
                plan.recovery_decided_at = Some(command.at);
                operation.status = CapitalOperationStatus::Indeterminate;
            },
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
                plan.status = match operation.kind {
                    CapitalOperationKind::EarnRedemption => CapitalPlanStatus::AwaitingRedemption,
                    CapitalOperationKind::EarnSubscription => {
                        CapitalPlanStatus::AwaitingSubscription
                    },
                    CapitalOperationKind::Transfer => CapitalPlanStatus::AwaitingTransfer,
                };
                operation.status = CapitalOperationStatus::AwaitingParticipant;
            },
            CapitalParticipantOperationState::Succeeded => {
                plan.status = CapitalPlanStatus::Reconciling;
                operation.status = CapitalOperationStatus::AwaitingAccountObservation;
            },
            CapitalParticipantOperationState::Failed
            | CapitalParticipantOperationState::Cancelled => {
                plan.status = CapitalPlanStatus::Failed;
                plan.recovery_action = CapitalRecoveryAction::HoldAndReview;
                plan.recovery_reason =
                    Some(operation.failure_reason.clone().unwrap_or_else(|| {
                        "participant reported terminal failure; automatic compensation is unsafe"
                            .into()
                    }));
                plan.recovery_decided_at = Some(command.at);
                operation.status = CapitalOperationStatus::Failed;
                reservation.status = CapitalReservationStatus::Released;
            },
            CapitalParticipantOperationState::Unknown => {
                plan.status = CapitalPlanStatus::Indeterminate;
                plan.recovery_action = CapitalRecoveryAction::ReconcileOriginalOperation;
                plan.recovery_reason = Some(
                    operation
                        .failure_reason
                        .clone()
                        .unwrap_or_else(|| "participant operation state is unknown".into()),
                );
                plan.recovery_decided_at = Some(command.at);
                operation.status = CapitalOperationStatus::Indeterminate;
            },
        }
        self.persist_and_apply_plan_state(plan.clone(), reservation, operation)?;
        Ok(plan)
    }

    pub(crate) fn record_recovery_required(
        &mut self,
        command: RecordCapitalRecoveryRequired,
    ) -> Result<CapitalPlan, ActorError> {
        self.validate_group(&command.capital_group_id)?;
        if command.reason.trim().is_empty() {
            return Err(ActorError::Invalid(
                "Capital recovery reason must be non-empty".into(),
            ));
        }
        let mut plan = self.plan(&command.plan_id)?;
        let reservation = self.reservation(&plan.reservation_id)?;
        let mut operation = self
            .operation_for_plan(&plan.plan_id)
            .cloned()
            .ok_or_else(|| ActorError::Rejected("Capital operation was not prepared".into()))?;
        if operation.status == CapitalOperationStatus::Prepared {
            return Err(ActorError::Rejected(
                "an undelivered Capital operation does not require participant reconciliation"
                    .into(),
            ));
        }
        if matches!(
            operation.status,
            CapitalOperationStatus::Settled
                | CapitalOperationStatus::Expired
                | CapitalOperationStatus::Rejected
                | CapitalOperationStatus::Failed
        ) {
            return Ok(plan);
        }
        if command.at < operation.updated_at {
            return Err(ActorError::Invalid(
                "Capital recovery decision time cannot move backwards".into(),
            ));
        }
        if plan.recovery_action == CapitalRecoveryAction::HoldAndReview {
            return Ok(plan);
        }
        plan.recovery_action = CapitalRecoveryAction::ReconcileOriginalOperation;
        plan.recovery_reason = Some(command.reason);
        plan.recovery_decided_at = plan.recovery_decided_at.or(Some(command.at));
        operation.updated_at = command.at;
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
        if operation.kind == CapitalOperationKind::EarnRedemption {
            let source_expected = plan
                .source_observed_available
                .checked_add(plan.amount)
                .map_err(|error| ActorError::State(error.to_string()))?;
            let redeemed = command.source.account_watermark > plan.source_account_watermark
                && command.source.observed_available >= source_expected;
            if !redeemed {
                return Ok(plan);
            }
            plan.status = CapitalPlanStatus::Available;
            plan.recovery_action = CapitalRecoveryAction::None;
            plan.recovery_reason = None;
            plan.recovery_decided_at = None;
            plan.redemption_account_watermark = Some(command.source.account_watermark);
            plan.redemption_observed_available = Some(command.source.observed_available);
            operation.status = CapitalOperationStatus::Settled;
            operation.account_observation_watermark = Some(command.source.account_watermark);
            operation.updated_at = command.observed_at;
            self.persist_and_apply_plan_state(plan.clone(), reservation, operation)?;
            return Ok(plan);
        }
        if operation.kind == CapitalOperationKind::EarnSubscription {
            let source_expected = plan
                .source_observed_available
                .checked_sub(plan.amount)
                .map_err(|error| ActorError::State(error.to_string()))?;
            let principal = command
                .source
                .earn_holdings
                .iter()
                .filter(|holding| {
                    plan.selected_earn_product_id
                        .as_deref()
                        .is_some_and(|product_id| holding.product_id == product_id)
                        && holding.active
                })
                .try_fold(Quantity::ZERO, |total, holding| {
                    total
                        .checked_add(holding.principal)
                        .map_err(|error| ActorError::State(error.to_string()))
                })?;
            let principal_expected = plan
                .earn_principal_before
                .checked_add(plan.amount)
                .map_err(|error| ActorError::State(error.to_string()))?;
            let settled = command.source.account_watermark > plan.source_account_watermark
                && command.source.observed_available <= source_expected
                && principal >= principal_expected;
            if !settled {
                return Ok(plan);
            }
            plan.status = CapitalPlanStatus::Completed;
            plan.recovery_action = CapitalRecoveryAction::None;
            plan.recovery_reason = None;
            plan.recovery_decided_at = None;
            reservation.status = CapitalReservationStatus::Consumed;
            operation.status = CapitalOperationStatus::Settled;
            operation.account_observation_watermark = Some(command.source.account_watermark);
            operation.updated_at = command.observed_at;
            self.persist_and_apply_plan_state(plan.clone(), reservation, operation)?;
            return Ok(plan);
        }
        let source_baseline = plan
            .redemption_observed_available
            .unwrap_or(plan.source_observed_available);
        let source_watermark = plan
            .redemption_account_watermark
            .unwrap_or(plan.source_account_watermark);
        let source_expected = source_baseline
            .checked_sub(plan.amount)
            .map_err(|error| ActorError::State(error.to_string()))?;
        let destination_expected = plan
            .destination_observed_available
            .checked_add(plan.amount)
            .map_err(|error| ActorError::State(error.to_string()))?;
        let settled = command.source.account_watermark > source_watermark
            && command.destination.account_watermark > plan.destination_account_watermark
            && command.source.observed_available <= source_expected
            && command.destination.observed_available >= destination_expected;
        if !settled {
            return Ok(plan);
        }
        plan.status = CapitalPlanStatus::Completed;
        plan.recovery_action = CapitalRecoveryAction::None;
        plan.recovery_reason = None;
        plan.recovery_decided_at = None;
        reservation.status = CapitalReservationStatus::Consumed;
        operation.status = CapitalOperationStatus::Settled;
        operation.account_observation_watermark = Some(command.destination.account_watermark);
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

    fn apply_member_account_observation(&mut self, value: CapitalMemberAccountObservation) {
        let key = (value.broker.clone(), value.account_id.clone());
        if self
            .member_account_observations
            .get(&key)
            .is_some_and(|existing| {
                value.account_observed_at < existing.account_observed_at
                    || (value.account_observed_at == existing.account_observed_at
                        && value.account_watermark < existing.account_watermark)
            })
        {
            return;
        }
        self.member_account_observations.insert(key, value);
    }

    fn location_facts_ready(
        &self,
        location: &FundingLocation,
        at: UnixNanos,
        max_fact_age_nanos: u64,
    ) -> bool {
        self.facts.get(location).is_some_and(|facts| {
            facts.account_complete
                && at >= facts.account_observed_at
                && at.get().saturating_sub(facts.account_observed_at.get()) <= max_fact_age_nanos
        })
    }

    /// Returns the group-visible readiness and whether the condition closes
    /// the global write barrier. Optional member loss degrades observability
    /// but does not stop routes whose own endpoints remain ready.
    fn group_account_readiness(
        &self,
        at: UnixNanos,
        max_fact_age_nanos: u64,
    ) -> (CapitalReadiness, Option<String>, bool) {
        let mut optional_unavailable = Vec::new();
        for member in &self.config.members {
            let observation = self
                .member_account_observations
                .get(&(member.broker.clone(), member.account_id.clone()));
            let ready = observation.is_some_and(|observation| {
                observation.account_complete
                    && observation.account_watermark.get() > 0
                    && at >= observation.account_observed_at
                    && at
                        .get()
                        .saturating_sub(observation.account_observed_at.get())
                        <= max_fact_age_nanos
            });
            if ready {
                continue;
            }
            match member.readiness_role {
                CapitalMemberReadinessRole::Critical => {
                    return (
                        if observation.is_none() {
                            CapitalReadiness::WaitingForAccounts
                        } else {
                            CapitalReadiness::Degraded
                        },
                        Some(format!(
                            "critical Capital Account '{}' is incomplete, stale, or unavailable",
                            member.account_id
                        )),
                        true,
                    );
                },
                CapitalMemberReadinessRole::Optional => {
                    optional_unavailable.push(member.account_id.to_string());
                },
            }
        }
        if optional_unavailable.is_empty() {
            (CapitalReadiness::Ready, None, false)
        } else {
            optional_unavailable.sort();
            (
                CapitalReadiness::Degraded,
                Some(format!(
                    "optional Capital Accounts are incomplete, stale, or unavailable: {}",
                    optional_unavailable.join(", ")
                )),
                false,
            )
        }
    }

    fn ensure_route_accounts_ready(
        &self,
        route: &CapitalTransferRoute,
        at: UnixNanos,
        max_fact_age_nanos: u64,
    ) -> Result<(), ActorError> {
        let (_, reason, globally_blocked) = self.group_account_readiness(at, max_fact_age_nanos);
        if globally_blocked {
            return Err(ActorError::Rejected(reason.unwrap_or_else(|| {
                "critical Capital Account is unavailable".into()
            })));
        }
        for (name, location) in [
            ("source", &route.source),
            ("destination", &route.destination),
        ] {
            if !self.location_facts_ready(location, at, max_fact_age_nanos) {
                return Err(ActorError::Rejected(format!(
                    "Capital route {name} Account facts are incomplete, stale, or unavailable"
                )));
            }
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

    fn persist_and_apply_policy(
        &mut self,
        policy: CapitalPolicy,
        occurred_at: UnixNanos,
    ) -> Result<(), ActorError> {
        let (journal_sequence, event_sequence) = self.next_sequences()?;
        self.persist(&CapitalJournalRecord::PolicyChanged {
            journal_sequence,
            event_sequence,
            policy: Box::new(policy.clone()),
            occurred_at,
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        self.policies
            .insert(policy.destination.clone(), policy.clone());
        self.pending_events.push(CapitalEvent::PolicyChanged {
            policy,
            event_sequence: self.event_sequence,
            occurred_at,
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

    fn persist_and_apply_route(
        &mut self,
        route: CapitalTransferRoute,
        occurred_at: UnixNanos,
    ) -> Result<(), ActorError> {
        let (journal_sequence, event_sequence) = self.next_sequences()?;
        self.persist(&CapitalJournalRecord::RouteChanged {
            journal_sequence,
            event_sequence,
            route: Box::new(route.clone()),
            occurred_at,
        })?;
        self.journal_sequence = Sequence::new(journal_sequence);
        self.event_sequence = Sequence::new(event_sequence);
        self.routes.insert(route.route_id.clone(), route.clone());
        self.pending_events.push(CapitalEvent::RouteChanged {
            route,
            event_sequence: self.event_sequence,
            occurred_at,
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
            .filter(|operation| operation.plan_id == *plan_id)
            .max_by_key(|operation| operation.operation_index)
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
        let observed_available = self
            .facts
            .get(&policy.destination)
            .map(|facts| facts.observed_available)
            .unwrap_or(Quantity::ZERO);
        let mut horizon_rows: BTreeMap<UnixNanos, CapitalFundingHorizon> = BTreeMap::new();
        for record in &active {
            let row = horizon_rows
                .entry(record.objective.required_by)
                .or_insert_with(|| CapitalFundingHorizon {
                    required_by: record.objective.required_by,
                    objective_ids: Vec::new(),
                    demand_ids: Vec::new(),
                    desired_available: Quantity::ZERO,
                });
            row.objective_ids
                .push(record.objective.objective_id.clone());
            row.desired_available = row
                .desired_available
                .max(record.objective.desired_available);
        }
        for record in &active_demands {
            let row = horizon_rows
                .entry(record.demand.required_by)
                .or_insert_with(|| CapitalFundingHorizon {
                    required_by: record.demand.required_by,
                    objective_ids: Vec::new(),
                    demand_ids: Vec::new(),
                    desired_available: Quantity::ZERO,
                });
            row.demand_ids.push(record.demand.demand_id.clone());
            let demand_target = observed_available
                .checked_add(record.demand.observed_shortfall)
                .map_err(|error| ActorError::Invalid(error.to_string()))?;
            row.desired_available = row.desired_available.max(demand_target);
        }
        let funding_horizons = horizon_rows
            .into_values()
            .map(|mut row| {
                row.objective_ids.sort();
                row.demand_ids.sort();
                row
            })
            .collect::<Vec<_>>();

        let Some(facts) = self.facts.get(&policy.destination) else {
            return Ok(CapitalAvailabilityView {
                destination: policy.destination.clone(),
                readiness: CapitalReadiness::WaitingForFacts,
                policy_version: policy.version,
                active_objective_ids: objective_ids,
                active_demand_ids: demand_ids,
                funding_horizons,
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
        let demand_target = if active_demands.is_empty() {
            Quantity::ZERO
        } else {
            facts
                .observed_available
                .checked_add(demand_shortfall)
                .map_err(|error| ActorError::Invalid(error.to_string()))?
        };
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
        let local_readiness_reason = if !facts.account_complete {
            Some("Account facts are incomplete".to_string())
        } else if fact_age > policy.max_fact_age_nanos {
            Some("Account facts are stale".to_string())
        } else if risk_below_minimum {
            Some("Risk capacity is below the Capital policy minimum".to_string())
        } else {
            None
        };
        let (group_readiness, group_readiness_reason, group_writes_blocked) =
            self.group_account_readiness(evaluated_at, policy.max_fact_age_nanos);
        let readiness_reason = local_readiness_reason.clone().or(group_readiness_reason);
        let readiness = if local_readiness_reason.is_some() {
            CapitalReadiness::Degraded
        } else {
            group_readiness
        };
        let writes_blocked = local_readiness_reason.is_some() || group_writes_blocked;
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
            .map(|plan| UnixNanos::new(plan.created_at.get().saturating_add(policy.cooldown_nanos)))
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
            || writes_blocked
        {
            deficit = Quantity::ZERO;
        }
        Ok(CapitalAvailabilityView {
            destination: policy.destination.clone(),
            readiness,
            policy_version: policy.version,
            active_objective_ids: objective_ids,
            active_demand_ids: demand_ids,
            funding_horizons,
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
            validate_plan_relation(plan, reservation)?;
        }
        let mut operation_ids = HashSet::new();
        let mut operation_indexes = HashSet::new();
        for operation in &snapshot.operations {
            if !operation_ids.insert(operation.operation_id.clone()) {
                return Err("Capital snapshot contains duplicate operation IDs".into());
            }
            let plan = snapshot
                .plans
                .iter()
                .find(|plan| plan.plan_id == operation.plan_id)
                .ok_or_else(|| "Capital snapshot operation has no plan".to_string())?;
            if !operation_indexes.insert((operation.plan_id.clone(), operation.operation_index)) {
                return Err("Capital snapshot contains duplicate operation indexes".into());
            }
            validate_operation_relation(plan, operation)?;
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
            },
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
            },
            CapitalJournalRecord::PolicyChanged {
                journal_sequence,
                event_sequence,
                policy,
                occurred_at,
            } => {
                self.replay_event_sequences(journal_sequence, event_sequence)?;
                self.policies
                    .insert(policy.destination.clone(), (*policy).clone());
                self.pending_events.push(CapitalEvent::PolicyChanged {
                    policy: *policy,
                    event_sequence: self.event_sequence,
                    occurred_at,
                });
            },
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
            },
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
            },
            CapitalJournalRecord::RouteChanged {
                journal_sequence,
                event_sequence,
                route,
                occurred_at,
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
                    occurred_at,
                });
            },
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
                validate_plan_relation(&plan, &reservation)?;
                self.replay_event_sequences(journal_sequence, event_sequence)?;
                self.plans.insert(plan.plan_id.clone(), (*plan).clone());
                self.reservations
                    .insert(reservation.reservation_id.clone(), (*reservation).clone());
                self.pending_events.push(CapitalEvent::PlanAuthorized {
                    plan,
                    reservation,
                    event_sequence: self.event_sequence,
                });
            },
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
                validate_plan_relation(&plan, &reservation)?;
                validate_operation_relation(&plan, &operation)?;
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
            },
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
                validate_plan_relation(&plan, &reservation)?;
                if let Some(operation) = operation.as_deref() {
                    validate_operation_relation(&plan, operation)?;
                }
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
            },
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
                    },
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
            },
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
) -> Result<(), String> {
    if reservation.reservation_id != plan.reservation_id
        || reservation.plan_id != plan.plan_id
        || reservation.source != plan.source
        || reservation.amount != plan.amount
    {
        return Err("Capital plan and reservation do not match".into());
    }
    if matches!(
        plan.route_kind,
        CapitalRouteKind::EarnRedemptionThenTransfer | CapitalRouteKind::EarnSubscription
    ) && plan.selected_earn_product_id.is_none()
    {
        return Err("Capital Earn plan has no selected product".into());
    }
    Ok(())
}

fn validate_operation_relation(
    plan: &CapitalPlan,
    operation: &CapitalOperation,
) -> Result<(), String> {
    if operation.plan_id != plan.plan_id {
        return Err("Capital plan and operation do not match".into());
    }
    let expected_kind = match (plan.route_kind, operation.operation_index) {
        (CapitalRouteKind::EarnRedemptionThenTransfer, 0) => CapitalOperationKind::EarnRedemption,
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
        plan.plan_id.as_str(),
        operation.operation_index
    );
    if operation.idempotency_key.as_str() != expected_key {
        return Err("Capital operation idempotency key is not stable".into());
    }
    if operation.operation_index == 0 && operation.idempotency_key != plan.idempotency_key {
        return Err("Capital first operation does not match the plan idempotency key".into());
    }
    Ok(())
}
