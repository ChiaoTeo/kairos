//! Provider-neutral execution/order update stream capability.

use crate::application::Connection;
use crate::domain::{DecimalValue, OrderSide, OrderType};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalExecutionEvent {
    pub order_id: String,
    pub symbol: String,
    pub status: String,
    pub side: Option<OrderSide>,
    pub order_type: Option<OrderType>,
    pub quantity: Option<DecimalValue>,
    pub limit_price: Option<DecimalValue>,
    pub filled_quantity: Option<DecimalValue>,
    pub remaining_quantity: Option<DecimalValue>,
    pub fill_quantity: Option<DecimalValue>,
    pub fill_price: Option<DecimalValue>,
    pub execution_id: Option<String>,
    pub fee_currency: Option<String>,
    pub fee_amount: Option<DecimalValue>,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
}

pub trait ExecutionStreamConnection: Connection {
    fn next_execution_event(&mut self) -> Result<Option<ExternalExecutionEvent>, String>;
}
