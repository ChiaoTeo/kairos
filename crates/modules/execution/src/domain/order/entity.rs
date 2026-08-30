//! Execution order entity and lifecycle status.

use super::*;

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

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum OrderReconciliationCause {
    DeliveryIndeterminate,
    AuthoritativeFactConflict,
}

/// Ordering evidence attached to a normalized private execution fact.
/// Sequence is scoped by the physical connection channel and its reconnect
/// epoch; it is not treated as a venue-global order sequence.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OrderFactCursor {
    pub connection_id: kairos_primitives::integration::IntegrationSourceId,
    pub channel_id: kairos_primitives::execution::ExecutionChannelCode,
    pub channel_epoch: kairos_primitives::time::Generation,
    pub participant_sequence: Option<Sequence>,
}

impl OrderFactCursor {
    pub fn regresses(&self, previous: &Self) -> bool {
        if self.connection_id != previous.connection_id || self.channel_id != previous.channel_id {
            return false;
        }
        if self.channel_epoch != previous.channel_epoch {
            return self.channel_epoch < previous.channel_epoch;
        }
        match (previous.participant_sequence, self.participant_sequence) {
            (Some(previous), Some(incoming)) => incoming < previous,
            _ => false,
        }
    }
}

impl From<ExecutionOrderStatus> for kairos_primitives::integration::OrderStatus {
    fn from(status: ExecutionOrderStatus) -> Self {
        match status {
            ExecutionOrderStatus::Pending | ExecutionOrderStatus::Submitting => Self::Pending,
            ExecutionOrderStatus::Accepted => Self::Accepted,
            ExecutionOrderStatus::PartiallyFilled => Self::PartiallyFilled,
            ExecutionOrderStatus::Filled => Self::Filled,
            ExecutionOrderStatus::CancelRequested | ExecutionOrderStatus::Canceled => {
                Self::Canceled
            },
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
    pub strategy_id: Option<kairos_primitives::runtime::StrategyId>,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    #[serde(default)]
    pub execution_route_id: Option<ExecutionRouteId>,
    /// Complete route facts durably selected before provider submission.
    #[serde(default)]
    pub selected_route: Option<SelectedExecutionRoute>,
    #[serde(default)]
    pub attempts: Vec<ExecutionAttempt>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: Quantity,
    pub limit_price: Option<Price>,
    pub remote_order_id: Option<RemoteOrderId>,
    pub filled_quantity: Quantity,
    pub status: ExecutionOrderStatus,
    #[serde(default)]
    pub reconciliation_cause: Option<OrderReconciliationCause>,
    #[serde(default)]
    pub last_order_fact_cursor: Option<OrderFactCursor>,
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
            execution_route_id: None,
            selected_route: None,
            attempts: Vec::new(),
            side,
            order_type,
            quantity,
            limit_price: None,
            remote_order_id: None,
            filled_quantity: Quantity::ZERO,
            status: ExecutionOrderStatus::Pending,
            reconciliation_cause: None,
            last_order_fact_cursor: None,
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
