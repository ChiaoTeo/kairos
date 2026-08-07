use std::collections::BTreeMap;

use crate::application::protocol::{RiskEventSink, RiskPublisher, RiskStateStore};
use crate::application::{AssessRisk, ReserveRisk, RiskAssessment, RiskEvent, RiskSnapshot};
use crate::domain::{Budget, Reservation, ReservationAllocation, ReservationStatus};

#[derive(Debug)]
pub enum ActorError {
    Invalid(String),
    Rejected(String),
    State(String),
}

pub(crate) struct RiskActor {
    actor_id: String,
    generation: u64,
    event_sequence: u64,
    budgets: BTreeMap<String, Budget>,
    reservations: BTreeMap<String, Reservation>,
    allow_unbudgeted: bool,
    store: Option<Box<dyn RiskStateStore>>,
    publisher: Option<Box<dyn RiskPublisher>>,
    events: Option<Box<dyn RiskEventSink>>,
}

impl RiskActor {
    pub(crate) fn new(
        actor_id: impl Into<String>,
        budgets: Vec<Budget>,
        allow_unbudgeted: bool,
        store: Option<Box<dyn RiskStateStore>>,
        publisher: Option<Box<dyn RiskPublisher>>,
        events: Option<Box<dyn RiskEventSink>>,
    ) -> Result<Self, String> {
        let actor_id = actor_id.into();
        if actor_id.trim().is_empty() {
            return Err("risk actor id is required".into());
        }
        let mut actor = Self {
            actor_id,
            generation: 0,
            event_sequence: 0,
            budgets: BTreeMap::new(),
            reservations: BTreeMap::new(),
            allow_unbudgeted,
            store,
            publisher,
            events,
        };
        if let Some(store) = actor.store.as_mut() {
            if let Some(snapshot) = store.load()? {
                actor.restore(snapshot)?;
                return Ok(actor);
            }
        }
        actor.replace_budgets(budgets)?;
        Ok(actor)
    }

    pub(crate) fn restore(&mut self, snapshot: RiskSnapshot) -> Result<(), String> {
        if snapshot.actor_id != self.actor_id {
            return Err("risk snapshot belongs to another actor".into());
        }
        let mut budgets = BTreeMap::new();
        for budget in snapshot.budgets {
            budget.validate()?;
            if budgets.insert(budget.budget_id.clone(), budget).is_some() {
                return Err("risk snapshot contains duplicate budget ids".into());
            }
        }
        let mut reservations = BTreeMap::new();
        for reservation in snapshot.reservations {
            if reservation.reservation_id.trim().is_empty() {
                return Err("risk snapshot contains an empty reservation id".into());
            }
            if reservations
                .insert(reservation.reservation_id.clone(), reservation)
                .is_some()
            {
                return Err("risk snapshot contains duplicate reservation ids".into());
            }
        }
        self.generation = snapshot.generation;
        self.event_sequence = snapshot.event_sequence;
        self.budgets = budgets;
        self.reservations = reservations;
        let restored = self.snapshot();
        if let Some(publisher) = self.publisher.as_mut() {
            publisher.publish(&restored)?;
        }
        Ok(())
    }

    pub fn replace_budgets(&mut self, budgets: Vec<Budget>) -> Result<(), String> {
        if !self.reservations.is_empty() {
            return Err("cannot replace budgets while reservations exist".into());
        }
        let mut next = BTreeMap::new();
        for budget in budgets {
            budget.validate()?;
            if next.insert(budget.budget_id.clone(), budget).is_some() {
                return Err("budget ids must be unique".into());
            }
        }
        self.budgets = next;
        self.changed().map_err(|e| e.to_string())
    }

    pub fn assess(&self, request: &AssessRisk) -> Result<RiskAssessment, String> {
        if request.usages.is_empty() {
            return Err("at least one risk usage is required".into());
        }
        let mut available: BTreeMap<String, _> = self
            .budgets
            .iter()
            .map(|(id, b)| (id.clone(), b.available()))
            .collect();
        let mut allocations = Vec::new();
        let mut violations = Vec::new();
        for usage in &request.usages {
            let matches: Vec<_> = self
                .budgets
                .values()
                .filter(|b| {
                    b.metric == usage.metric
                        && b.active_at(request.at_unix_nanos)
                        && usage.budgets.contains(&b.reference)
                })
                .collect();
            if matches.is_empty() {
                if !self.allow_unbudgeted && usage.amount.mantissa != 0 {
                    violations.push(format!("no active budget for {}", usage.metric.as_str()));
                }
                continue;
            }
            let remaining = matches
                .iter()
                .map(|b| available[&b.budget_id])
                .min_by(|a, b| a.cmp_value(*b))
                .unwrap();
            if usage.amount.cmp_value(remaining).is_gt() {
                violations.push(format!(
                    "{} exceeds available budget",
                    usage.metric.as_str()
                ));
            }
            for budget in matches {
                let approved = if usage.amount.cmp_value(remaining).is_gt() {
                    remaining
                } else {
                    usage.amount
                };
                allocations.push((budget.budget_id.clone(), usage.metric, approved));
                available.insert(
                    budget.budget_id.clone(),
                    available[&budget.budget_id]
                        .checked_sub(approved)
                        .map_err(|e| e.to_string())?,
                );
            }
        }
        Ok(RiskAssessment {
            request_id: request.request_id.clone(),
            allowed: violations.is_empty(),
            allocations,
            violations,
            evaluated_at_unix_nanos: request.at_unix_nanos,
        })
    }

    pub fn reserve(&mut self, request: ReserveRisk) -> Result<Reservation, ActorError> {
        if let Some(existing) = self.reservations.get(&request.reservation_id) {
            if existing.request_id == request.assessment.request_id {
                return Ok(existing.clone());
            }
            return Err(ActorError::Rejected("conflicting reservation id".into()));
        }
        let assessment = self
            .assess(&request.assessment)
            .map_err(ActorError::Invalid)?;
        if !assessment.allowed {
            return Err(ActorError::Rejected(assessment.violations.join("; ")));
        }
        let allocations = assessment
            .allocations
            .into_iter()
            .map(|(budget_id, metric, amount)| ReservationAllocation {
                budget_id,
                metric,
                amount,
            })
            .collect::<Vec<_>>();
        for allocation in &allocations {
            let budget = self
                .budgets
                .get_mut(&allocation.budget_id)
                .ok_or_else(|| ActorError::State("budget disappeared during reservation".into()))?;
            budget.reserved = budget
                .reserved
                .checked_add(allocation.amount)
                .map_err(ActorError::State)?;
        }
        let reservation = Reservation {
            reservation_id: request.reservation_id,
            request_id: request.assessment.request_id,
            allocations,
            status: ReservationStatus::Reserved,
            created_at_unix_nanos: request.assessment.at_unix_nanos,
            updated_at_unix_nanos: request.assessment.at_unix_nanos,
        };
        self.reservations
            .insert(reservation.reservation_id.clone(), reservation.clone());
        self.changed()
            .map_err(|e| ActorError::State(e.to_string()))?;
        self.emit(RiskEvent::ReservationChanged {
            reservation: reservation.clone(),
            event_sequence: self.event_sequence,
        })
        .map_err(ActorError::State)?;
        Ok(reservation)
    }

    pub fn release(&mut self, id: &str) -> Result<Reservation, String> {
        self.transition(id, ReservationStatus::Released, false)
    }
    pub fn consume(&mut self, id: &str) -> Result<Reservation, String> {
        self.transition(id, ReservationStatus::Consumed, true)
    }

    pub fn snapshot(&self) -> RiskSnapshot {
        RiskSnapshot {
            actor_id: self.actor_id.clone(),
            generation: self.generation,
            event_sequence: self.event_sequence,
            budgets: self.budgets.values().cloned().collect(),
            reservations: self.reservations.values().cloned().collect(),
        }
    }

    fn transition(
        &mut self,
        id: &str,
        status: ReservationStatus,
        consume: bool,
    ) -> Result<Reservation, String> {
        let current = self
            .reservations
            .get(id)
            .cloned()
            .ok_or_else(|| format!("unknown reservation: {id}"))?;
        if current.status != ReservationStatus::Reserved {
            return Err(format!("reservation is not active: {id}"));
        }
        for allocation in &current.allocations {
            let budget = self
                .budgets
                .get_mut(&allocation.budget_id)
                .ok_or_else(|| "budget missing for reservation".to_string())?;
            budget.reserved = budget.reserved.checked_sub(allocation.amount)?;
            if consume {
                budget.used = budget.used.checked_add(allocation.amount)?;
            }
        }
        let updated = Reservation {
            status,
            updated_at_unix_nanos: current.updated_at_unix_nanos,
            ..current
        };
        self.reservations.insert(id.to_string(), updated.clone());
        self.changed()?;
        self.emit(RiskEvent::ReservationChanged {
            reservation: updated.clone(),
            event_sequence: self.event_sequence,
        })?;
        Ok(updated)
    }

    fn emit(&mut self, event: RiskEvent) -> Result<(), String> {
        if let Some(events) = self.events.as_mut() {
            events.publish(&event)?;
        }
        Ok(())
    }
    fn changed(&mut self) -> Result<(), String> {
        self.generation += 1;
        self.event_sequence += 1;
        let snapshot = self.snapshot();
        if let Some(store) = self.store.as_mut() {
            store.save(&snapshot)?;
        }
        if let Some(publisher) = self.publisher.as_mut() {
            publisher.publish(&snapshot)?;
        }
        Ok(())
    }
}
