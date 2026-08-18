//! Participant-neutral order and execution data.

use kairos_primitives::{
    AccountId, ClientOrderId, Currency, DomainTypeError, FillId, InstrumentId, IntentId, MarketId,
    Money, OrderId, Price, Quantity, RemoteOrderId, SegmentKey, Symbol, UnixNanos,
};

use crate::domain::ParticipantInstrumentRef;

pub use kairos_primitives::{OrderSide, OrderStatus};

pub(crate) fn normalize_order_side(value: &str) -> OrderSide {
    match value.to_ascii_uppercase().as_str() {
        "SELL" => OrderSide::Sell,
        _ => OrderSide::Buy,
    }
}

pub(crate) fn normalize_order_status(value: &str) -> OrderStatus {
    match value.to_ascii_uppercase().as_str() {
        "NEW" | "OPEN" | "ACKNOWLEDGED" | "PENDING" => OrderStatus::Acknowledged,
        "SUBMITTED" | "ACCEPTED" => OrderStatus::Accepted,
        "PARTIALLY_FILLED" | "PARTIAL" => OrderStatus::PartiallyFilled,
        "FILLED" | "COMPLETED" => OrderStatus::Filled,
        "CANCELED" | "CANCELLED" => OrderStatus::Canceled,
        "REJECTED" => OrderStatus::Rejected,
        "EXPIRED" => OrderStatus::Expired,
        _ => OrderStatus::Unknown,
    }
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
pub enum OrderEntryStatus {
    Accepted,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
    Expired,
    Unknown,
}

impl From<OrderEntryStatus> for OrderStatus {
    fn from(status: OrderEntryStatus) -> Self {
        match status {
            OrderEntryStatus::Accepted => Self::Accepted,
            OrderEntryStatus::PartiallyFilled => Self::PartiallyFilled,
            OrderEntryStatus::Filled => Self::Filled,
            OrderEntryStatus::Canceled => Self::Canceled,
            OrderEntryStatus::Rejected => Self::Rejected,
            OrderEntryStatus::Expired => Self::Expired,
            OrderEntryStatus::Unknown => Self::Unknown,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OrderEntryRequest {
    pub order_id: OrderId,
    pub intent_id: Option<IntentId>,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    pub participant_instrument: ParticipantInstrumentRef,
    pub side: OrderSide,
    pub quantity: DecimalValue,
    pub order_type: OrderType,
    pub limit_price: Option<DecimalValue>,
    pub options: OrderEntryOptions,
}

/// Participant-neutral order controls. Integrations may ignore an option when a
/// exchange does not support it, but they never receive raw vendor parameters.
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
    pub order_id: OrderId,
    pub status: OrderEntryStatus,
    pub remote_order_id: Option<RemoteOrderId>,
    pub filled_quantity: Option<DecimalValue>,
    pub occurred_at_unix_nanos: UnixNanos,
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

impl TryFrom<DecimalValue> for Quantity {
    type Error = DomainTypeError;

    fn try_from(value: DecimalValue) -> Result<Self, Self::Error> {
        Quantity::new(value.mantissa, value.scale)
    }
}

impl TryFrom<DecimalValue> for Price {
    type Error = DomainTypeError;

    fn try_from(value: DecimalValue) -> Result<Self, Self::Error> {
        Price::new(value.mantissa, value.scale)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OrderRequest {
    pub client_order_id: Option<ClientOrderId>,
    pub symbol: Symbol,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: Quantity,
    pub limit_price: Option<Price>,
    pub stop_price: Option<Price>,
    pub time_in_force: Option<TimeInForce>,
}

impl OrderRequest {
    pub fn validate(&self) -> Result<(), String> {
        if self.quantity.is_zero() {
            return Err("order quantity must be positive".into());
        }
        if matches!(self.order_type, OrderType::Limit | OrderType::StopLimit)
            && self.limit_price.is_none()
        {
            return Err("limit orders require a limit price".into());
        }
        if matches!(self.order_type, OrderType::Stop | OrderType::StopLimit)
            && self.stop_price.is_none()
        {
            return Err("stop orders require a stop price".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Order {
    pub order_id: OrderId,
    pub client_order_id: Option<ClientOrderId>,
    pub symbol: Symbol,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub status: OrderStatus,
    pub requested_quantity: Quantity,
    pub filled_quantity: Quantity,
    pub average_fill_price: Option<Price>,
    pub submitted_at_unix_nanos: Option<UnixNanos>,
    pub updated_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionReport {
    pub order_id: OrderId,
    pub execution_id: FillId,
    pub symbol: Symbol,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub price: Price,
    pub fee: Option<Money>,
    pub fee_asset: Option<Currency>,
    pub executed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ExternalOrderQuery {
    pub symbol: Option<Symbol>,
    pub instrument_type: Option<crate::ParticipantInstrumentTypeRef>,
    pub order_id: Option<OrderId>,
    pub limit: Option<u32>,
    pub since_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalOrder {
    pub connection_key: crate::ConnectionKey,
    pub order_id: OrderId,
    pub client_order_id: Option<ClientOrderId>,
    pub symbol: Symbol,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub status: OrderStatus,
    pub quantity: DecimalValue,
    pub filled_quantity: DecimalValue,
    pub average_fill_price: Option<DecimalValue>,
    pub occurred_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalExecutionEvent {
    pub order_id: OrderId,
    pub symbol: Symbol,
    pub status: OrderStatus,
    pub side: Option<OrderSide>,
    pub order_type: Option<OrderType>,
    pub quantity: Option<DecimalValue>,
    pub limit_price: Option<DecimalValue>,
    pub filled_quantity: Option<DecimalValue>,
    pub remaining_quantity: Option<DecimalValue>,
    pub fill_quantity: Option<DecimalValue>,
    pub fill_price: Option<DecimalValue>,
    pub execution_id: Option<FillId>,
    pub fee_currency: Option<Currency>,
    pub fee_amount: Option<DecimalValue>,
    pub occurred_at_unix_nanos: UnixNanos,
    pub reason: String,
}

#[cfg(test)]
mod tests {
    use super::{OrderRequest, OrderSide, OrderType};
    use kairos_primitives::Symbol;

    #[test]
    fn limit_order_requires_a_limit_price() {
        let order = OrderRequest {
            client_order_id: None,
            symbol: Symbol::new("BTCUSDT").unwrap(),
            side: OrderSide::Buy,
            order_type: OrderType::Limit,
            quantity: "1".parse().unwrap(),
            limit_price: None,
            stop_price: None,
            time_in_force: None,
        };
        assert!(order.validate().is_err());
    }
}
