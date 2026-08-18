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

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Amount {
    pub mantissa: i64,
    pub scale: u8,
}

impl Serialize for Amount {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        if self.scale > 18 {
            return Err(serde::ser::Error::custom("decimal scale exceeds 18 digits"));
        }
        let negative = self.mantissa < 0;
        let magnitude = i128::from(self.mantissa).abs();
        let value = if self.scale == 0 {
            format!("{}{magnitude}", if negative { "-" } else { "" })
        } else {
            let factor = 10_i128.pow(u32::from(self.scale));
            format!(
                "{}{whole}.{fraction:0width$}",
                if negative { "-" } else { "" },
                whole = magnitude / factor,
                fraction = magnitude % factor,
                width = usize::from(self.scale)
            )
        };
        serializer.serialize_str(&value)
    }
}

impl<'de> Deserialize<'de> for Amount {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        let (negative, unsigned) = value
            .strip_prefix('-')
            .map_or((false, value.as_str()), |value| (true, value));
        let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
        if whole.is_empty()
            || fraction.len() > 18
            || !whole.bytes().all(|byte| byte.is_ascii_digit())
            || !fraction.bytes().all(|byte| byte.is_ascii_digit())
        {
            return Err(serde::de::Error::custom(
                "expected a decimal string with at most 18 fractional digits",
            ));
        }
        let magnitude = format!("{whole}{fraction}")
            .parse::<i128>()
            .map_err(serde::de::Error::custom)?;
        let mantissa = i64::try_from(if negative { -magnitude } else { magnitude })
            .map_err(serde::de::Error::custom)?;
        Ok(Self {
            mantissa,
            scale: fraction.len() as u8,
        })
    }
}

#[cfg(test)]
mod amount_tests {
    use super::{
        AdvanceRiskTimeRequest, Amount, ConsumeReservationRequest, ResizeReservationRequest,
    };

    #[test]
    fn json_amount_is_a_decimal_string_only() {
        let amount = serde_json::from_str::<Amount>("\"-12.50\"").unwrap();
        assert_eq!((amount.mantissa, amount.scale), (-1_250, 2));
        assert_eq!(serde_json::to_string(&amount).unwrap(), "\"-12.50\"");
        assert!(serde_json::from_str::<Amount>(r#"{"mantissa":-1250,"scale":2}"#).is_err());
    }

    #[test]
    fn mutation_controls_have_typed_contract_shapes() {
        let resize = ResizeReservationRequest {
            reservation_id: "reservation-1".into(),
            amount: Amount {
                mantissa: 125,
                scale: 2,
            },
            at_unix_nanos: 10,
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
            reservation_id: "reservation-1".into(),
            at_unix_nanos: 11,
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
                event_time_unix_nanos: 12
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
    pub account_snapshot_watermark: u64,
    pub market_freshness_watermark: u64,
    pub portfolio_version: u64,
    pub current_exposure: Amount,
    pub current_margin: Amount,
    pub available_margin: Amount,
    pub current_pnl: Amount,
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
pub struct PublishPolicyRequest {
    pub policy: RiskPolicy,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResizeReservationRequest {
    pub reservation_id: String,
    pub amount: Amount,
    pub at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReleaseReservationRequest {
    pub reservation_id: String,
    pub at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ConsumeReservationRequest {
    pub reservation_id: String,
    pub at_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AdvanceRiskTimeRequest {
    pub event_time_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AdvanceRiskTimeResponse {
    pub event_time_unix_nanos: u64,
    pub expired: usize,
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
    #[serde(default)]
    pub account_id: Option<String>,
    #[serde(default)]
    pub strategy_id: Option<String>,
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
    pub decision_id: String,
    pub request_id: String,
    pub account_id: String,
    pub strategy_id: String,
    pub instrument_id: String,
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

/// mmap current-state contract. It cannot carry event history or positions.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskCurrentView {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub policy_version: u64,
    pub limits: Vec<LimitView>,
    pub reservations: Vec<Reservation>,
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
    DecisionEvaluated {
        decision: RiskDecision,
        account_id: String,
        strategy_id: String,
        event_sequence: u64,
    },
    CircuitChanged {
        circuit: CircuitState,
        event_sequence: u64,
    },
}
