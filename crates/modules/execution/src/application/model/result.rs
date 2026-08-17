//! Public Execution results and persisted reconciliation records.

use super::*;

/// A exchange order observed through a private stream or remote query that could
/// not be associated with a locally submitted order.  It is deliberately
/// persisted instead of being discarded or treated as a transient gateway
/// error: recovery must be able to inspect and resolve it after a restart.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct UnknownRemoteOrder {
    pub remote_order_id: kairos_primitives::RemoteOrderId,
    pub symbol: Symbol,
    pub status: ExecutionOrderStatus,
    pub execution_id: Option<FillId>,
    pub fill_quantity: Option<Quantity>,
    pub fill_price: Option<Price>,
    pub fee_currency: Option<Currency>,
    pub fee_amount: Option<Money>,
    pub first_seen_at_unix_nanos: UnixNanos,
    pub last_seen_at_unix_nanos: UnixNanos,
    pub resolution: UnknownRemoteOrderResolution,
    pub reason: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum UnknownRemoteOrderResolution {
    Pending,
    LinkedToLocalOrder,
    ImportedAsExternalOrder,
    ManualReview,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RemoteOrder {
    pub binding_id: String,
    pub order_id: OrderId,
    pub client_order_id: Option<ClientOrderId>,
    pub symbol: Symbol,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub status: ExecutionOrderStatus,
    pub quantity: Quantity,
    pub filled_quantity: Quantity,
    pub average_fill_price: Option<Price>,
    pub occurred_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Deserialize)]
struct RemoteOrderWire {
    #[serde(default)]
    binding_id: String,
    order_id: String,
    client_order_id: Option<String>,
    symbol: String,
    side: OrderSide,
    order_type: OrderType,
    status: String,
    quantity: String,
    filled_quantity: String,
    average_fill_price: Option<String>,
    occurred_at_unix_millis: Option<u64>,
}

impl<'de> Deserialize<'de> for RemoteOrder {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let wire = RemoteOrderWire::deserialize(deserializer)?;
        Ok(Self {
            binding_id: wire.binding_id,
            order_id: OrderId::new(wire.order_id).map_err(serde::de::Error::custom)?,
            client_order_id: wire
                .client_order_id
                .map(ClientOrderId::new)
                .transpose()
                .map_err(serde::de::Error::custom)?,
            symbol: Symbol::new(wire.symbol).map_err(serde::de::Error::custom)?,
            side: wire.side,
            order_type: wire.order_type,
            status: remote_status(&wire.status),
            quantity: wire.quantity.parse().map_err(serde::de::Error::custom)?,
            filled_quantity: wire
                .filled_quantity
                .parse()
                .map_err(serde::de::Error::custom)?,
            average_fill_price: wire
                .average_fill_price
                .map(|value| value.parse())
                .transpose()
                .map_err(serde::de::Error::custom)?,
            occurred_at_unix_nanos: wire
                .occurred_at_unix_millis
                .map(|value| UnixNanos::from(value.saturating_mul(1_000_000))),
        })
    }
}

impl Serialize for RemoteOrder {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        #[derive(Serialize)]
        struct Wire<'a> {
            binding_id: &'a str,
            order_id: &'a str,
            client_order_id: Option<&'a str>,
            symbol: &'a str,
            side: OrderSide,
            order_type: OrderType,
            status: &'a str,
            quantity: String,
            filled_quantity: String,
            average_fill_price: Option<String>,
            occurred_at_unix_millis: Option<u64>,
        }
        let status = match self.status {
            ExecutionOrderStatus::Pending => "pending",
            ExecutionOrderStatus::Accepted => "accepted",
            ExecutionOrderStatus::PartiallyFilled => "partially_filled",
            ExecutionOrderStatus::Filled => "filled",
            ExecutionOrderStatus::Canceled => "canceled",
            ExecutionOrderStatus::Rejected => "rejected",
            ExecutionOrderStatus::Expired => "expired",
            ExecutionOrderStatus::Submitting => "submitting",
            ExecutionOrderStatus::CancelRequested => "cancel_requested",
            ExecutionOrderStatus::Unknown => "unknown",
            ExecutionOrderStatus::Failed => "failed",
        };
        Wire {
            binding_id: &self.binding_id,
            order_id: self.order_id.as_str(),
            client_order_id: self.client_order_id.as_ref().map(ClientOrderId::as_str),
            symbol: self.symbol.as_str(),
            side: self.side,
            order_type: self.order_type,
            status,
            quantity: self.quantity.to_string(),
            filled_quantity: self.filled_quantity.to_string(),
            average_fill_price: self.average_fill_price.map(|value| value.to_string()),
            occurred_at_unix_millis: self
                .occurred_at_unix_nanos
                .map(|value| value.get() / 1_000_000),
        }
        .serialize(serializer)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentStatus {
    Accepted,
    Planning,
    Planned,
    Executing,
    PartiallyFilled,
    CancelRequested,
    Satisfied,
    Rejected,
    Canceled,
    Expired,
    Failed,
    Compensating,
    ReconciliationRequired,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentState {
    pub intent: ExecuteStrategyIntent,
    pub status: IntentStatus,
    pub order_ids: Vec<OrderId>,
    #[serde(default)]
    pub plan: Option<ExecutionPlan>,
    pub completed_quantity: Quantity,
    pub updated_at_unix_nanos: UnixNanos,
    pub reason: String,
    #[serde(default)]
    pub dependency_watermarks: DependencyWatermarks,
    #[serde(default)]
    pub pending_orders: Vec<SubmitOrder>,
    #[serde(default)]
    pub pending_order_due_unix_nanos: BTreeMap<OrderId, UnixNanos>,
    #[serde(default)]
    pub quote_version: u64,
    #[serde(default)]
    pub last_quote_refresh_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub compensation_attempts: u32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct HedgeRequirement {
    pub intent_id: IntentId,
    pub leader_leg_id: LegId,
    pub hedge_leg_id: LegId,
    pub leader_filled_quantity: Quantity,
    pub hedge_filled_quantity: Quantity,
    pub required_hedge_quantity: Quantity,
    pub unhedged_quantity: Quantity,
    pub max_unhedged_quantity: Quantity,
    pub within_tolerance: bool,
    pub compensation_attempts: u32,
    pub max_compensation_attempts: u32,
}

pub(crate) fn remote_status(value: &str) -> ExecutionOrderStatus {
    let normalized = value.to_ascii_lowercase();
    if normalized.contains("partial") && normalized.contains("fill") {
        ExecutionOrderStatus::PartiallyFilled
    } else if normalized.contains("fill") {
        ExecutionOrderStatus::Filled
    } else if normalized.contains("cancel") {
        ExecutionOrderStatus::Canceled
    } else if normalized.contains("reject") {
        ExecutionOrderStatus::Rejected
    } else if normalized.contains("expire") {
        ExecutionOrderStatus::Expired
    } else if normalized.contains("submit") || normalized.contains("accept") {
        ExecutionOrderStatus::Accepted
    } else {
        ExecutionOrderStatus::Unknown
    }
}
