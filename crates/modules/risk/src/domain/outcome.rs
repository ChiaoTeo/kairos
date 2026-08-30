use kairos_primitives::risk::{DecisionId, MarginRuleCode};
use kairos_primitives::runtime::{ActorId, RequestId};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use super::{
    Allocation, Amount, CircuitState, DependencyWatermarks, ReasonCode, Reservation, RiskContext,
    RiskPolicy,
};

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
    pub context: Option<RiskContext>,
    pub funding_requirement: Option<FundingRequirement>,
    pub evaluated_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FundingRequirement {
    pub required_margin: Amount,
    pub available_margin: Amount,
    pub shortfall: Amount,
    pub margin_rule_id: MarginRuleCode,
    pub account_segment: kairos_primitives::account::SegmentKey,
    pub collateral_asset: kairos_primitives::reference::Currency,
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
        account_id: kairos_primitives::account::AccountId,
        strategy_id: kairos_primitives::runtime::StrategyId,
        instrument_id: kairos_primitives::reference::InstrumentId,
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
    pub used: Amount,
    pub reserved: Amount,
    pub available: Amount,
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
