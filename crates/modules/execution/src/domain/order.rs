use serde::{Deserialize, Serialize};

pub use kairos_primitives::IntentId;
pub use kairos_primitives::{
    AccountId, Currency, ExecutionAccessId, FillId, InstrumentId, LegId, MarketId, Money, OrderId,
    OrderSide, PlanId, Price, Quantity, RemoteOrderId, SegmentKey, UnixNanos,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum OrderType {
    Market,
    Limit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExecutionOrderStatus {
    Pending,
    Submitting,
    Accepted,
    PartiallyFilled,
    Filled,
    CancelRequested,
    Canceled,
    Rejected,
    Expired,
    Unknown,
    Failed,
}

impl ExecutionOrderStatus {
    pub fn terminal(self) -> bool {
        matches!(
            self,
            Self::Filled | Self::Canceled | Self::Rejected | Self::Expired
        )
    }
}

impl From<ExecutionOrderStatus> for kairos_primitives::OrderStatus {
    fn from(status: ExecutionOrderStatus) -> Self {
        match status {
            ExecutionOrderStatus::Pending | ExecutionOrderStatus::Submitting => Self::Pending,
            ExecutionOrderStatus::Accepted => Self::Accepted,
            ExecutionOrderStatus::PartiallyFilled => Self::PartiallyFilled,
            ExecutionOrderStatus::Filled => Self::Filled,
            ExecutionOrderStatus::CancelRequested | ExecutionOrderStatus::Canceled => {
                Self::Canceled
            }
            ExecutionOrderStatus::Rejected | ExecutionOrderStatus::Failed => Self::Rejected,
            ExecutionOrderStatus::Expired => Self::Expired,
            ExecutionOrderStatus::Unknown => Self::Unknown,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionOrder {
    pub order_id: OrderId,
    #[serde(default)]
    pub plan_id: Option<PlanId>,
    #[serde(default)]
    pub leg_id: Option<LegId>,
    pub intent_id: Option<IntentId>,
    #[serde(default)]
    pub strategy_id: Option<kairos_primitives::StrategyId>,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    #[serde(default)]
    pub execution_access_id: Option<ExecutionAccessId>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: Quantity,
    pub limit_price: Option<Price>,
    pub remote_order_id: Option<RemoteOrderId>,
    pub filled_quantity: Quantity,
    pub status: ExecutionOrderStatus,
    pub submitted_at_unix_nanos: UnixNanos,
    pub updated_at_unix_nanos: UnixNanos,
    pub reason: String,
}

impl ExecutionOrder {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        order_id: impl Into<String>,
        account_id: impl Into<String>,
        segment_key: impl Into<String>,
        instrument_id: impl Into<String>,
        side: OrderSide,
        order_type: OrderType,
        quantity: Quantity,
        at_unix_nanos: u64,
    ) -> Result<Self, String> {
        let order = Self {
            order_id: OrderId::new(order_id).map_err(|error| error.to_string())?,
            plan_id: None,
            leg_id: None,
            intent_id: None,
            strategy_id: None,
            account_id: AccountId::new(account_id).map_err(|error| error.to_string())?,
            segment_key: SegmentKey::new(segment_key.into()).map_err(|error| error.to_string())?,
            instrument_id: InstrumentId::new(instrument_id.into())
                .map_err(|error| error.to_string())?,
            market_id: None,
            execution_access_id: None,
            side,
            order_type,
            quantity,
            limit_price: None,
            remote_order_id: None,
            filled_quantity: Quantity::ZERO,
            status: ExecutionOrderStatus::Pending,
            submitted_at_unix_nanos: UnixNanos::new(at_unix_nanos),
            updated_at_unix_nanos: UnixNanos::new(at_unix_nanos),
            reason: String::new(),
        };
        if quantity <= Quantity::ZERO {
            return Err("execution order quantity must be positive".into());
        }
        Ok(order)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionFill {
    pub fill_id: FillId,
    pub order_id: OrderId,
    #[serde(default)]
    pub plan_id: Option<PlanId>,
    #[serde(default)]
    pub leg_id: Option<LegId>,
    pub intent_id: Option<IntentId>,
    pub instrument_id: InstrumentId,
    /// The canonical Market where this fill actually occurred. For smart/SOR
    /// routes this may differ between fills of one order and is intentionally
    /// distinct from the order's requested/pricing market.
    #[serde(default)]
    pub execution_market_id: Option<MarketId>,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub price: Price,
    pub fee: Money,
    /// Currency in which the provider charged the fee.
    #[serde(default)]
    pub fee_currency: Option<Currency>,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CommitmentResource {
    Asset(Currency),
    Instrument(InstrumentId),
    MarginNotional(Currency),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CommitmentBasis {
    QuotePriceCap {
        price_cap: Price,
    },
    BaseQuantity,
    ContractNotional {
        price_cap: Price,
        contract_size: Quantity,
    },
    SimulationQuantity,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CommitmentStatus {
    HeldBeforeSend,
    Active,
    Uncertain,
    Reduced,
    Released,
    Reconciled,
}

impl CommitmentStatus {
    pub fn consumes_capacity(self) -> bool {
        matches!(
            self,
            Self::HeldBeforeSend | Self::Active | Self::Uncertain | Self::Reduced
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OrderCommitment {
    pub order_id: OrderId,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub side: OrderSide,
    pub resource: CommitmentResource,
    pub amount: Money,
    pub remaining_quantity: Quantity,
    pub status: CommitmentStatus,
    pub basis: CommitmentBasis,
    #[serde(default)]
    pub settlement_asset: Option<Currency>,
    pub updated_at_unix_nanos: UnixNanos,
}

impl OrderCommitment {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        order_id: OrderId,
        account_id: AccountId,
        segment_key: SegmentKey,
        instrument_id: InstrumentId,
        side: OrderSide,
        resource: CommitmentResource,
        amount: Money,
        remaining_quantity: Quantity,
        basis: CommitmentBasis,
        updated_at_unix_nanos: UnixNanos,
    ) -> Result<Self, String> {
        if amount <= Money::ZERO || remaining_quantity <= Quantity::ZERO {
            return Err("order commitment amount and quantity must be positive".into());
        }
        Ok(Self {
            order_id,
            account_id,
            segment_key,
            instrument_id,
            side,
            resource,
            amount,
            remaining_quantity,
            status: CommitmentStatus::HeldBeforeSend,
            basis,
            settlement_asset: None,
            updated_at_unix_nanos,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum RiskReservationSagaStatus {
    AuthorizePending,
    Active,
    ResizePending,
    ReleasePending,
    ConsumePending,
    Released,
    Consumed,
    Expired,
    Failed,
    Uncertain,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RiskReservationEvidence {
    pub order_id: OrderId,
    pub reservation_id: String,
    pub idempotency_key: String,
    pub account_id: AccountId,
    pub amount: Money,
    pub status: RiskReservationSagaStatus,
    pub risk_generation: u64,
    pub risk_event_sequence: u64,
    pub policy_version: u64,
    pub expires_at_unix_nanos: UnixNanos,
    pub updated_at_unix_nanos: UnixNanos,
}
