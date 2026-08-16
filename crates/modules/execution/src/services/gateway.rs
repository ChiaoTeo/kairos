//! Dedicated order-entry gateway worker.
//!
//! The worker owns the concrete provider connection. ExecutionApplication
//! sees only a provider-neutral proxy, so synchronous SDK/network work does
//! not run on the UDS/Tokio runtime thread.

use kairos_integration::application::{
    AsyncOrderEntryConnection, AsyncOrderEventSource, AsyncOrderQueryConnection, CommandOutcome,
    ConnectionHealth, ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder,
    ExternalOrderQuery, IntegrationError, OrderEntryEvent, OrderEntryRequest,
};
use kairos_integration::blocking::{OrderEntryConnection, OrderEventSource, OrderQueryConnection};
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use std::time::Duration;

enum GatewayRequest {
    Submit {
        request: OrderEntryRequest,
        reply: SyncSender<Result<CommandOutcome<OrderEntryEvent>, IntegrationError>>,
    },
    Cancel {
        request: OrderEntryRequest,
        remote_order_id: String,
        at_unix_nanos: u64,
        reply: SyncSender<Result<CommandOutcome<OrderEntryEvent>, IntegrationError>>,
    },
}

pub struct QueuedOrderEntry {
    sender: SyncSender<GatewayRequest>,
}

pub struct GatewayWorker {
    connection: Box<dyn OrderEntryConnection>,
    receiver: Receiver<GatewayRequest>,
}

/// Synchronous application proxy backed by an async provider connection.
///
/// Execution's state owner remains synchronous and single-threaded, while the
/// provider future is polled by the business process Tokio runtime. The proxy
/// blocks only the state-owner thread waiting for its command result; it never
/// creates or owns a runtime.
pub struct AsyncQueuedOrderEntry {
    sender: tokio::sync::mpsc::Sender<GatewayRequest>,
}

pub struct AsyncGatewayWorker<C> {
    connection: C,
    receiver: tokio::sync::mpsc::Receiver<GatewayRequest>,
}

enum QueryGatewayRequest {
    Open {
        query: ExternalOrderQuery,
        reply: SyncSender<Result<Vec<ExternalOrder>, IntegrationError>>,
    },
    History {
        query: ExternalOrderQuery,
        reply: SyncSender<Result<Vec<ExternalOrder>, IntegrationError>>,
    },
    Detail {
        query: ExternalOrderQuery,
        reply: SyncSender<Result<Option<ExternalOrder>, IntegrationError>>,
    },
}

pub struct QueuedOrderQuery {
    sender: SyncSender<QueryGatewayRequest>,
}

pub struct QueryGatewayWorker {
    connection: Box<dyn OrderQueryConnection>,
    receiver: Receiver<QueryGatewayRequest>,
}

pub struct AsyncQueuedOrderQuery {
    sender: tokio::sync::mpsc::Sender<QueryGatewayRequest>,
}

pub struct AsyncQueryGatewayWorker<C> {
    connection: C,
    receiver: tokio::sync::mpsc::Receiver<QueryGatewayRequest>,
}

enum EventGatewayRequest {
    Connect {
        reply: SyncSender<Result<(), IntegrationError>>,
    },
    Disconnect {
        reply: SyncSender<Result<(), IntegrationError>>,
    },
    Reconnect {
        reply: SyncSender<Result<(), IntegrationError>>,
    },
    Health {
        reply: SyncSender<ConnectionHealth>,
    },
    Next {
        reply: SyncSender<
            Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError>,
        >,
    },
}

pub struct AsyncQueuedOrderEventSource {
    sender: tokio::sync::mpsc::Sender<EventGatewayRequest>,
}

pub struct AsyncEventGatewayWorker<C> {
    source: C,
    receiver: tokio::sync::mpsc::Receiver<EventGatewayRequest>,
}

impl QueuedOrderEntry {
    pub fn channel(
        connection: Box<dyn OrderEntryConnection>,
        capacity: usize,
    ) -> (Self, GatewayWorker) {
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        (
            Self { sender },
            GatewayWorker {
                connection,
                receiver,
            },
        )
    }

    fn request(
        &self,
        request: GatewayRequest,
        reply: Receiver<Result<CommandOutcome<OrderEntryEvent>, IntegrationError>>,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut request = Some(request);
        loop {
            match self
                .sender
                .try_send(request.take().expect("gateway request present"))
            {
                Ok(()) => break,
                Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                    return Err(IntegrationError::Unavailable(
                        "order gateway worker is stopped".into(),
                    ))
                }
                Err(std::sync::mpsc::TrySendError::Full(value)) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(IntegrationError::Backpressure(
                            "order gateway queue is full".into(),
                        ));
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        reply.recv().map_err(|_| {
            IntegrationError::Unavailable("order gateway worker did not respond".into())
        })?
    }
}

impl OrderEntryConnection for QueuedOrderEntry {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
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
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            GatewayRequest::Cancel {
                request: request.clone(),
                remote_order_id: remote_order_id.into(),
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
        while !stop.load(Ordering::Acquire) {
            match self.receiver.recv_timeout(Duration::from_millis(50)) {
                Ok(GatewayRequest::Submit { request, reply }) => {
                    let _ = reply.send(self.connection.submit_order(&request));
                }
                Ok(GatewayRequest::Cancel {
                    request,
                    remote_order_id,
                    at_unix_nanos,
                    reply,
                }) => {
                    let _ = reply.send(self.connection.cancel_order(
                        &request,
                        &remote_order_id,
                        at_unix_nanos,
                    ));
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => break,
            }
        }
    }
}

impl AsyncQueuedOrderEntry {
    pub fn channel<C>(connection: C, capacity: usize) -> (Self, AsyncGatewayWorker<C>)
    where
        C: AsyncOrderEntryConnection,
    {
        let (sender, receiver) = tokio::sync::mpsc::channel(capacity.max(1));
        (
            Self { sender },
            AsyncGatewayWorker {
                connection,
                receiver,
            },
        )
    }

    fn request(
        &self,
        request: GatewayRequest,
        reply: Receiver<Result<CommandOutcome<OrderEntryEvent>, IntegrationError>>,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut request = Some(request);
        loop {
            match self
                .sender
                .try_send(request.take().expect("async gateway request present"))
            {
                Ok(()) => break,
                Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => {
                    return Err(IntegrationError::Unavailable(
                        "async order gateway worker is stopped".into(),
                    ));
                }
                Err(tokio::sync::mpsc::error::TrySendError::Full(value)) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(IntegrationError::Backpressure(
                            "async order gateway queue is full".into(),
                        ));
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        reply.recv().map_err(|_| {
            IntegrationError::Unavailable("async order gateway did not respond".into())
        })?
    }
}

impl OrderEntryConnection for AsyncQueuedOrderEntry {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
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
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            GatewayRequest::Cancel {
                request: request.clone(),
                remote_order_id: remote_order_id.into(),
                at_unix_nanos,
                reply: reply_tx,
            },
            reply_rx,
        )
    }
}

impl<C> AsyncGatewayWorker<C>
where
    C: AsyncOrderEntryConnection,
{
    pub async fn run(mut self, mut shutdown: tokio::sync::watch::Receiver<bool>) {
        loop {
            let request = tokio::select! {
                biased;
                changed = shutdown.changed() => {
                    if changed.is_err() || *shutdown.borrow() {
                        break;
                    }
                    continue;
                }
                request = self.receiver.recv() => request,
            };
            let Some(request) = request else {
                break;
            };
            // Once a command is dequeued it is always driven to completion.
            // Dropping a submitted trading future on shutdown would destroy
            // delivery certainty and could duplicate an order on recovery.
            match request {
                GatewayRequest::Submit { request, reply } => {
                    let _ = reply.send(self.connection.submit_order(&request).await);
                }
                GatewayRequest::Cancel {
                    request,
                    remote_order_id,
                    at_unix_nanos,
                    reply,
                } => {
                    let _ = reply.send(
                        self.connection
                            .cancel_order(&request, &remote_order_id, at_unix_nanos)
                            .await,
                    );
                }
            }
        }
    }
}

impl QueuedOrderQuery {
    pub fn channel(
        connection: Box<dyn OrderQueryConnection>,
        capacity: usize,
    ) -> (Self, QueryGatewayWorker) {
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        (
            Self { sender },
            QueryGatewayWorker {
                connection,
                receiver,
            },
        )
    }

    fn request<T>(
        &self,
        request: QueryGatewayRequest,
        reply: Receiver<Result<T, IntegrationError>>,
    ) -> Result<T, IntegrationError> {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut request = Some(request);
        loop {
            match self
                .sender
                .try_send(request.take().expect("query gateway request present"))
            {
                Ok(()) => break,
                Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                    return Err(IntegrationError::Unavailable(
                        "order query worker is stopped".into(),
                    ))
                }
                Err(std::sync::mpsc::TrySendError::Full(value)) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(IntegrationError::Backpressure(
                            "order query queue is full".into(),
                        ));
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        reply.recv().map_err(|_| {
            IntegrationError::Unavailable("order query worker did not respond".into())
        })?
    }
}

impl OrderQueryConnection for QueuedOrderQuery {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::Open {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
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
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
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
    }
}

impl AsyncQueuedOrderQuery {
    pub fn channel<C>(connection: C, capacity: usize) -> (Self, AsyncQueryGatewayWorker<C>)
    where
        C: AsyncOrderQueryConnection,
    {
        let (sender, receiver) = tokio::sync::mpsc::channel(capacity.max(1));
        (
            Self { sender },
            AsyncQueryGatewayWorker {
                connection,
                receiver,
            },
        )
    }

    fn request<T>(
        &self,
        request: QueryGatewayRequest,
        reply: Receiver<Result<T, IntegrationError>>,
    ) -> Result<T, IntegrationError> {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut request = Some(request);
        loop {
            match self
                .sender
                .try_send(request.take().expect("async query request present"))
            {
                Ok(()) => break,
                Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => {
                    return Err(IntegrationError::Unavailable(
                        "async order query worker is stopped".into(),
                    ));
                }
                Err(tokio::sync::mpsc::error::TrySendError::Full(value)) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(IntegrationError::Backpressure(
                            "async order query queue is full".into(),
                        ));
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        reply.recv().map_err(|_| {
            IntegrationError::Unavailable("async order query worker did not respond".into())
        })?
    }
}

impl OrderQueryConnection for AsyncQueuedOrderQuery {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::Open {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
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
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
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

impl<C> AsyncQueryGatewayWorker<C>
where
    C: AsyncOrderQueryConnection,
{
    pub async fn run(mut self, mut shutdown: tokio::sync::watch::Receiver<bool>) {
        loop {
            let request = tokio::select! {
                biased;
                changed = shutdown.changed() => {
                    if changed.is_err() || *shutdown.borrow() {
                        break;
                    }
                    continue;
                }
                request = self.receiver.recv() => request,
            };
            let Some(request) = request else {
                break;
            };
            match request {
                QueryGatewayRequest::Open { query, reply } => {
                    let _ = reply.send(self.connection.open_orders(&query).await);
                }
                QueryGatewayRequest::History { query, reply } => {
                    let _ = reply.send(self.connection.order_history(&query).await);
                }
                QueryGatewayRequest::Detail { query, reply } => {
                    let _ = reply.send(self.connection.order_detail(&query).await);
                }
            }
        }
    }
}

impl AsyncQueuedOrderEventSource {
    pub fn channel<C>(source: C, capacity: usize) -> (Self, AsyncEventGatewayWorker<C>)
    where
        C: AsyncOrderEventSource,
    {
        let (sender, receiver) = tokio::sync::mpsc::channel(capacity.max(1));
        (
            Self { sender },
            AsyncEventGatewayWorker { source, receiver },
        )
    }

    fn send(&self, request: EventGatewayRequest) -> Result<(), IntegrationError> {
        self.sender.try_send(request).map_err(|error| match error {
            tokio::sync::mpsc::error::TrySendError::Full(_) => {
                IntegrationError::Backpressure("async event gateway queue is full".into())
            }
            tokio::sync::mpsc::error::TrySendError::Closed(_) => {
                IntegrationError::Unavailable("async event gateway is stopped".into())
            }
        })
    }
}

impl OrderEventSource for AsyncQueuedOrderEventSource {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        self.send(EventGatewayRequest::Connect { reply })?;
        receiver.recv().map_err(|_| {
            IntegrationError::Unavailable("async event gateway did not respond".into())
        })?
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        self.send(EventGatewayRequest::Disconnect { reply })?;
        receiver.recv().map_err(|_| {
            IntegrationError::Unavailable("async event gateway did not respond".into())
        })?
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        self.send(EventGatewayRequest::Reconnect { reply })?;
        receiver.recv().map_err(|_| {
            IntegrationError::Unavailable("async event gateway did not respond".into())
        })?
    }

    fn channel_health(&self) -> ConnectionHealth {
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        if self.send(EventGatewayRequest::Health { reply }).is_err() {
            return ConnectionHealth {
                lifecycle: kairos_integration::application::ConnectionLifecycle::Failed,
                healthy: false,
                authenticated: false,
                last_error: Some("async event gateway is stopped".into()),
            };
        }
        receiver.recv().unwrap_or(ConnectionHealth {
            lifecycle: kairos_integration::application::ConnectionLifecycle::Failed,
            healthy: false,
            authenticated: false,
            last_error: Some("async event gateway did not respond".into()),
        })
    }

    fn try_next_order_event(
        &mut self,
    ) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError> {
        let (reply, receiver) = std::sync::mpsc::sync_channel(1);
        self.send(EventGatewayRequest::Next { reply })?;
        receiver.recv().map_err(|_| {
            IntegrationError::Unavailable("async event gateway did not respond".into())
        })?
    }
}

impl<C> AsyncEventGatewayWorker<C>
where
    C: AsyncOrderEventSource,
{
    pub async fn run(mut self, mut shutdown: tokio::sync::watch::Receiver<bool>) {
        loop {
            let request = tokio::select! {
                biased;
                changed = shutdown.changed() => {
                    if changed.is_err() || *shutdown.borrow() {
                        break;
                    }
                    continue;
                }
                request = self.receiver.recv() => request,
            };
            let Some(request) = request else {
                break;
            };
            match request {
                EventGatewayRequest::Connect { reply } => {
                    let _ = reply.send(self.source.connect_channel().await);
                }
                EventGatewayRequest::Disconnect { reply } => {
                    let _ = reply.send(self.source.disconnect_channel().await);
                }
                EventGatewayRequest::Reconnect { reply } => {
                    let _ = reply.send(self.source.reconnect_channel().await);
                }
                EventGatewayRequest::Health { reply } => {
                    let _ = reply.send(self.source.channel_health());
                }
                EventGatewayRequest::Next { reply } => {
                    let result = match tokio::time::timeout(
                        Duration::from_secs(1),
                        self.source.next_order_event(),
                    )
                    .await
                    {
                        Ok(result) => result.map(Some),
                        Err(_) => Ok(None),
                    };
                    let _ = reply.send(result);
                }
            }
        }
        let _ = self.source.disconnect_channel().await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use kairos_integration::application::{
        ConnectionLifecycle, DecimalValue, OrderEntryOptions, OrderEntryStatus, OrderSide,
        OrderType, ParticipantKind, ParticipantRef, ProviderInstrumentRef,
    };
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    struct AwaitingOrderEntry {
        used_caller_runtime: Arc<AtomicBool>,
        started: Arc<tokio::sync::Notify>,
        release: Arc<tokio::sync::Notify>,
    }

    impl AsyncOrderEntryConnection for AwaitingOrderEntry {
        async fn submit_order(
            &mut self,
            request: &OrderEntryRequest,
        ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
            self.used_caller_runtime.store(
                tokio::runtime::Handle::try_current().is_ok(),
                Ordering::Release,
            );
            self.started.notify_one();
            self.release.notified().await;
            Ok(CommandOutcome::Confirmed(OrderEntryEvent {
                order_id: request.order_id.clone(),
                status: OrderEntryStatus::Accepted,
                remote_order_id: None,
                filled_quantity: None,
                occurred_at_unix_nanos: 1.into(),
                reason: "accepted".into(),
            }))
        }

        async fn cancel_order(
            &mut self,
            _request: &OrderEntryRequest,
            _remote_order_id: &str,
            _at_unix_nanos: u64,
        ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
            Err(IntegrationError::UnsupportedOperation)
        }
    }

    struct RuntimeCheckingQuery(Arc<AtomicBool>);

    impl AsyncOrderQueryConnection for RuntimeCheckingQuery {
        async fn open_orders(
            &mut self,
            _query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError> {
            self.0.store(
                tokio::runtime::Handle::try_current().is_ok(),
                Ordering::Release,
            );
            Ok(Vec::new())
        }

        async fn order_history(
            &mut self,
            _query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError> {
            Ok(Vec::new())
        }

        async fn order_detail(
            &mut self,
            _query: &ExternalOrderQuery,
        ) -> Result<Option<ExternalOrder>, IntegrationError> {
            Ok(None)
        }
    }

    struct PendingEventSource {
        lifecycle: ConnectionLifecycle,
    }

    impl AsyncOrderEventSource for PendingEventSource {
        async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
            self.lifecycle = ConnectionLifecycle::Ready;
            Ok(())
        }

        async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
            self.lifecycle = ConnectionLifecycle::Stopped;
            Ok(())
        }

        fn channel_health(&self) -> ConnectionHealth {
            ConnectionHealth {
                lifecycle: self.lifecycle,
                healthy: self.lifecycle == ConnectionLifecycle::Ready,
                authenticated: self.lifecycle == ConnectionLifecycle::Ready,
                last_error: None,
            }
        }

        async fn next_order_event(
            &mut self,
        ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
            std::future::pending().await
        }
    }

    fn request() -> OrderEntryRequest {
        OrderEntryRequest {
            order_id: kairos_primitives::OrderId::new("order-async-gateway").unwrap(),
            intent_id: None,
            account_id: kairos_primitives::AccountId::new("main").unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("spot").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("instrument:btc-usdt").unwrap(),
            market_id: None,
            provider_instrument: ProviderInstrumentRef::new(
                ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
                Some(kairos_integration::participants::binance::ConnectionDomain::Spot.into()),
                "BTCUSDT",
            )
            .unwrap(),
            side: OrderSide::Buy,
            quantity: DecimalValue::new(1, 0),
            order_type: OrderType::Market,
            limit_price: None,
            options: OrderEntryOptions::default(),
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn async_command_uses_business_runtime_and_finishes_after_shutdown_starts() {
        let used_caller_runtime = Arc::new(AtomicBool::new(false));
        let started = Arc::new(tokio::sync::Notify::new());
        let release = Arc::new(tokio::sync::Notify::new());
        let (mut proxy, worker) = AsyncQueuedOrderEntry::channel(
            AwaitingOrderEntry {
                used_caller_runtime: Arc::clone(&used_caller_runtime),
                started: Arc::clone(&started),
                release: Arc::clone(&release),
            },
            2,
        );
        let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
        let worker = tokio::spawn(worker.run(shutdown_rx));
        let call = tokio::task::spawn_blocking(move || proxy.submit_order(&request()));

        started.notified().await;
        shutdown.send(true).unwrap();
        release.notify_one();

        let result = call.await.unwrap().unwrap();
        assert!(matches!(result, CommandOutcome::Confirmed(_)));
        assert!(used_caller_runtime.load(Ordering::Acquire));
        worker.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn async_query_uses_business_runtime_without_a_dedicated_thread_runtime() {
        let used_caller_runtime = Arc::new(AtomicBool::new(false));
        let (mut proxy, worker) = AsyncQueuedOrderQuery::channel(
            RuntimeCheckingQuery(Arc::clone(&used_caller_runtime)),
            2,
        );
        let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
        let worker = tokio::spawn(worker.run(shutdown_rx));
        let call =
            tokio::task::spawn_blocking(move || proxy.open_orders(&ExternalOrderQuery::default()));

        assert!(call.await.unwrap().unwrap().is_empty());
        assert!(used_caller_runtime.load(Ordering::Acquire));
        let _ = shutdown.send(true);
        worker.await.unwrap();
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn async_event_proxy_bounds_one_shot_cli_poll() {
        let (mut proxy, worker) = AsyncQueuedOrderEventSource::channel(
            PendingEventSource {
                lifecycle: ConnectionLifecycle::Created,
            },
            1,
        );
        let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
        let worker = tokio::spawn(worker.run(shutdown_rx));
        let call = tokio::task::spawn_blocking(move || proxy.try_next_order_event());

        assert!(call.await.unwrap().unwrap().is_none());
        let _ = shutdown.send(true);
        worker.await.unwrap();
    }
}
