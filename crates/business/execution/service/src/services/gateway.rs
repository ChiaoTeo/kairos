//! Dedicated order-entry gateway worker.
//!
//! The worker owns the concrete provider connection. ExecutionApplication
//! sees only a provider-neutral proxy, so synchronous SDK/network work does
//! not run on the UDS/Tokio runtime thread.

use kairos_integration::application::{
    Connection, ExternalOrder, ExternalOrderQuery, OrderEntryConnection, OrderQueryConnection,
};
use kairos_integration::domain::{
    ConnectionHealth, ConnectionIdentity, ConnectionLifecycle, ConnectionState,
};
use kairos_integration::{OrderEntryEvent, OrderEntryRequest};
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use std::time::Duration;

enum GatewayRequest {
    Submit {
        request: OrderEntryRequest,
        reply: SyncSender<Result<OrderEntryEvent, String>>,
    },
    Cancel {
        request: OrderEntryRequest,
        venue_order_id: String,
        at_unix_nanos: u64,
        reply: SyncSender<Result<OrderEntryEvent, String>>,
    },
}

pub struct QueuedOrderEntry {
    state: ConnectionState,
    sender: SyncSender<GatewayRequest>,
}

pub struct GatewayWorker {
    connection: Box<dyn OrderEntryConnection>,
    receiver: Receiver<GatewayRequest>,
}

enum QueryGatewayRequest {
    Open {
        query: ExternalOrderQuery,
        reply: SyncSender<Result<Vec<ExternalOrder>, String>>,
    },
    History {
        query: ExternalOrderQuery,
        reply: SyncSender<Result<Vec<ExternalOrder>, String>>,
    },
    Detail {
        query: ExternalOrderQuery,
        reply: SyncSender<Result<Option<ExternalOrder>, String>>,
    },
}

pub struct QueuedOrderQuery {
    state: ConnectionState,
    sender: SyncSender<QueryGatewayRequest>,
}

pub struct QueryGatewayWorker {
    connection: Box<dyn OrderQueryConnection>,
    receiver: Receiver<QueryGatewayRequest>,
}

impl QueuedOrderEntry {
    pub fn channel(
        connection: Box<dyn OrderEntryConnection>,
        capacity: usize,
    ) -> (Self, GatewayWorker) {
        let state = connection.state().clone();
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        (
            Self { state, sender },
            GatewayWorker {
                connection,
                receiver,
            },
        )
    }

    fn request(
        &self,
        request: GatewayRequest,
        reply: Receiver<Result<OrderEntryEvent, String>>,
    ) -> Result<OrderEntryEvent, String> {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut request = Some(request);
        loop {
            match self
                .sender
                .try_send(request.take().expect("gateway request present"))
            {
                Ok(()) => break,
                Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                    return Err("order gateway worker is stopped".into())
                }
                Err(std::sync::mpsc::TrySendError::Full(value)) => {
                    if std::time::Instant::now() >= deadline {
                        return Err("order gateway queue is full".into());
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        reply
            .recv()
            .map_err(|_| "order gateway worker did not respond".to_string())?
    }
}

impl Connection for QueuedOrderEntry {
    fn identity(&self) -> &ConnectionIdentity {
        &self.state.identity
    }

    fn state(&self) -> &ConnectionState {
        &self.state
    }

    fn start(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }

    fn stop(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn reconnect(&mut self) -> Result<(), String> {
        self.start()
    }

    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }
}

impl OrderEntryConnection for QueuedOrderEntry {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> Result<OrderEntryEvent, String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            GatewayRequest::Submit {
                request: request.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        venue_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<OrderEntryEvent, String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            GatewayRequest::Cancel {
                request: request.clone(),
                venue_order_id: venue_order_id.into(),
                at_unix_nanos,
                reply: reply_tx,
            },
            reply_rx,
        )
    }
}

impl GatewayWorker {
    pub fn run(mut self, stop: std::sync::Arc<std::sync::atomic::AtomicBool>) {
        use std::sync::atomic::Ordering;
        if let Err(error) = self.connection.start() {
            tracing::error!(event = "gateway_start_failed", component = "execution", error = %error, "order gateway failed to start");
        }
        while !stop.load(Ordering::Acquire) {
            match self.receiver.recv_timeout(Duration::from_millis(50)) {
                Ok(GatewayRequest::Submit { request, reply }) => {
                    let _ = reply.send(self.connection.submit_order(&request));
                }
                Ok(GatewayRequest::Cancel {
                    request,
                    venue_order_id,
                    at_unix_nanos,
                    reply,
                }) => {
                    let _ = reply.send(self.connection.cancel_order(
                        &request,
                        &venue_order_id,
                        at_unix_nanos,
                    ));
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => break,
            }
        }
        let _ = self.connection.stop();
    }
}

impl QueuedOrderQuery {
    pub fn channel(
        connection: Box<dyn OrderQueryConnection>,
        capacity: usize,
    ) -> (Self, QueryGatewayWorker) {
        let state = connection.state().clone();
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        (
            Self { state, sender },
            QueryGatewayWorker {
                connection,
                receiver,
            },
        )
    }

    fn request<T>(
        &self,
        request: QueryGatewayRequest,
        reply: Receiver<Result<T, String>>,
    ) -> Result<T, String> {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut request = Some(request);
        loop {
            match self
                .sender
                .try_send(request.take().expect("query gateway request present"))
            {
                Ok(()) => break,
                Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                    return Err("order query worker is stopped".into())
                }
                Err(std::sync::mpsc::TrySendError::Full(value)) => {
                    if std::time::Instant::now() >= deadline {
                        return Err("order query queue is full".into());
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        reply
            .recv()
            .map_err(|_| "order query worker did not respond".to_string())?
    }
}

impl Connection for QueuedOrderQuery {
    fn identity(&self) -> &ConnectionIdentity {
        &self.state.identity
    }

    fn state(&self) -> &ConnectionState {
        &self.state
    }

    fn start(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }

    fn stop(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn reconnect(&mut self) -> Result<(), String> {
        self.start()
    }

    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }
}

impl OrderQueryConnection for QueuedOrderQuery {
    fn open_orders(&mut self, query: &ExternalOrderQuery) -> Result<Vec<ExternalOrder>, String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::Open {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn order_history(&mut self, query: &ExternalOrderQuery) -> Result<Vec<ExternalOrder>, String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::History {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::Detail {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }
}

impl QueryGatewayWorker {
    pub fn run(mut self, stop: std::sync::Arc<std::sync::atomic::AtomicBool>) {
        use std::sync::atomic::Ordering;
        if let Err(error) = self.connection.start() {
            tracing::error!(event = "query_gateway_start_failed", component = "execution", error = %error, "order query gateway failed to start");
        }
        while !stop.load(Ordering::Acquire) {
            match self.receiver.recv_timeout(Duration::from_millis(50)) {
                Ok(QueryGatewayRequest::Open { query, reply }) => {
                    let _ = reply.send(self.connection.open_orders(&query));
                }
                Ok(QueryGatewayRequest::History { query, reply }) => {
                    let _ = reply.send(self.connection.order_history(&query));
                }
                Ok(QueryGatewayRequest::Detail { query, reply }) => {
                    let _ = reply.send(self.connection.order_detail(&query));
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => break,
            }
        }
        let _ = self.connection.stop();
    }
}
