use std::collections::HashMap;

use crate::application::{
    CloseCircuit, LimitView, OpenCircuit, ResizeReservation, RiskDecision, RiskEvent, RiskSnapshot,
};
use crate::domain::{
    Allocation, Amount, AuthorizeRequest, DependencyWatermarks, EnforcementMode, Metric,
    ReasonCode, Reservation, ReservationStatus, RiskPolicy,
};
use crate::services::persistence::{PersistedEvent, RiskStateStore};
use kairos_primitives::{
    ActorId, DecisionId, Generation, IdempotencyKey, PolicyId, RequestId, ReservationId, Sequence,
    UnixNanos,
};

#[derive(Debug)]
pub enum ActorError {
    Invalid(String),
    Rejected(String),
    State(String),
    Persistence(String),
}

#[derive(Clone, Debug)]
struct LimitState {
    policy: RiskPolicy,
    used: Amount,
    reserved: Amount,
}

impl LimitState {
    fn available(&self) -> Result<Amount, String> {
        self.policy
            .limit
            .checked_sub(self.used.checked_add(self.reserved)?)
    }
}

/// The only owner of mutable risk state.  It is intentionally synchronous so
/// the caller can place it behind exactly one bounded command loop.
pub(crate) struct RiskActor {
    actor_id: ActorId,
    generation: Generation,
    event_sequence: Sequence,
    policy_version: Generation,
    limits: HashMap<PolicyId, LimitState>,
    by_metric: HashMap<Metric, Vec<PolicyId>>,
    reservations: HashMap<ReservationId, Reservation>,
    idempotency: HashMap<IdempotencyKey, ReservationId>,
    watermarks: DependencyWatermarks,
    store: Option<Box<dyn RiskStateStore>>,
    pending_events: Vec<RiskEvent>,
    circuits: Vec<crate::domain::CircuitState>,
}

impl RiskActor {
    pub(crate) fn new(
        actor_id: impl Into<String>,
        policies: Vec<RiskPolicy>,
        store: Option<Box<dyn RiskStateStore>>,
    ) -> Result<Self, String> {
        let actor_id = ActorId::new(actor_id.into()).map_err(|error| error.to_string())?;
        let mut actor = Self {
            actor_id,
            generation: Generation::new(0),
            event_sequence: Sequence::new(0),
            policy_version: Generation::new(0),
            limits: HashMap::new(),
            by_metric: HashMap::new(),
            reservations: HashMap::new(),
            idempotency: HashMap::new(),
            watermarks: DependencyWatermarks {
                generation: Generation::new(0),
                event_sequence: Sequence::new(0),
            },
            store,
            pending_events: Vec::new(),
            circuits: Vec::new(),
        };
        let mut has_recovered_state = false;
        if let Some(store) = actor.store.as_mut() {
            let recovered = store.load()?;
            if let Some(snapshot) = recovered.snapshot {
                has_recovered_state = true;
                actor.restore(snapshot)?;
            }
            has_recovered_state |= !recovered.events.is_empty();
            for event in recovered.events {
                actor.apply_persisted_event(event)?;
            }
        }
        if !has_recovered_state {
            for policy in policies {
                actor
                    .publish_policy(policy)
                    .map_err(|error| format!("initial policy failed: {error:?}"))?;
            }
        }
        Ok(actor)
    }

    pub(crate) fn restore(&mut self, snapshot: RiskSnapshot) -> Result<(), String> {
        if snapshot.actor_id != self.actor_id {
            return Err("risk snapshot belongs to another actor".into());
        }
        self.generation = snapshot.generation;
        self.event_sequence = snapshot.event_sequence;
        self.policy_version = snapshot.policy_version;
        self.watermarks = snapshot.watermarks;
        self.limits.clear();
        self.by_metric.clear();
        for view in snapshot.limits {
            view.policy.validate()?;
            self.insert_policy(view.policy.clone())?;
            let state = self
                .limits
                .get_mut(&view.policy.policy_id)
                .ok_or_else(|| "restored policy disappeared".to_string())?;
            state.used = view.used;
            state.reserved = view.reserved;
            state.available()?;
        }
        self.reservations.clear();
        self.idempotency.clear();
        for reservation in snapshot.reservations {
            if self
                .reservations
                .insert(reservation.reservation_id.clone(), reservation.clone())
                .is_some()
            {
                return Err("duplicate reservation id in snapshot".into());
            }
            self.idempotency.insert(
                reservation.idempotency_key.clone(),
                reservation.reservation_id.clone(),
            );
        }
        self.circuits = snapshot.circuits;
        Ok(())
    }

    pub(crate) fn publish_policy(&mut self, policy: RiskPolicy) -> Result<(), ActorError> {
        policy.validate().map_err(ActorError::Invalid)?;
        if let Some(existing) = self.limits.get(&policy.policy_id) {
            if policy.version <= existing.policy.version {
                return Err(ActorError::Invalid("policy version must increase".into()));
            }
            let committed = existing
                .used
                .checked_add(existing.reserved)
                .map_err(ActorError::Invalid)?;
            if committed.cmp_value(policy.limit).is_gt() {
                return Err(ActorError::Invalid(
                    "policy limit cannot be lower than committed usage".into(),
                ));
            }
        }
        let next_sequence = self.event_sequence + 1;
        self.persist(PersistedEvent::PolicyActivated {
            sequence: next_sequence.into(),
            policy: policy.clone(),
        })?;
        self.event_sequence = next_sequence;
        self.generation += 1;
        self.advance_watermarks();
        self.policy_version = self.policy_version.max(policy.version);
        self.insert_policy(policy.clone())
            .map_err(ActorError::State)?;
        self.pending_events.push(RiskEvent::PolicyActivated {
            policy,
            event_sequence: next_sequence,
        });
        self.checkpoint();
        Ok(())
    }

    fn evaluate_pre_trade(&self, request: AuthorizeRequest) -> Result<RiskDecision, ActorError> {
        request.validate().map_err(ActorError::Invalid)?;
        let mut reasons = Vec::new();
        let mut violations = Vec::new();
        if self.circuits.iter().any(|c| {
            crate::domain::circuit::blocks(c, request.at_unix_nanos) && c.scope.matches(&request)
        }) {
            reasons.push(ReasonCode::CircuitOpen);
            violations.push("a matching risk circuit is open".into());
        }
        if let Some(context) = request.context.as_ref() {
            if !context.market_is_fresh {
                reasons.push(ReasonCode::StaleMarket);
                violations.push("market data is stale".into());
            }
            if request.metric == Metric::Margin
                && !crate::domain::margin::is_available(context.available_margin, request.amount)
            {
                reasons.push(ReasonCode::InsufficientMargin);
                violations.push("available margin is insufficient".into());
            }
            if request.metric == Metric::Notional
                && context.available_margin != Amount::ZERO
                && !crate::domain::margin::is_available(context.available_margin, request.amount)
            {
                reasons.push(ReasonCode::InsufficientMargin);
                violations.push("available account margin is insufficient for notional".into());
            }
            if matches!(request.metric, Metric::GrossExposure | Metric::NetExposure) {
                let observed =
                    crate::domain::exposure::project(context.current_exposure, request.amount)
                        .map_err(ActorError::Invalid)?;
                if self.policy_exceeded(request.metric, &request, observed)? {
                    reasons.push(ReasonCode::LimitExceeded);
                    violations.push("exposure limit exceeded".into());
                }
            }
            if request.metric == Metric::DailyLoss && context.current_pnl.mantissa() < 0 {
                let loss = Amount::new(
                    context.current_pnl.mantissa().unsigned_abs() as i64,
                    context.current_pnl.scale(),
                )
                .map_err(ActorError::Invalid)?
                .checked_add(request.amount)
                .map_err(ActorError::Invalid)?;
                if self.policy_exceeded(request.metric, &request, loss)? {
                    reasons.push(ReasonCode::LossLimitExceeded);
                    violations.push("daily loss limit exceeded".into());
                }
            }
            if request.metric == Metric::Drawdown
                && self.policy_exceeded(Metric::Drawdown, &request, context.current_drawdown)?
            {
                reasons.push(ReasonCode::LossLimitExceeded);
                violations.push("drawdown limit exceeded".into());
            }
            if request.metric == Metric::Leverage {
                let leverage = Amount::new(
                    i64::try_from(context.leverage_bps.get())
                        .map_err(|_| ActorError::Invalid("leverage value overflow".into()))?,
                    0,
                )
                .map_err(ActorError::Invalid)?;
                if self.policy_exceeded(Metric::Leverage, &request, leverage)? {
                    reasons.push(ReasonCode::LeverageExceeded);
                    violations.push("leverage limit exceeded".into());
                }
            }
            if request.metric == Metric::PriceDeviation {
                let deviation = Amount::new(
                    i64::try_from(context.price_deviation_bps.get())
                        .map_err(|_| ActorError::Invalid("price deviation overflow".into()))?,
                    0,
                )
                .map_err(ActorError::Invalid)?;
                if self.policy_exceeded(Metric::PriceDeviation, &request, deviation)? {
                    reasons.push(ReasonCode::LimitExceeded);
                    violations.push("price deviation limit exceeded".into());
                }
            }
            if request.metric == Metric::StressLoss
                && self.policy_exceeded(Metric::StressLoss, &request, context.stress_loss)?
            {
                reasons.push(ReasonCode::LimitExceeded);
                violations.push("stress scenario loss limit exceeded".into());
            }
        }
        Ok(self.decision(&request, reasons, violations))
    }

    pub(crate) fn pre_trade_check(
        &mut self,
        request: AuthorizeRequest,
    ) -> Result<RiskDecision, ActorError> {
        let decision = self.evaluate_pre_trade(request.clone())?;
        self.commit_decision(&request, decision)
    }

    pub(crate) fn post_trade_check(
        &mut self,
        request: AuthorizeRequest,
    ) -> Result<RiskDecision, ActorError> {
        let decision = self.evaluate_pre_trade(request.clone())?;
        self.commit_decision(&request, decision)
    }

    pub(crate) fn open_circuit(
        &mut self,
        request: OpenCircuit,
    ) -> Result<crate::domain::CircuitState, ActorError> {
        if request.reason.trim().is_empty() {
            return Err(ActorError::Invalid("circuit reason is required".into()));
        }
        let circuit = crate::domain::CircuitState {
            scope: request.scope,
            open: true,
            opened_at_unix_nanos: Some(request.at_unix_nanos),
            reset_at_unix_nanos: request.reset_at_unix_nanos,
            reason: request.reason,
        };
        self.persist(PersistedEvent::CircuitChanged {
            sequence: (self.event_sequence + 1).into(),
            circuit: circuit.clone(),
        })?;
        self.event_sequence += 1;
        self.generation += 1;
        self.advance_watermarks();
        self.circuits.retain(|value| value.scope != circuit.scope);
        self.circuits.push(circuit.clone());
        self.pending_events.push(RiskEvent::CircuitChanged {
            circuit: circuit.clone(),
            event_sequence: self.event_sequence,
        });
        self.checkpoint();
        Ok(circuit)
    }

    pub(crate) fn close_circuit(
        &mut self,
        request: CloseCircuit,
    ) -> Result<crate::domain::CircuitState, ActorError> {
        let mut circuit = self
            .circuits
            .iter()
            .find(|value| value.scope == request.scope)
            .cloned()
            .ok_or_else(|| ActorError::Invalid("circuit not found".into()))?;
        circuit.open = false;
        circuit.reset_at_unix_nanos = Some(request.at_unix_nanos);
        self.persist(PersistedEvent::CircuitChanged {
            sequence: (self.event_sequence + 1).into(),
            circuit: circuit.clone(),
        })?;
        self.event_sequence += 1;
        self.generation += 1;
        self.advance_watermarks();
        self.circuits.retain(|value| value.scope != circuit.scope);
        self.circuits.push(circuit.clone());
        self.pending_events.push(RiskEvent::CircuitChanged {
            circuit: circuit.clone(),
            event_sequence: self.event_sequence,
        });
        self.checkpoint();
        Ok(circuit)
    }

    pub(crate) fn circuits(&self) -> Vec<crate::domain::CircuitState> {
        self.circuits.clone()
    }

    pub(crate) fn authorize_and_reserve(
        &mut self,
        request: AuthorizeRequest,
    ) -> Result<RiskDecision, ActorError> {
        let admission = self.evaluate_pre_trade(request.clone())?;
        if !admission.allowed {
            return self.commit_decision(&request, admission);
        }
        if let Some(existing_id) = self.idempotency.get(&request.idempotency_key) {
            let existing = self
                .reservations
                .get(existing_id)
                .ok_or_else(|| ActorError::State("idempotency index is corrupt".into()))?;
            let decision = RiskDecision {
                decision_id: decision_id(&request.request_id),
                request_id: request.request_id.clone(),
                allowed: existing.status == ReservationStatus::Reserved,
                degraded: false,
                reason_codes: vec![ReasonCode::DuplicateRequest],
                violations: Vec::new(),
                allocations: existing.allocations.clone(),
                reservation: Some(existing.clone()),
                policy_version: existing.policy_version,
                dependency_watermarks: self.watermarks.clone(),
                context: request.context.clone(),
                evaluated_at_unix_nanos: request.at_unix_nanos,
            };
            return self.commit_decision(&request, decision);
        }

        if request.dependency_generation > self.watermarks.generation
            || request.dependency_event_sequence > self.watermarks.event_sequence
        {
            let decision = RiskDecision {
                decision_id: decision_id(&request.request_id),
                request_id: request.request_id.clone(),
                allowed: false,
                degraded: false,
                reason_codes: vec![ReasonCode::StaleDependency],
                violations: vec!["risk dependency watermark is not available".into()],
                allocations: Vec::new(),
                reservation: None,
                policy_version: self.policy_version,
                dependency_watermarks: self.watermarks.clone(),
                context: request.context.clone(),
                evaluated_at_unix_nanos: request.at_unix_nanos,
            };
            return self.commit_decision(&request, decision);
        }

        let mut allocations = Vec::new();
        let mut planned: HashMap<PolicyId, Amount> = HashMap::new();
        let policy_ids = self
            .by_metric
            .get(&request.metric)
            .cloned()
            .unwrap_or_default();
        let mut matches = 0;
        let mut violations = Vec::new();
        let mut reason_codes = Vec::new();
        let mut policy_version = Generation::new(0);

        for policy_id in policy_ids {
            let state = self
                .limits
                .get(&policy_id)
                .ok_or_else(|| ActorError::State("policy index is corrupt".into()))?;
            if !state.policy.active_at(request.at_unix_nanos)
                || !state.policy.scope.matches(&request)
            {
                continue;
            }
            matches += 1;
            policy_version = policy_version.max(state.policy.version);
            let planned_amount = planned.get(&policy_id).copied().unwrap_or(Amount::ZERO);
            let available = self
                .available_for_request(state, &request)
                .map_err(ActorError::State)?
                .checked_sub(planned_amount)
                .map_err(ActorError::State)?;
            if request.amount.cmp_value(available).is_gt() {
                match state.policy.enforcement {
                    EnforcementMode::Reject => {
                        reason_codes.push(ReasonCode::LimitExceeded);
                        violations.push(format!("policy {} limit exceeded", policy_id));
                    }
                    EnforcementMode::Warn | EnforcementMode::Observe => {}
                }
            }
            planned.insert(
                policy_id.clone(),
                planned_amount
                    .checked_add(request.amount)
                    .map_err(ActorError::State)?,
            );
            allocations.push(Allocation {
                policy_id,
                metric: request.metric,
                amount: request.amount,
            });
        }

        if matches == 0 {
            reason_codes.push(ReasonCode::NoMatchingPolicy);
            violations.push(format!("no active policy for {}", request.metric.as_str()));
        }
        if !violations.is_empty() {
            let decision = RiskDecision {
                decision_id: decision_id(&request.request_id),
                request_id: request.request_id.clone(),
                allowed: false,
                degraded: false,
                reason_codes,
                violations,
                allocations: Vec::new(),
                reservation: None,
                policy_version,
                dependency_watermarks: self.watermarks.clone(),
                context: request.context.clone(),
                evaluated_at_unix_nanos: request.at_unix_nanos,
            };
            return self.commit_decision(&request, decision);
        }

        let reservation = Reservation {
            reservation_id: request.reservation_id.clone(),
            request_id: request.request_id.clone(),
            account_id: Some(request.account_id.clone()),
            strategy_id: Some(request.strategy_id.clone()),
            idempotency_key: request.idempotency_key.clone(),
            allocations,
            status: ReservationStatus::Reserved,
            created_at_unix_nanos: request.at_unix_nanos,
            updated_at_unix_nanos: request.at_unix_nanos,
            expires_at_unix_nanos: request
                .at_unix_nanos
                .checked_add(request.reservation_ttl_nanos.get().into())
                .ok_or_else(|| ActorError::Invalid("reservation TTL overflow".into()))?,
            policy_version,
        };
        let next_sequence = self.event_sequence + 1;
        self.persist(PersistedEvent::ReservationChanged {
            sequence: next_sequence.into(),
            reservation: reservation.clone(),
        })?;
        self.event_sequence = next_sequence;
        self.generation += 1;
        self.advance_watermarks();
        self.apply_reservation(&reservation, true)
            .map_err(ActorError::State)?;
        self.idempotency.insert(
            reservation.idempotency_key.clone(),
            reservation.reservation_id.clone(),
        );
        self.reservations
            .insert(reservation.reservation_id.clone(), reservation.clone());
        self.pending_events.push(RiskEvent::ReservationChanged {
            reservation: reservation.clone(),
            event_sequence: next_sequence,
        });
        self.checkpoint();
        let decision = RiskDecision {
            decision_id: decision_id(&reservation.request_id),
            request_id: reservation.request_id.clone(),
            allowed: true,
            degraded: false,
            reason_codes,
            violations,
            allocations: reservation.allocations.clone(),
            reservation: Some(reservation),
            policy_version,
            dependency_watermarks: self.watermarks.clone(),
            context: request.context.clone(),
            evaluated_at_unix_nanos: request.at_unix_nanos,
        };
        self.commit_decision(&request, decision)
    }

    fn commit_decision(
        &mut self,
        request: &AuthorizeRequest,
        decision: RiskDecision,
    ) -> Result<RiskDecision, ActorError> {
        let next_sequence = self.event_sequence + 1;
        self.persist(PersistedEvent::DecisionEvaluated {
            sequence: next_sequence.into(),
            decision: decision.clone(),
            account_id: request.account_id.clone(),
            strategy_id: request.strategy_id.clone(),
        })?;
        self.event_sequence = next_sequence;
        self.generation += 1;
        self.advance_watermarks();
        self.pending_events.push(RiskEvent::DecisionEvaluated {
            decision: decision.clone(),
            account_id: request.account_id.clone(),
            strategy_id: request.strategy_id.clone(),
            event_sequence: next_sequence,
        });
        self.checkpoint();
        Ok(decision)
    }

    pub(crate) fn transition(
        &mut self,
        id: &ReservationId,
        status: ReservationStatus,
        at: UnixNanos,
    ) -> Result<Reservation, ActorError> {
        let current = self
            .reservations
            .get(id)
            .cloned()
            .ok_or_else(|| ActorError::Invalid(format!("reservation not found: {id}")))?;
        if current.status != ReservationStatus::Reserved {
            return Err(ActorError::Rejected(format!(
                "reservation is not active: {id}"
            )));
        }
        let updated = Reservation {
            status,
            updated_at_unix_nanos: at,
            ..current
        };
        let next_sequence = self.event_sequence + 1;
        self.persist(PersistedEvent::ReservationChanged {
            sequence: next_sequence.into(),
            reservation: updated.clone(),
        })?;
        self.event_sequence = next_sequence;
        self.generation += 1;
        self.advance_watermarks();
        self.apply_reservation(&updated, false)
            .map_err(ActorError::State)?;
        self.reservations.insert(id.clone(), updated.clone());
        self.pending_events.push(RiskEvent::ReservationChanged {
            reservation: updated.clone(),
            event_sequence: next_sequence,
        });
        self.checkpoint();
        Ok(updated)
    }

    pub(crate) fn resize(&mut self, request: ResizeReservation) -> Result<Reservation, ActorError> {
        if request.amount == Amount::ZERO {
            return Err(ActorError::Invalid(
                "reservation amount must be positive".into(),
            ));
        }
        let current = self
            .reservations
            .get(&request.reservation_id)
            .cloned()
            .ok_or_else(|| ActorError::Invalid("reservation not found".into()))?;
        if current.status != ReservationStatus::Reserved {
            return Err(ActorError::Rejected("reservation is not active".into()));
        }
        for allocation in &current.allocations {
            let state = self
                .limits
                .get(&allocation.policy_id)
                .ok_or_else(|| ActorError::State("reservation refers to missing policy".into()))?;
            let available_without_current = state
                .available()
                .map_err(ActorError::State)?
                .checked_add(allocation.amount)
                .map_err(ActorError::State)?;
            if request.amount.cmp_value(available_without_current).is_gt()
                && state.policy.enforcement == EnforcementMode::Reject
            {
                return Err(ActorError::Rejected(format!(
                    "policy {} limit exceeded",
                    allocation.policy_id
                )));
            }
        }
        let updated = Reservation {
            allocations: current
                .allocations
                .iter()
                .map(|allocation| Allocation {
                    amount: request.amount,
                    ..allocation.clone()
                })
                .collect(),
            updated_at_unix_nanos: request.at_unix_nanos,
            ..current.clone()
        };
        self.persist(PersistedEvent::ReservationChanged {
            sequence: (self.event_sequence + 1).into(),
            reservation: updated.clone(),
        })?;
        self.event_sequence += 1;
        self.generation += 1;
        self.advance_watermarks();
        self.apply_reservation(&current, false)
            .map_err(ActorError::State)?;
        self.apply_reservation(&updated, true)
            .map_err(ActorError::State)?;
        self.reservations
            .insert(updated.reservation_id.clone(), updated.clone());
        self.pending_events.push(RiskEvent::ReservationChanged {
            reservation: updated.clone(),
            event_sequence: self.event_sequence,
        });
        self.checkpoint();
        Ok(updated)
    }

    pub(crate) fn expire(&mut self, at: UnixNanos) -> Result<usize, ActorError> {
        let ids: Vec<_> = self
            .reservations
            .values()
            .filter(|r| r.status == ReservationStatus::Reserved && r.expires_at_unix_nanos <= at)
            .map(|r| r.reservation_id.clone())
            .collect();
        let mut count = 0;
        for id in ids {
            self.transition(&id, ReservationStatus::Expired, at)?;
            count += 1;
        }
        Ok(count)
    }

    pub(crate) fn current_view(&self) -> crate::RiskCurrentView {
        let mut limits: Vec<_> = self
            .limits
            .values()
            .map(|state| LimitView {
                policy: state.policy.clone(),
                used: state.used,
                reserved: state.reserved,
                available: state.available().unwrap_or(Amount::ZERO),
            })
            .collect();
        limits.sort_by(|a, b| a.policy.policy_id.cmp(&b.policy.policy_id));
        let mut reservations: Vec<_> = self.reservations.values().cloned().collect();
        reservations.sort_by(|a, b| a.reservation_id.cmp(&b.reservation_id));
        crate::RiskCurrentView {
            actor_id: self.actor_id.clone(),
            generation: self.generation,
            policy_version: self.policy_version,
            limits,
            reservations,
            circuits: self.circuits.clone(),
        }
    }

    pub(crate) fn snapshot(&self) -> RiskSnapshot {
        let view = self.current_view();
        RiskSnapshot {
            actor_id: view.actor_id,
            generation: view.generation,
            event_sequence: self.event_sequence,
            policy_version: view.policy_version,
            limits: view.limits,
            reservations: view.reservations,
            watermarks: self.watermarks.clone(),
            circuits: view.circuits,
        }
    }

    pub(crate) fn pending_event(&self) -> Option<&RiskEvent> {
        self.pending_events.first()
    }

    pub(crate) fn acknowledge_event(&mut self) {
        if !self.pending_events.is_empty() {
            self.pending_events.remove(0);
        }
    }

    fn insert_policy(&mut self, policy: RiskPolicy) -> Result<(), String> {
        policy.validate()?;
        let previous_state = self.limits.remove(&policy.policy_id);
        if let Some(previous) = previous_state.as_ref() {
            if let Some(ids) = self.by_metric.get_mut(&previous.policy.metric) {
                ids.retain(|id| id != &previous.policy.policy_id);
            }
        }
        self.limits.insert(
            policy.policy_id.clone(),
            LimitState {
                policy: policy.clone(),
                used: previous_state
                    .as_ref()
                    .map_or(Amount::ZERO, |state| state.used),
                reserved: previous_state
                    .as_ref()
                    .map_or(Amount::ZERO, |state| state.reserved),
            },
        );
        self.by_metric
            .entry(policy.metric)
            .or_default()
            .push(policy.policy_id);
        Ok(())
    }

    fn apply_reservation(
        &mut self,
        reservation: &Reservation,
        reserve: bool,
    ) -> Result<(), String> {
        let sign = reserve;
        let consume = reservation.status == ReservationStatus::Consumed;
        for allocation in &reservation.allocations {
            let state = self
                .limits
                .get_mut(&allocation.policy_id)
                .ok_or_else(|| "reservation refers to missing policy".to_string())?;
            if sign {
                state.reserved = state.reserved.checked_add(allocation.amount)?;
            } else {
                state.reserved = state.reserved.checked_sub(allocation.amount)?;
                if consume {
                    state.used = state.used.checked_add(allocation.amount)?;
                }
            }
        }
        Ok(())
    }

    fn persist(&mut self, event: PersistedEvent) -> Result<(), ActorError> {
        if let Some(store) = self.store.as_mut() {
            store.append(&event).map_err(ActorError::Persistence)?;
        }
        Ok(())
    }

    fn advance_watermarks(&mut self) {
        self.watermarks = DependencyWatermarks {
            generation: self.generation,
            event_sequence: self.event_sequence,
        };
    }

    fn apply_persisted_event(&mut self, event: PersistedEvent) -> Result<(), String> {
        let sequence = event.sequence();
        if sequence <= self.event_sequence.get() {
            return Ok(());
        }
        match event {
            PersistedEvent::PolicyActivated { policy, .. } => {
                self.policy_version = self.policy_version.max(policy.version);
                self.insert_policy(policy)?;
            }
            PersistedEvent::ReservationChanged { reservation, .. } => {
                match self.reservations.get(&reservation.reservation_id).cloned() {
                    None if reservation.status == ReservationStatus::Reserved => {
                        self.apply_reservation(&reservation, true)?;
                        self.idempotency.insert(
                            reservation.idempotency_key.clone(),
                            reservation.reservation_id.clone(),
                        );
                        self.reservations
                            .insert(reservation.reservation_id.clone(), reservation);
                    }
                    Some(current) if current.status == ReservationStatus::Reserved => {
                        self.apply_reservation(&reservation, false)?;
                        self.reservations
                            .insert(reservation.reservation_id.clone(), reservation);
                    }
                    Some(_) => {}
                    None => return Err("journal starts with a terminal reservation".into()),
                }
            }
            PersistedEvent::CircuitChanged { circuit, .. } => {
                self.circuits.retain(|value| value.scope != circuit.scope);
                self.circuits.push(circuit);
            }
            PersistedEvent::DecisionEvaluated { .. } => {}
        }
        self.event_sequence = sequence.into();
        self.generation = self.generation.max(sequence.into());
        Ok(())
    }

    fn checkpoint(&mut self) {
        if self.event_sequence == Sequence::new(0) || !self.event_sequence.is_multiple_of(128) {
            return;
        }
        let snapshot = self.snapshot();
        if let Some(store) = self.store.as_mut() {
            let _ = store.checkpoint(&snapshot);
        }
    }

    fn decision(
        &self,
        request: &AuthorizeRequest,
        reason_codes: Vec<ReasonCode>,
        violations: Vec<String>,
    ) -> RiskDecision {
        RiskDecision {
            decision_id: decision_id(&request.request_id),
            request_id: request.request_id.clone(),
            allowed: violations.is_empty(),
            degraded: false,
            reason_codes,
            violations,
            allocations: Vec::new(),
            reservation: None,
            policy_version: self.policy_version,
            dependency_watermarks: self.watermarks.clone(),
            context: request.context.clone(),
            evaluated_at_unix_nanos: request.at_unix_nanos,
        }
    }

    fn policy_exceeded(
        &self,
        metric: Metric,
        request: &AuthorizeRequest,
        observed: Amount,
    ) -> Result<bool, ActorError> {
        Ok(self
            .by_metric
            .get(&metric)
            .into_iter()
            .flatten()
            .filter_map(|id| self.limits.get(id))
            .filter(|state| {
                state.policy.active_at(request.at_unix_nanos) && state.policy.scope.matches(request)
            })
            .any(|state| observed.cmp_value(state.policy.limit).is_gt()))
    }

    fn available_for_request(
        &self,
        state: &LimitState,
        request: &AuthorizeRequest,
    ) -> Result<Amount, String> {
        if state.policy.metric != Metric::OrderRate {
            return state.available();
        }
        let Some(window) = state.policy.window_nanos else {
            return state.available();
        };
        let start = UnixNanos::new(request.at_unix_nanos.get().saturating_sub(window.get()));
        let used = self
            .reservations
            .values()
            .filter(|reservation| {
                reservation.created_at_unix_nanos >= start
                    && reservation.created_at_unix_nanos <= request.at_unix_nanos
                    && reservation
                        .allocations
                        .iter()
                        .any(|allocation| allocation.policy_id == state.policy.policy_id)
            })
            .try_fold(Amount::ZERO, |total, reservation| {
                reservation
                    .allocations
                    .iter()
                    .filter(|allocation| allocation.policy_id == state.policy.policy_id)
                    .try_fold(total, |inner, allocation| {
                        inner.checked_add(allocation.amount)
                    })
            })?;
        state.policy.limit.checked_sub(used)
    }
}

fn decision_id(request_id: &RequestId) -> DecisionId {
    DecisionId::new(format!("decision:{request_id}"))
        .expect("decision identifiers derived from valid request identifiers")
}
