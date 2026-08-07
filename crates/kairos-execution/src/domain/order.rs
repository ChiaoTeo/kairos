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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionOrder {
    pub order_id: String,
    pub intent_id: Option<String>,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity_mantissa: i64,
    pub quantity_scale: u8,
    pub limit_price_mantissa: Option<i64>,
    pub limit_price_scale: Option<u8>,
    pub venue_order_id: Option<String>,
    #[serde(default)]
    pub filled_quantity_mantissa: i64,
    #[serde(default)]
    pub filled_quantity_scale: u8,
    pub status: ExecutionOrderStatus,
    pub submitted_at_unix_nanos: u64,
    pub updated_at_unix_nanos: u64,
    pub reason: String,
}

impl ExecutionOrder {
    pub fn new(
        order_id: impl Into<String>,
        account_id: impl Into<String>,
        segment_key: impl Into<String>,
        instrument_id: impl Into<String>,
        side: OrderSide,
        order_type: OrderType,
        quantity_mantissa: i64,
        quantity_scale: u8,
        at_unix_nanos: u64,
    ) -> Result<Self, String> {
        let order = Self {
            order_id: order_id.into(),
            intent_id: None,
            account_id: account_id.into(),
            segment_key: segment_key.into(),
            instrument_id: instrument_id.into(),
            market_id: None,
            side,
            order_type,
            quantity_mantissa,
            quantity_scale,
            limit_price_mantissa: None,
            limit_price_scale: None,
            venue_order_id: None,
            filled_quantity_mantissa: 0,
            filled_quantity_scale: quantity_scale,
            status: ExecutionOrderStatus::Pending,
            submitted_at_unix_nanos: at_unix_nanos,
            updated_at_unix_nanos: at_unix_nanos,
            reason: String::new(),
        };
        if order.order_id.trim().is_empty()
            || order.account_id.trim().is_empty()
            || order.segment_key.trim().is_empty()
            || order.instrument_id.trim().is_empty()
        {
            return Err("execution order identity is required".into());
        }
        if quantity_mantissa <= 0 {
            return Err("execution order quantity must be positive".into());
        }
        Ok(order)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionFill {
    pub fill_id: String,
    pub order_id: String,
    pub intent_id: Option<String>,
    pub instrument_id: String,
    pub side: OrderSide,
    pub quantity_mantissa: i64,
    pub quantity_scale: u8,
    pub price_mantissa: i64,
    pub price_scale: u8,
    #[serde(default)]
    pub fee_mantissa: i64,
    #[serde(default)]
    pub fee_scale: u8,
    pub occurred_at_unix_nanos: u64,
}
