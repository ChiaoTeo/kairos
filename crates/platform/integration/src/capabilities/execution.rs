//! Async order entry, order query, and execution stream capabilities.

use std::future::Future;
use std::task::{Context, Poll};

use crate::domain::{
    ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery,
    OrderEntryEvent, OrderEntryRequest,
};
use crate::{CommandResult, IntegrationError};

pub trait OrderCommand: Send {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> impl Future<Output = CommandResult<OrderEntryEvent>> + Send;
    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> impl Future<Output = CommandResult<OrderEntryEvent>> + Send;
}

pub trait OrderQuery: Send {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> impl Future<Output = Result<Vec<ExternalOrder>, IntegrationError>> + Send;
    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> impl Future<Output = Result<Vec<ExternalOrder>, IntegrationError>> + Send;
    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> impl Future<Output = Result<Option<ExternalOrder>, IntegrationError>> + Send;
}

pub trait ExecutionStream: Send {
    fn poll_next(
        &mut self,
        cx: &mut Context<'_>,
    ) -> Poll<Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError>>;
}
