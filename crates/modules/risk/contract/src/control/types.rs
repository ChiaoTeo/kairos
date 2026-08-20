use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::reference::{Currency, Exchange, InstrumentId};
use kairos_primitives::risk::{DecisionId, PolicyId, ReservationId};
use kairos_primitives::runtime::{ActorId, IdempotencyKey, RequestId, StrategyId};
use kairos_primitives::time::{BasisPoints, DurationNanos, Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RiskControlResponse {
    pub status: Option<String>,
    pub command_id: Option<String>,
    pub decision_id: Option<String>,
    pub request_id: Option<String>,
    pub outcome: Option<String>,
    pub error: Option<RiskControlError>,
    #[serde(flatten)]
    pub details: std::collections::BTreeMap<String, serde_json::Value>,
}
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RiskControlError {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub details: std::collections::BTreeMap<String, serde_json::Value>,
}

// Stable cross-process Risk command, decision, snapshot and event models.

/// Signed fixed-decimal representation used by the Risk process contract.
/// Domain mappings decide whether a particular field is an amount, money,
/// rate, or another semantic value.
pub type DecimalValue = kairos_primitives::decimal::DecimalParts;

/// Compatibility name retained for existing Risk contract callers.
pub type Amount = DecimalValue;

#[cfg(test)]
mod amount_tests {
    use super::{
        AdvanceRiskTimeRequest, Amount, ConsumeReservationRequest, ResizeReservationRequest,
    };

    #[test]
    fn json_amount_is_a_decimal_string_only() {
        let amount = serde_json::from_str::<Amount>("\"-12.50\"").unwrap();
        assert_eq!((amount.mantissa(), amount.scale()), (-1_250, 2));
        assert_eq!(serde_json::to_string(&amount).unwrap(), "\"-12.50\"");
        assert!(serde_json::from_str::<Amount>(r#"{"mantissa":-1250,"scale":2}"#).is_err());
        assert!(serde_json::from_str::<Amount>("\"0.0000000000000000001\"").is_err());
        assert!(Amount::new(1, kairos_primitives::decimal::MAX_DECIMAL_SCALE + 1).is_err());
    }

    #[test]
    fn mutation_controls_have_typed_contract_shapes() {
        let resize = ResizeReservationRequest {
            reservation_id: kairos_primitives::risk::ReservationId::new("reservation-1").unwrap(),
            amount: Amount::new(125, 2).unwrap(),
            at_unix_nanos: 10.into(),
        };
        assert_eq!(
            serde_json::to_value(resize).unwrap(),
            serde_json::json!({
                "reservation_id": "reservation-1",
                "amount": "1.25",
                "at_unix_nanos": 10
            })
        );

        let consume = ConsumeReservationRequest {
            reservation_id: kairos_primitives::risk::ReservationId::new("reservation-1").unwrap(),
            at_unix_nanos: 11.into(),
        };
        assert_eq!(
            serde_json::to_value(consume).unwrap(),
            serde_json::json!({
                "reservation_id": "reservation-1",
                "at_unix_nanos": 11
            })
        );

        assert_eq!(
            serde_json::to_value(AdvanceRiskTimeRequest {
                event_time_unix_nanos: 12.into()
            })
            .unwrap(),
            serde_json::json!({"event_time_unix_nanos": 12})
        );
    }
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
    pub account_snapshot_watermark: UnixNanos,
    pub market_freshness_watermark: UnixNanos,
    pub portfolio_version: Generation,
    pub current_exposure: Amount,
    pub current_margin: Amount,
    pub available_margin: Amount,
    pub current_pnl: Amount,
    pub current_drawdown: Amount,
    pub market_is_fresh: bool,
    pub leverage_bps: BasisPoints,
    pub price_deviation_bps: BasisPoints,
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
    pub account_id: Option<AccountId>,
    pub strategy_id: Option<StrategyId>,
    pub instrument_id: Option<InstrumentId>,
    pub exchange_id: Option<Exchange>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskPolicy {
    pub policy_id: PolicyId,
    pub version: Generation,
    pub scope: PolicyScope,
    pub metric: Metric,
    pub limit: Amount,
    pub enforcement: EnforcementMode,
    pub valid_from_unix_nanos: UnixNanos,
    pub valid_until_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub window_nanos: Option<DurationNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DependencyWatermarks {
    pub generation: Generation,
    pub event_sequence: Sequence,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AuthorizeRequest {
    pub request_id: RequestId,
    pub idempotency_key: IdempotencyKey,
    pub reservation_id: ReservationId,
    pub account_id: AccountId,
    pub strategy_id: StrategyId,
    pub instrument_id: InstrumentId,
    pub exchange_id: Exchange,
    pub proposal: TradeRiskProposal,
    pub at_unix_nanos: UnixNanos,
    pub reservation_ttl_nanos: DurationNanos,
    pub dependency_generation: Generation,
    pub dependency_event_sequence: Sequence,
    #[serde(default)]
    pub context: Option<RiskContext>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TradeRiskProposal {
    pub notional: Amount,
    pub initial_margin_rate_bps: BasisPoints,
    pub account_segment: SegmentKey,
    pub collateral_asset: Currency,
    #[serde(default)]
    pub reduce_only: bool,
    pub margin_rule_id: kairos_primitives::risk::MarginRuleCode,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CircuitScope {
    pub account_id: Option<AccountId>,
    pub strategy_id: Option<StrategyId>,
    pub exchange_id: Option<Exchange>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OpenCircuitRequest {
    pub scope: CircuitScope,
    pub at_unix_nanos: UnixNanos,
    pub reset_at_unix_nanos: Option<UnixNanos>,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CloseCircuitRequest {
    pub scope: CircuitScope,
    pub at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PublishPolicyRequest {
    pub policy: RiskPolicy,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResizeReservationRequest {
    pub reservation_id: ReservationId,
    pub amount: Amount,
    pub at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReleaseReservationRequest {
    pub reservation_id: ReservationId,
    pub at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ConsumeReservationRequest {
    pub reservation_id: ReservationId,
    pub at_unix_nanos: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AdvanceRiskTimeRequest {
    pub event_time_unix_nanos: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AdvanceRiskTimeResponse {
    pub event_time_unix_nanos: UnixNanos,
    pub expired: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskCommandStatus {
    pub status: String,
}

/// The closed REST operation set owned by the Risk process Contract.
///
/// HTTP method/path selection and JSON framing are transport concerns. Once a
/// request reaches Conflux, the Actor receives one of these typed operations.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RiskRestRequest {
    Health,
    PublishPolicy(PublishPolicyRequest),
    AuthorizeAndReserve(AuthorizeRequest),
    PreTradeCheck(AuthorizeRequest),
    PostTradeCheck(AuthorizeRequest),
    OpenCircuit(OpenCircuitRequest),
    CloseCircuit(CloseCircuitRequest),
    ResizeReservation(ResizeReservationRequest),
    ReleaseReservation(ReleaseReservationRequest),
    ConsumeReservation(ConsumeReservationRequest),
    AdvanceTime(AdvanceRiskTimeRequest),
}

/// Response pair for [`RiskRestRequest`]. Each variant preserves the operation
/// identity so the Contract host cannot accidentally return one operation's
/// payload for another request.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RiskRestResponse {
    Health(Result<crate::Health, RiskControlError>),
    PublishPolicy(Result<RiskCommandStatus, RiskControlError>),
    AuthorizeAndReserve(Result<RiskDecision, RiskControlError>),
    PreTradeCheck(Result<RiskDecision, RiskControlError>),
    PostTradeCheck(Result<RiskDecision, RiskControlError>),
    OpenCircuit(Result<CircuitState, RiskControlError>),
    CloseCircuit(Result<CircuitState, RiskControlError>),
    ResizeReservation(Result<Reservation, RiskControlError>),
    ReleaseReservation(Result<Reservation, RiskControlError>),
    ConsumeReservation(Result<Reservation, RiskControlError>),
    AdvanceTime(Result<AdvanceRiskTimeResponse, RiskControlError>),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CircuitState {
    pub scope: CircuitScope,
    pub open: bool,
    pub opened_at_unix_nanos: Option<UnixNanos>,
    pub reset_at_unix_nanos: Option<UnixNanos>,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Allocation {
    pub policy_id: PolicyId,
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
    pub reservation_id: ReservationId,
    pub request_id: RequestId,
    #[serde(default)]
    pub account_id: Option<AccountId>,
    #[serde(default)]
    pub strategy_id: Option<StrategyId>,
    pub idempotency_key: IdempotencyKey,
    pub allocations: Vec<Allocation>,
    pub status: ReservationStatus,
    pub created_at_unix_nanos: UnixNanos,
    pub updated_at_unix_nanos: UnixNanos,
    pub expires_at_unix_nanos: UnixNanos,
    pub policy_version: Generation,
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
    CircuitOpen,
    StaleMarket,
    InsufficientMargin,
    LeverageExceeded,
    LossLimitExceeded,
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
    pub decision_id: DecisionId,
    pub request_id: RequestId,
    pub account_id: AccountId,
    pub strategy_id: StrategyId,
    pub instrument_id: InstrumentId,
    pub allowed: bool,
    pub degraded: bool,
    pub reason_codes: Vec<ReasonCode>,
    pub violations: Vec<String>,
    pub allocations: Vec<Allocation>,
    pub reservation: Option<Reservation>,
    pub policy_version: Generation,
    pub dependency_watermarks: DependencyWatermarks,
    #[serde(default)]
    pub context: Option<RiskContext>,
    #[serde(default)]
    pub funding_requirement: Option<FundingRequirement>,
    pub evaluated_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FundingRequirement {
    pub required_margin: Amount,
    pub available_margin: Amount,
    pub shortfall: Amount,
    pub margin_rule_id: kairos_primitives::risk::MarginRuleCode,
    pub account_segment: SegmentKey,
    pub collateral_asset: Currency,
}

/// mmap current-state contract. It cannot carry event history or positions.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskCurrentView {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub policy_version: Generation,
    pub limits: Vec<LimitView>,
    pub reservations: Vec<Reservation>,
    #[serde(default)]
    pub circuits: Vec<CircuitState>,
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
        account_id: AccountId,
        strategy_id: StrategyId,
        event_sequence: Sequence,
    },
    CircuitChanged {
        circuit: CircuitState,
        event_sequence: Sequence,
    },
}
