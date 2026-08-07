//! Provider-neutral order and execution data.

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OrderType {
    Market,
    Limit,
    Stop,
    StopLimit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TimeInForce {
    GoodTilCanceled,
    ImmediateOrCancel,
    FillOrKill,
    Day,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OrderStatus {
    Pending,
    Accepted,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
    Expired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OrderEntryStatus {
    Accepted,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
    Expired,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OrderEntryRequest {
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
    pub options: OrderEntryOptions,
}

/// Provider-neutral order controls. Integrations may ignore an option when a
/// venue does not support it, but they never receive raw vendor parameters.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct OrderEntryOptions {
    pub time_in_force: Option<TimeInForce>,
    pub reduce_only: Option<bool>,
    pub post_only: Option<bool>,
    pub position_side: Option<String>,
    pub quote_asset: Option<String>,
    pub wallet_type: Option<String>,
    pub trading_session: Option<String>,
    pub tokenize: Option<bool>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OrderEntryEvent {
    pub order_id: String,
    pub status: OrderEntryStatus,
    pub venue_order_id: Option<String>,
    pub filled_quantity: Option<DecimalValue>,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct DecimalValue {
    pub mantissa: i64,
    pub scale: u8,
}

impl DecimalValue {
    pub const fn new(mantissa: i64, scale: u8) -> Self {
        Self { mantissa, scale }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OrderRequest {
    pub client_order_id: Option<String>,
    pub symbol: String,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: String,
    pub limit_price: Option<String>,
    pub stop_price: Option<String>,
    pub time_in_force: Option<TimeInForce>,
}

impl OrderRequest {
    pub fn validate(&self) -> Result<(), String> {
        if self.symbol.trim().is_empty() {
            return Err("order symbol is required".into());
        }
        if self.quantity.trim().is_empty() {
            return Err("order quantity is required".into());
        }
        if matches!(self.order_type, OrderType::Limit | OrderType::StopLimit)
            && self
                .limit_price
                .as_deref()
                .unwrap_or_default()
                .trim()
                .is_empty()
        {
            return Err("limit orders require a limit price".into());
        }
        if matches!(self.order_type, OrderType::Stop | OrderType::StopLimit)
            && self
                .stop_price
                .as_deref()
                .unwrap_or_default()
                .trim()
                .is_empty()
        {
            return Err("stop orders require a stop price".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Order {
    pub order_id: String,
    pub client_order_id: Option<String>,
    pub symbol: String,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub status: OrderStatus,
    pub requested_quantity: String,
    pub filled_quantity: String,
    pub average_fill_price: Option<String>,
    pub submitted_at_unix_nanos: Option<u64>,
    pub updated_at_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionReport {
    pub order_id: String,
    pub execution_id: String,
    pub symbol: String,
    pub side: OrderSide,
    pub quantity: String,
    pub price: String,
    pub fee: Option<String>,
    pub fee_asset: Option<String>,
    pub executed_at_unix_nanos: u64,
}

#[cfg(test)]
mod tests {
    use super::{OrderRequest, OrderSide, OrderType};

    #[test]
    fn limit_order_requires_a_limit_price() {
        let order = OrderRequest {
            client_order_id: None,
            symbol: "BTCUSDT".into(),
            side: OrderSide::Buy,
            order_type: OrderType::Limit,
            quantity: "1".into(),
            limit_price: None,
            stop_price: None,
            time_in_force: None,
        };
        assert!(order.validate().is_err());
    }
}
