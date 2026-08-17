use crate::domain::{
    Allocation, AuthorizeRequest, CircuitScope, CircuitState, DependencyWatermarks, ReasonCode,
    Reservation, ReservationStatus, RiskPolicy,
};
use crate::services::actor::{ActorError, RiskActor};
use kairos_primitives::{
    ActorId, DecisionId, Generation, RequestId, ReservationId, Sequence, UnixNanos,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Deserialize)]
pub struct PublishPolicy {
    pub policy: RiskPolicy,
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize)]
pub struct ConsumeReservation {
    pub reservation_id: ReservationId,
    pub at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize)]
pub struct ReleaseReservation {
    pub reservation_id: ReservationId,
    pub at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize)]
pub struct ResizeReservation {
    pub reservation_id: ReservationId,
    pub amount: crate::domain::Amount,
    pub at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize)]
pub struct ExpireReservations {
    pub at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize)]
pub struct OpenCircuit {
    pub scope: CircuitScope,
    pub at_unix_nanos: UnixNanos,
    pub reset_at_unix_nanos: Option<UnixNanos>,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Deserialize)]
pub struct CloseCircuit {
    pub scope: CircuitScope,
    pub at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskDecision {
    pub decision_id: DecisionId,
    pub request_id: RequestId,
    pub allowed: bool,
    pub degraded: bool,
    pub reason_codes: Vec<ReasonCode>,
    pub violations: Vec<String>,
    pub allocations: Vec<Allocation>,
    pub reservation: Option<Reservation>,
    pub policy_version: Generation,
    pub dependency_watermarks: DependencyWatermarks,
    /// External account/market/portfolio facts used during evaluation.
    pub context: Option<crate::domain::RiskContext>,
    pub evaluated_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum RiskEvent {
    PolicyActivated {
        policy: RiskPolicy,
        event_sequence: Sequence,
    },
    ReservationChanged {
        reservation: Reservation,
        event_sequence: Sequence,
    },
    DecisionEvaluated {
        decision: RiskDecision,
        account_id: kairos_primitives::AccountId,
        strategy_id: kairos_primitives::StrategyId,
        event_sequence: Sequence,
    },
    CircuitChanged {
        circuit: CircuitState,
        event_sequence: Sequence,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LimitView {
    pub policy: RiskPolicy,
    pub used: crate::domain::Amount,
    pub reserved: crate::domain::Amount,
    pub available: crate::domain::Amount,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskSnapshot {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub policy_version: Generation,
    pub limits: Vec<LimitView>,
    pub reservations: Vec<Reservation>,
    pub watermarks: DependencyWatermarks,
    pub circuits: Vec<CircuitState>,
}

/// Read-only state published through mmap.
///
/// The applied event sequence is a state watermark, not a replay cursor.
/// Event delivery remains owned by Aeron or an explicit journal.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskCurrentView {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub policy_version: Generation,
    pub limits: Vec<LimitView>,
    pub reservations: Vec<Reservation>,
    pub circuits: Vec<CircuitState>,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum RiskError {
    #[error("invalid risk request: {0}")]
    Invalid(String),
    #[error("risk state failed: {0}")]
    State(String),
    #[error("risk request rejected: {0}")]
    Rejected(String),
    #[error("risk persistence failed: {0}")]
    Persistence(String),
    #[error("risk service is busy")]
    Busy,
}

pub struct RiskApplication {
    pub(crate) actor: RiskActor,
}

impl RiskApplication {
    pub(crate) fn new(actor: RiskActor) -> Self {
        Self { actor }
    }

    pub fn publish_policy(&mut self, request: PublishPolicy) -> Result<(), RiskError> {
        self.actor
            .publish_policy(request.policy)
            .map_err(map_actor_error)
    }

    /// The only authoritative transaction for trade admission.  Evaluation
    /// and reservation are deliberately one state-owner operation.
    pub fn authorize_and_reserve(
        &mut self,
        request: AuthorizeRequest,
    ) -> Result<RiskDecision, RiskError> {
        self.actor
            .authorize_and_reserve(request)
            .map_err(map_actor_error)
    }

    pub fn pre_trade_check(
        &mut self,
        request: AuthorizeRequest,
    ) -> Result<RiskDecision, RiskError> {
        self.actor.pre_trade_check(request).map_err(map_actor_error)
    }

    pub fn post_trade_check(
        &mut self,
        request: AuthorizeRequest,
    ) -> Result<RiskDecision, RiskError> {
        self.actor
            .post_trade_check(request)
            .map_err(map_actor_error)
    }

    pub fn open_circuit(&mut self, request: OpenCircuit) -> Result<CircuitState, RiskError> {
        self.actor.open_circuit(request).map_err(map_actor_error)
    }

    pub fn close_circuit(&mut self, request: CloseCircuit) -> Result<CircuitState, RiskError> {
        self.actor.close_circuit(request).map_err(map_actor_error)
    }

    pub fn circuits(&self) -> Vec<CircuitState> {
        self.actor.circuits()
    }

    pub fn consume(&mut self, request: ConsumeReservation) -> Result<Reservation, RiskError> {
        self.actor
            .transition(
                &request.reservation_id,
                ReservationStatus::Consumed,
                request.at_unix_nanos,
            )
            .map_err(map_actor_error)
    }

    pub fn release(&mut self, request: ReleaseReservation) -> Result<Reservation, RiskError> {
        self.actor
            .transition(
                &request.reservation_id,
                ReservationStatus::Released,
                request.at_unix_nanos,
            )
            .map_err(map_actor_error)
    }

    pub fn resize(&mut self, request: ResizeReservation) -> Result<Reservation, RiskError> {
        self.actor.resize(request).map_err(map_actor_error)
    }

    pub fn expire(&mut self, request: ExpireReservations) -> Result<usize, RiskError> {
        self.actor
            .expire(request.at_unix_nanos)
            .map_err(map_actor_error)
    }

    pub fn snapshot(&self) -> RiskSnapshot {
        self.actor.snapshot()
    }

    pub fn current_view(&self) -> RiskCurrentView {
        self.actor.current_view()
    }

    pub fn pending_event(&self) -> Option<&RiskEvent> {
        self.actor.pending_event()
    }

    pub fn acknowledge_event(&mut self) {
        self.actor.acknowledge_event();
    }
}

fn map_actor_error(error: ActorError) -> RiskError {
    match error {
        ActorError::Invalid(value) => RiskError::Invalid(value),
        ActorError::Rejected(value) => RiskError::Rejected(value),
        ActorError::State(value) => RiskError::State(value),
        ActorError::Persistence(value) => RiskError::Persistence(value),
    }
}
