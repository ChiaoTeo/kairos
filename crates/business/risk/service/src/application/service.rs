use crate::domain::{Amount, Budget, Metric, Reservation, Usage};
use crate::services::actor::RiskActor;
use tracing::{info, warn};

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

    pub fn configure(&mut self, request: ConfigureBudgets) -> Result<(), RiskError> {
        let budget_count = request.budgets.len();
        info!(
            event = "risk_budgets_configure_started",
            component = "risk",
            budget_count,
            "risk budget configuration started"
        );
        let result = self
            .actor
            .replace_budgets(request.budgets)
            .map_err(RiskError::Invalid);
        match &result {
            Ok(()) => info!(
                event = "risk_budgets_configured",
                component = "risk",
                budget_count,
                "risk budgets configured"
            ),
            Err(error) => {
                warn!(event = "risk_budgets_configuration_failed", component = "risk", budget_count, error = %error, "risk budget configuration failed")
            }
        }
        result
    }

    pub fn assess(&self, request: AssessRisk) -> Result<RiskAssessment, RiskError> {
        if request.request_id.trim().is_empty() {
            return Err(RiskError::Invalid("request_id is required".into()));
        }
        info!(event = "risk_assessment_started", component = "risk", request_id = %request.request_id, usage_count = request.usages.len(), "risk assessment started");
        let result = self.actor.assess(&request).map_err(RiskError::Invalid);
        match &result {
            Ok(result) if result.allowed => {
                info!(event = "risk_assessment_allowed", component = "risk", request_id = %result.request_id, allocation_count = result.allocations.len(), "risk assessment allowed")
            }
            Ok(result) => {
                warn!(event = "risk_assessment_rejected", component = "risk", request_id = %result.request_id, violation_count = result.violations.len(), "risk assessment rejected")
            }
            Err(error) => {
                warn!(event = "risk_assessment_failed", component = "risk", request_id = %request.request_id, error = %error, "risk assessment failed")
            }
        }
        result
    }

    pub fn reserve(&mut self, request: ReserveRisk) -> Result<Reservation, RiskError> {
        if request.reservation_id.trim().is_empty() {
            return Err(RiskError::Invalid("reservation_id is required".into()));
        }
        let reservation_id = request.reservation_id.clone();
        let request_id = request.assessment.request_id.clone();
        info!(event = "risk_reservation_started", component = "risk", reservation_id = %reservation_id, request_id = %request_id, "risk reservation started");
        let result = self.actor.reserve(request).map_err(|error| match error {
            crate::services::actor::ActorError::Rejected(value) => RiskError::Rejected(value),
            crate::services::actor::ActorError::Invalid(value) => RiskError::Invalid(value),
            crate::services::actor::ActorError::State(value) => RiskError::State(value),
        });
        match &result {
            Ok(_) => {
                info!(event = "risk_reservation_created", component = "risk", reservation_id = %reservation_id, request_id = %request_id, "risk reservation created")
            }
            Err(error) => {
                warn!(event = "risk_reservation_rejected", component = "risk", reservation_id = %reservation_id, request_id = %request_id, error = %error, "risk reservation rejected")
            }
        }
        result
    }

    pub fn release(&mut self, request: ReleaseReservation) -> Result<Reservation, RiskError> {
        let reservation_id = request.reservation_id.clone();
        info!(event = "risk_reservation_release_started", component = "risk", reservation_id = %reservation_id, "risk reservation release started");
        let result = self
            .actor
            .release(&reservation_id)
            .map_err(RiskError::Invalid);
        match &result {
            Ok(_) => {
                info!(event = "risk_reservation_released", component = "risk", reservation_id = %reservation_id, "risk reservation released")
            }
            Err(error) => {
                warn!(event = "risk_reservation_release_failed", component = "risk", reservation_id = %reservation_id, error = %error, "risk reservation release failed")
            }
        }
        result
    }

    pub fn consume(&mut self, request: ConsumeReservation) -> Result<Reservation, RiskError> {
        let reservation_id = request.reservation_id.clone();
        info!(event = "risk_reservation_consume_started", component = "risk", reservation_id = %reservation_id, "risk reservation consume started");
        let result = self
            .actor
            .consume(&reservation_id)
            .map_err(RiskError::Invalid);
        match &result {
            Ok(_) => {
                info!(event = "risk_reservation_consumed", component = "risk", reservation_id = %reservation_id, "risk reservation consumed")
            }
            Err(error) => {
                warn!(event = "risk_reservation_consume_failed", component = "risk", reservation_id = %reservation_id, error = %error, "risk reservation consume failed")
            }
        }
        result
    }

    pub fn snapshot(&self) -> RiskSnapshot {
        self.actor.snapshot()
    }

    pub fn drain_events(&mut self) -> Vec<RiskEvent> {
        self.actor.drain_events()
    }
}
