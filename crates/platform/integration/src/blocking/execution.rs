//! Synchronous order and execution capabilities.

use std::time::Duration;

use crate::{
    CommandResult, ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder,
    ExternalOrderQuery, IntegrationError, OrderEntryEvent, OrderEntryRequest,
};

pub trait OrderCommand: Send {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> CommandResult<OrderEntryEvent>;
    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent>;
}

pub trait OrderQuery: Send {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError>;
    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError>;
    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError>;
}

pub trait ExecutionStream: Send {
    fn next(
        &mut self,
        timeout: Duration,
    ) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError>;
}
