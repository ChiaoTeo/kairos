//! Empty async capability types used when a process has no configured live gateway.

use super::*;
use kairos_integration::application::{
    AsyncOrderEntryConnection, AsyncOrderEventSource, AsyncOrderQueryConnection, CommandOutcome,
    ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery,
    IntegrationError, OrderEntryEvent, OrderEntryRequest,
};

impl<E, Q, S> ExecutionProcess<E, Q, S> {
    pub(super) fn start_gateway_worker(
        &mut self,
        stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
    ) -> Option<std::thread::JoinHandle<()>> {
        let connection = self.application.take_order_entry()?;
        let (proxy, worker) = QueuedOrderEntry::channel(connection, 256);
        self.application.install_order_entry(Box::new(proxy));
        Some(std::thread::spawn(move || worker.run(stop)))
    }

    pub(super) fn start_async_gateway_worker(
        &mut self,
        shutdown: tokio::sync::watch::Receiver<bool>,
    ) -> Option<tokio::task::JoinHandle<()>>
    where
        E: AsyncOrderEntryConnection + 'static,
    {
        let connection = self.async_order_entry.take()?;
        // The server path uses the async capability. Drop the synchronous
        // projection installed during composition so there is exactly one
        // active command lane for this route.
        drop(self.application.take_order_entry());
        let (proxy, worker) = AsyncQueuedOrderEntry::channel(connection, 256);
        self.application.install_order_entry(Box::new(proxy));
        Some(tokio::spawn(worker.run(shutdown)))
    }

    pub(super) fn start_query_gateway_worker(
        &mut self,
        stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
    ) -> Option<std::thread::JoinHandle<()>> {
        let connection = self.application.take_order_query()?;
        let (proxy, worker) = QueuedOrderQuery::channel(connection, 128);
        self.application.install_order_query(Box::new(proxy));
        Some(std::thread::spawn(move || worker.run(stop)))
    }

    pub(super) fn start_async_query_gateway_worker(
        &mut self,
        shutdown: tokio::sync::watch::Receiver<bool>,
    ) -> Option<tokio::task::JoinHandle<()>>
    where
        Q: AsyncOrderQueryConnection + 'static,
    {
        let connection = self.async_order_query.take()?;
        drop(self.application.take_order_query());
        let (proxy, worker) = AsyncQueuedOrderQuery::channel(connection, 128);
        self.application.install_order_query(Box::new(proxy));
        Some(tokio::spawn(worker.run(shutdown)))
    }
}

pub struct NoAsyncOrderEntryConnection;

impl AsyncOrderEntryConnection for NoAsyncOrderEntryConnection {
    async fn submit_order(
        &mut self,
        _request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Err(IntegrationError::NotReady)
    }

    async fn cancel_order(
        &mut self,
        _request: &OrderEntryRequest,
        _remote_order_id: &str,
        _at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Err(IntegrationError::NotReady)
    }
}

pub struct NoAsyncOrderQueryConnection;

impl AsyncOrderQueryConnection for NoAsyncOrderQueryConnection {
    async fn open_orders(
        &mut self,
        _query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        Err(IntegrationError::NotReady)
    }

    async fn order_history(
        &mut self,
        _query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        Err(IntegrationError::NotReady)
    }

    async fn order_detail(
        &mut self,
        _query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        Err(IntegrationError::NotReady)
    }
}

pub struct NoAsyncOrderEventSource;

impl AsyncOrderEventSource for NoAsyncOrderEventSource {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        Err(IntegrationError::NotReady)
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        Ok(())
    }

    fn channel_health(&self) -> kairos_integration::application::ConnectionHealth {
        kairos_integration::application::ConnectionHealth {
            lifecycle: kairos_integration::application::ConnectionLifecycle::Stopped,
            healthy: false,
            authenticated: false,
            last_error: None,
        }
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        Err(IntegrationError::NotReady)
    }
}
