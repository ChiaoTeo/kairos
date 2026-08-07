use crate::application::protocol::RiskStateStore;
use crate::domain::{Amount, Budget, Metric, Reservation, Usage};
use crate::services::actor::RiskActor;

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize)]
pub struct ConfigureBudgets {
    pub budgets: Vec<Budget>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize)]
pub struct AssessRisk {
    pub request_id: String,
    pub usages: Vec<Usage>,
    pub at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize)]
pub struct ReserveRisk {
    pub reservation_id: String,
    pub assessment: AssessRisk,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize)]
pub struct ReleaseReservation {
    pub reservation_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize)]
pub struct ConsumeReservation {
    pub reservation_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct RiskAssessment {
    pub request_id: String,
    pub allowed: bool,
    pub allocations: Vec<(String, Metric, Amount)>,
    pub violations: Vec<String>,
    pub evaluated_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub enum RiskEvent {
    ReservationChanged {
        reservation: Reservation,
        event_sequence: u64,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct RiskSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub budgets: Vec<Budget>,
    pub reservations: Vec<Reservation>,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum RiskError {
    #[error("invalid risk request: {0}")]
    Invalid(String),
    #[error("risk state failed: {0}")]
    State(String),
    #[error("risk request rejected: {0}")]
    Rejected(String),
}

pub struct RiskApplication {
    actor: RiskActor,
}

impl RiskApplication {
    pub(crate) fn new(actor: RiskActor) -> Self {
        Self { actor }
    }

    pub fn with_dependencies(
        actor_id: impl Into<String>,
        budgets: Vec<Budget>,
        allow_unbudgeted: bool,
        store: Option<Box<dyn RiskStateStore>>,
    ) -> Result<Self, RiskError> {
        RiskActor::new(actor_id, budgets, allow_unbudgeted, store)
            .map(Self::new)
            .map_err(RiskError::Invalid)
    }

    pub fn configure(&mut self, request: ConfigureBudgets) -> Result<(), RiskError> {
        self.actor
            .replace_budgets(request.budgets)
            .map_err(RiskError::Invalid)
    }

    pub fn assess(&self, request: AssessRisk) -> Result<RiskAssessment, RiskError> {
        if request.request_id.trim().is_empty() {
            return Err(RiskError::Invalid("request_id is required".into()));
        }
        self.actor.assess(&request).map_err(RiskError::Invalid)
    }

    pub fn reserve(&mut self, request: ReserveRisk) -> Result<Reservation, RiskError> {
        if request.reservation_id.trim().is_empty() {
            return Err(RiskError::Invalid("reservation_id is required".into()));
        }
        self.actor.reserve(request).map_err(|error| match error {
            crate::services::actor::ActorError::Rejected(value) => RiskError::Rejected(value),
            crate::services::actor::ActorError::Invalid(value) => RiskError::Invalid(value),
            crate::services::actor::ActorError::State(value) => RiskError::State(value),
        })
    }

    pub fn release(&mut self, request: ReleaseReservation) -> Result<Reservation, RiskError> {
        self.actor
            .release(&request.reservation_id)
            .map_err(RiskError::Invalid)
    }

    pub fn consume(&mut self, request: ConsumeReservation) -> Result<Reservation, RiskError> {
        self.actor
            .consume(&request.reservation_id)
            .map_err(RiskError::Invalid)
    }

    pub fn snapshot(&self) -> RiskSnapshot {
        self.actor.snapshot()
    }

    pub fn drain_events(&mut self) -> Vec<RiskEvent> {
        self.actor.drain_events()
    }
}
