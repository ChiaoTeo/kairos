//! Provider-neutral remote order queries.

use crate::application::Connection;
use crate::domain::{DecimalValue, OrderSide, OrderType};

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ExternalOrderQuery {
    pub symbol: Option<String>,
    pub order_id: Option<String>,
    pub limit: Option<u32>,
    pub since_unix_millis: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalOrder {
    pub order_id: String,
    pub client_order_id: Option<String>,
    pub symbol: String,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub status: String,
    pub quantity: DecimalValue,
    pub filled_quantity: DecimalValue,
    pub average_fill_price: Option<DecimalValue>,
    pub occurred_at_unix_millis: Option<u64>,
}

pub trait OrderQueryConnection: Connection {
    fn open_orders(&mut self, query: &ExternalOrderQuery) -> Result<Vec<ExternalOrder>, String>;
    fn order_history(&mut self, query: &ExternalOrderQuery) -> Result<Vec<ExternalOrder>, String>;
    fn order_detail(&mut self, query: &ExternalOrderQuery)
        -> Result<Option<ExternalOrder>, String>;
}
