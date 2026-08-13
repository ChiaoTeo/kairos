use std::cmp::Ordering;

use kairos_domain_types::{
    AccountId, BasisPoints, DurationNanos, Exchange, Generation, IdempotencyKey, InstrumentId,
    Money, PolicyId, RequestId, ReservationId, Sequence, StrategyId, UnixNanos,
};
use rust_decimal::Decimal as RustDecimal;
use serde::{Deserialize, Serialize};

/// Risk quantities are non-negative fixed-point values.  The wire contract
/// remains i64-compatible, while arithmetic is performed through Decimal so
/// values with different scales cannot be accidentally compared as integers.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Amount {
    mantissa: i64,
    scale: u8,
}

impl Amount {
    pub const ZERO: Self = Self {
        mantissa: 0,
        scale: 0,
    };

    pub fn new(mantissa: i64, scale: u8) -> Result<Self, String> {
        if mantissa < 0 || scale > 18 {
            return Err("risk amounts cannot be negative".into());
        }
        let value = RustDecimal::try_new(mantissa, u32::from(scale))
            .map_err(|_| "risk amount overflow".to_string())?
            .normalize();
        Self::from_decimal(value)
    }

    pub fn checked_add(self, other: Self) -> Result<Self, String> {
        let value = self
            .as_decimal()?
            .checked_add(other.as_decimal()?)
            .ok_or_else(|| "risk amount overflow".to_string())?;
        Self::from_decimal(value)
    }

    pub fn checked_sub(self, other: Self) -> Result<Self, String> {
        let value = self
            .as_decimal()?
            .checked_sub(other.as_decimal()?)
            .ok_or_else(|| "risk amount underflow".to_string())?;
        if value.is_sign_negative() {
            return Err("risk amount cannot become negative".into());
        }
        Self::from_decimal(value)
    }

    pub fn cmp_value(self, other: Self) -> Ordering {
        self.as_decimal()
            .and_then(|left| other.as_decimal().map(|right| left.cmp(&right)))
            .unwrap_or_else(|_| self.mantissa.cmp(&other.mantissa))
    }

    fn as_decimal(self) -> Result<RustDecimal, String> {
        RustDecimal::try_new(self.mantissa, u32::from(self.scale))
            .map_err(|_| "risk amount overflow".to_string())
    }

    fn from_decimal(value: RustDecimal) -> Result<Self, String> {
        let mantissa =
            i64::try_from(value.mantissa()).map_err(|_| "risk amount overflow".to_string())?;
        let scale = u8::try_from(value.scale()).map_err(|_| "risk amount overflow".to_string())?;
        Ok(Self { mantissa, scale })
    }
}

impl Serialize for Amount {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        serializer.serialize_str(
            &self
                .as_decimal()
                .map_err(serde::ser::Error::custom)?
                .to_string(),
        )
    }
}

impl<'de> Deserialize<'de> for Amount {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        let value = RustDecimal::from_str_exact(&value).map_err(serde::de::Error::custom)?;
        if value.is_sign_negative() {
            return Err(serde::de::Error::custom("risk amounts cannot be negative"));
        }
        Self::from_decimal(value.normalize()).map_err(serde::de::Error::custom)
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
    pub fn as_str(self) -> &'static str {
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

/// Facts owned by other modules and supplied to a risk decision. Risk stores
/// the watermarks and decision facts, but never becomes the owner of these
/// balances, positions, or market observations.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskContext {
    pub account_snapshot_watermark: UnixNanos,
    pub market_freshness_watermark: UnixNanos,
    pub portfolio_version: Generation,
    pub current_exposure: Amount,
    pub current_margin: Amount,
    pub available_margin: Amount,
    pub current_pnl: Money,
    pub current_drawdown: Amount,
    pub market_is_fresh: bool,
    pub leverage_bps: BasisPoints,
    pub price_deviation_bps: BasisPoints,
    pub stress_loss: Amount,
}

impl Default for RiskContext {
    fn default() -> Self {
        Self {
            account_snapshot_watermark: UnixNanos::new(0),
            market_freshness_watermark: UnixNanos::new(0),
            portfolio_version: Generation::new(0),
            current_exposure: Amount::ZERO,
            current_margin: Amount::ZERO,
            available_margin: Amount::ZERO,
            current_pnl: Money::default(),
            current_drawdown: Amount::ZERO,
            market_is_fresh: true,
            leverage_bps: BasisPoints::new(0),
            price_deviation_bps: BasisPoints::new(0),
            stress_loss: Amount::ZERO,
        }
    }
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

impl PolicyScope {
    pub fn matches(&self, request: &AuthorizeRequest) -> bool {
        self.account_id
            .as_deref()
            .is_none_or(|v| request.account_id == v)
            && self
                .strategy_id
                .as_deref()
                .is_none_or(|v| request.strategy_id == v)
            && self
                .instrument_id
                .as_deref()
                .is_none_or(|v| request.instrument_id == v)
            && self
                .exchange_id
                .as_deref()
                .is_none_or(|v| request.exchange_id == v)
    }
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

impl RiskPolicy {
    pub fn validate(&self) -> Result<(), String> {
        if self.version.get() == 0 {
            return Err("policy_id and positive version are required".into());
        }
        if self
            .valid_until_unix_nanos
            .is_some_and(|until| until <= self.valid_from_unix_nanos)
        {
            return Err("policy validity interval is inverted".into());
        }
        if self.window_nanos.is_some_and(|window| window.get() == 0) {
            return Err("policy window must be positive".into());
        }
        if self.limit == Amount::ZERO {
            return Err("policy limit must be positive".into());
        }
        Ok(())
    }

    pub fn active_at(&self, at: UnixNanos) -> bool {
        at >= self.valid_from_unix_nanos
            && self.valid_until_unix_nanos.is_none_or(|until| at < until)
    }
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
    pub metric: Metric,
    pub amount: Amount,
    pub at_unix_nanos: UnixNanos,
    pub reservation_ttl_nanos: DurationNanos,
    pub dependency_generation: Generation,
    pub dependency_event_sequence: Sequence,
    #[serde(default)]
    pub context: Option<RiskContext>,
}

impl AuthorizeRequest {
    pub fn validate(&self) -> Result<(), String> {
        if self.amount == Amount::ZERO || self.reservation_ttl_nanos.get() == 0 {
            return Err("amount and reservation TTL must be positive".into());
        }
        Ok(())
    }
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
    pub idempotency_key: IdempotencyKey,
    pub allocations: Vec<Allocation>,
    pub status: ReservationStatus,
    pub created_at_unix_nanos: UnixNanos,
    pub updated_at_unix_nanos: UnixNanos,
    pub expires_at_unix_nanos: UnixNanos,
    pub policy_version: Generation,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DependencyWatermarks {
    pub generation: Generation,
    pub event_sequence: Sequence,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
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
pub struct CircuitScope {
    pub account_id: Option<AccountId>,
    pub strategy_id: Option<StrategyId>,
    pub exchange_id: Option<Exchange>,
}

impl CircuitScope {
    pub fn matches(&self, request: &AuthorizeRequest) -> bool {
        self.account_id
            .as_deref()
            .is_none_or(|v| request.account_id == v)
            && self
                .strategy_id
                .as_deref()
                .is_none_or(|v| request.strategy_id == v)
            && self
                .exchange_id
                .as_deref()
                .is_none_or(|v| request.exchange_id == v)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CircuitState {
    pub scope: CircuitScope,
    pub open: bool,
    pub opened_at_unix_nanos: Option<UnixNanos>,
    pub reset_at_unix_nanos: Option<UnixNanos>,
    pub reason: String,
}

impl CircuitState {
    pub fn blocks_at(&self, at_unix_nanos: UnixNanos) -> bool {
        self.open
            && self
                .reset_at_unix_nanos
                .is_none_or(|reset_at| at_unix_nanos < reset_at)
    }
}
