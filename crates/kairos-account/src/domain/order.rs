use super::DecimalValue;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum OrderSide {
    Buy,
    Sell,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum OrderType {
    Market,
    Limit,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum OrderStatus {
    Planned,
    Reserved,
    Submitting,
    Acknowledged,
    PartiallyFilled,
    Filled,
    CancelRequested,
    Canceled,
    Rejected,
    Expired,
    Unknown,
}
impl OrderStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Planned => "planned",
            Self::Reserved => "reserved",
            Self::Submitting => "submitting",
            Self::Acknowledged => "acknowledged",
            Self::PartiallyFilled => "partially_filled",
            Self::Filled => "filled",
            Self::CancelRequested => "cancel_requested",
            Self::Canceled => "canceled",
            Self::Rejected => "rejected",
            Self::Expired => "expired",
            Self::Unknown => "unknown",
        }
    }

    pub fn terminal(self) -> bool {
        matches!(
            self,
            Self::Filled | Self::Canceled | Self::Rejected | Self::Expired
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OrderRequest {
    pub order_id: String,
    pub intent_id: Option<String>,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub side: OrderSide,
    pub quantity: DecimalValue,
    pub order_type: OrderType,
    pub limit_price: Option<DecimalValue>,
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OrderEvent {
    pub order_id: String,
    pub status: OrderStatus,
    pub venue_order_id: Option<String>,
    pub filled_quantity: Option<DecimalValue>,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OrderState {
    pub request: OrderRequest,
    pub status: OrderStatus,
    pub venue_order_id: Option<String>,
    pub filled_quantity: DecimalValue,
    pub updated_at_unix_nanos: u64,
    pub reason: String,
}

impl OrderState {
    pub fn new(request: OrderRequest, at: u64) -> Result<Self, String> {
        if request.order_id.trim().is_empty()
            || request.account_id.trim().is_empty()
            || request.segment_key.trim().is_empty()
            || request.instrument_id.trim().is_empty()
        {
            return Err("order identity and account scope are required".into());
        }
        if request.quantity.mantissa <= 0 {
            return Err("order quantity must be positive".into());
        }
        if request.order_type == OrderType::Limit && request.limit_price.is_none() {
            return Err("limit order requires limit_price".into());
        }
        Ok(Self {
            filled_quantity: DecimalValue::new(0, request.quantity.scale),
            request,
            status: OrderStatus::Planned,
            venue_order_id: None,
            updated_at_unix_nanos: at,
            reason: String::new(),
        })
    }
    pub fn apply(&mut self, event: OrderEvent) -> Result<(), String> {
        if event.order_id != self.request.order_id {
            return Err("order event identity mismatch".into());
        }
        if self.status.terminal() {
            return Err("cannot update terminal order".into());
        }
        if let Some(quantity) = event.filled_quantity {
            if quantity.scale != self.request.quantity.scale
                || quantity.mantissa < self.filled_quantity.mantissa
                || quantity.mantissa > self.request.quantity.mantissa
            {
                return Err("invalid cumulative filled quantity".into());
            }
            self.filled_quantity = quantity;
        }
        self.status = event.status;
        self.venue_order_id = event.venue_order_id.or_else(|| self.venue_order_id.clone());
        self.updated_at_unix_nanos = event.occurred_at_unix_nanos;
        self.reason = event.reason;
        Ok(())
    }
}
