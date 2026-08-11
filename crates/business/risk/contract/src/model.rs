//! Stable cross-process Risk command, decision, snapshot and event models.

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Amount {
    pub mantissa: i64,
    pub scale: u8,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Metric {
    Notional,
    Margin,
    GrossExposure,
    NetExposure,
    Turnover,
    OrderRate,
    DailyLoss,
    Drawdown,
    Leverage,
    PriceDeviation,
    StressLoss,
}

impl Metric {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Notional => "notional",
            Self::Margin => "margin",
            Self::GrossExposure => "gross_exposure",
            Self::NetExposure => "net_exposure",
            Self::Turnover => "turnover",
            Self::OrderRate => "order_rate",
            Self::DailyLoss => "daily_loss",
            Self::Drawdown => "drawdown",
            Self::Leverage => "leverage",
            Self::PriceDeviation => "price_deviation",
            Self::StressLoss => "stress_loss",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskContext {
    pub account_snapshot_watermark: u64,
    pub market_freshness_watermark: u64,
    pub portfolio_version: u64,
    pub current_exposure: Amount,
    pub current_margin: Amount,
    pub available_margin: Amount,
    pub current_pnl: i64,
    pub current_drawdown: Amount,
    pub market_is_fresh: bool,
    pub leverage_bps: u64,
    pub price_deviation_bps: u64,
    pub stress_loss: Amount,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EnforcementMode {
    Reject,
    Warn,
    Observe,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PolicyScope {
    pub account_id: Option<String>,
    pub strategy_id: Option<String>,
    pub instrument_id: Option<String>,
    pub exchange_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskPolicy {
    pub policy_id: String,
    pub version: u64,
    pub scope: PolicyScope,
    pub metric: Metric,
    pub limit: Amount,
    pub enforcement: EnforcementMode,
    pub valid_from_unix_nanos: u64,
    pub valid_until_unix_nanos: Option<u64>,
    #[serde(default)]
    pub window_nanos: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DependencyWatermarks {
    pub generation: u64,
    pub event_sequence: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AuthorizeRequest {
    pub request_id: String,
    pub idempotency_key: String,
    pub reservation_id: String,
    pub account_id: String,
    pub strategy_id: String,
    pub instrument_id: String,
    pub exchange_id: String,
    pub metric: Metric,
    pub amount: Amount,
    pub at_unix_nanos: u64,
    pub reservation_ttl_nanos: u64,
    pub dependency_generation: u64,
    pub dependency_event_sequence: u64,
    #[serde(default)]
    pub context: Option<RiskContext>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CircuitScope {
    pub account_id: Option<String>,
    pub strategy_id: Option<String>,
    pub exchange_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OpenCircuitRequest {
    pub scope: CircuitScope,
    pub at_unix_nanos: u64,
    pub reset_at_unix_nanos: Option<u64>,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CloseCircuitRequest {
    pub scope: CircuitScope,
    pub at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CircuitState {
    pub scope: CircuitScope,
    pub open: bool,
    pub opened_at_unix_nanos: Option<u64>,
    pub reset_at_unix_nanos: Option<u64>,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Allocation {
    pub policy_id: String,
    pub metric: Metric,
    pub amount: Amount,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReservationStatus {
    Reserved,
    Consumed,
    Released,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Reservation {
    pub reservation_id: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub allocations: Vec<Allocation>,
    pub status: ReservationStatus,
    pub created_at_unix_nanos: u64,
    pub updated_at_unix_nanos: u64,
    pub expires_at_unix_nanos: u64,
    pub policy_version: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReasonCode {
    NoMatchingPolicy,
    LimitExceeded,
    StaleDependency,
    DuplicateRequest,
    ReservationNotFound,
    ReservationNotActive,
    InvalidRequest,
    PersistenceFailure,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LimitView {
    pub policy: RiskPolicy,
    pub used: Amount,
    pub reserved: Amount,
    pub available: Amount,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskDecision {
    pub decision_id: String,
    pub request_id: String,
    pub allowed: bool,
    pub degraded: bool,
    pub reason_codes: Vec<ReasonCode>,
    pub violations: Vec<String>,
    pub allocations: Vec<Allocation>,
    pub reservation: Option<Reservation>,
    pub policy_version: u64,
    pub dependency_watermarks: DependencyWatermarks,
    #[serde(default)]
    pub context: Option<RiskContext>,
    pub evaluated_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub policy_version: u64,
    pub limits: Vec<LimitView>,
    pub reservations: Vec<Reservation>,
    pub watermarks: DependencyWatermarks,
    #[serde(default)]
    pub circuits: Vec<CircuitState>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum RiskEvent {
    PolicyActivated {
        policy: RiskPolicy,
        event_sequence: u64,
    },
    ReservationChanged {
        reservation: Reservation,
        event_sequence: u64,
    },
}
