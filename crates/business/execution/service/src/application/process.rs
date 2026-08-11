use crate::application::{
    BacktestApplication, BacktestRequest, CancelIntent, CancelOrder, ExecuteStrategyIntent,
    ExecutionApplication, ExecutionAuditEvent, ExecutionAuditQuery, ExecutionAuditSink,
    ExecutionFillReport, ExecutionSnapshot, ExpireIntent, RefreshQuoteIntent, RemoteOrderQuery,
    ReplaceOrder, SubmitOrder,
};
use crate::services::actor::RemoteOrderEvent;
use crate::services::gateway::{
    AsyncQueuedOrderEntry, AsyncQueuedOrderQuery, QueuedOrderEntry, QueuedOrderQuery,
};
use crate::services::persistence::ExecutionOutboxEvent;
use crate::services::simulator::{ExecutionSimulator, SimulationOrderRequest};
use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json, Router,
};
use kairos_integration::application::{
    AsyncOrderEntryConnection, AsyncOrderEventSource, AsyncOrderQueryConnection, CommandOutcome,
    ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery,
    IntegrationError, OrderEntryEvent, OrderEntryRequest,
};
use kairos_workspace::runtime::{HEALTH_PATH, SNAPSHOT_PATH, STOP_PATH};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use tokio::net::UnixListener;
use tokio::sync::oneshot;
use tracing::{debug, info, Instrument};

#[derive(serde::Deserialize)]
struct IntentPayload {
    intent: ExecuteStrategyIntent,
}

#[derive(serde::Deserialize)]
struct CommandEnvelope<T> {
    schema_version: u16,
    command_id: String,
    idempotency_key: String,
    operation: String,
    strategy_id: String,
    instance_id: String,
    #[serde(default)]
    launch_id: String,
    payload: T,
}

pub struct ExecutionProcess<
    E = NoAsyncOrderEntryConnection,
    Q = NoAsyncOrderQueryConnection,
    S = NoAsyncOrderEventSource,
> {
    application: ExecutionApplication,
    simulator: Option<ExecutionSimulator>,
    socket_path: PathBuf,
    audit: Option<Box<dyn ExecutionAuditSink>>,
    stopping: bool,
    last_published_generation: Option<u64>,
    snapshot_publisher: Option<Box<dyn ExecutionSnapshotPublisher>>,
    intent_snapshot_publisher: Option<Box<dyn IntentSnapshotPublisher>>,
    seen_exchange_events: std::collections::HashSet<String>,
    exchange_event_order: std::collections::VecDeque<String>,
    metrics: std::sync::Arc<RuntimeMetrics>,
    last_remote_reconcile_unix_nanos: u64,
    async_order_entry: Option<E>,
    async_order_query: Option<Q>,
    async_execution_streams: Vec<S>,
}

/// Empty async order-entry type used by legacy providers and fixtures.
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

/// Empty async order-query type used by legacy providers and fixtures.
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

/// Empty async source type used by fixtures and non-streaming processes.
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

/// Application-owned publication capabilities. Concrete transport publishers
/// are selected by composition and injected into the process facade.
pub trait ExecutionSnapshotPublisher: Send {
    fn publish(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String>;
}

pub trait IntentSnapshotPublisher: Send {
    fn publish(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String>;
}

struct ExecutionHttpRequest {
    target: String,
    body: Vec<u8>,
    response: oneshot::Sender<Result<(u16, Value), String>>,
}

const EXCHANGE_BATCH_LIMIT: usize = 64;

#[derive(Default)]
struct RuntimeMetrics {
    pending_commands: AtomicUsize,
    pending_queries: AtomicUsize,
    pending_exchange_events: AtomicUsize,
    exchange_events_applied: AtomicU64,
    exchange_batches: AtomicU64,
    max_exchange_batch: AtomicUsize,
    state_loop_errors: AtomicU64,
    last_operation_micros: AtomicU64,
}

impl RuntimeMetrics {
    fn snapshot(&self) -> Value {
        json!({
            "pending_commands": self.pending_commands.load(Ordering::Relaxed),
            "pending_queries": self.pending_queries.load(Ordering::Relaxed),
            "pending_exchange_events": self.pending_exchange_events.load(Ordering::Relaxed),
            "exchange_events_applied": self.exchange_events_applied.load(Ordering::Relaxed),
            "exchange_batches": self.exchange_batches.load(Ordering::Relaxed),
            "max_exchange_batch": self.max_exchange_batch.load(Ordering::Relaxed),
            "state_loop_errors": self.state_loop_errors.load(Ordering::Relaxed),
            "last_operation_micros": self.last_operation_micros.load(Ordering::Relaxed),
        })
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum RequestClass {
    Command,
    Query,
}

#[derive(Clone)]
struct ExecutionIngress {
    command_tx: SyncSender<ExecutionHttpRequest>,
    query_tx: SyncSender<ExecutionHttpRequest>,
    metrics: std::sync::Arc<RuntimeMetrics>,
}

impl
    ExecutionProcess<
        NoAsyncOrderEntryConnection,
        NoAsyncOrderQueryConnection,
        NoAsyncOrderEventSource,
    >
{
    pub fn new(application: ExecutionApplication, socket_path: impl Into<PathBuf>) -> Self {
        Self {
            application,
            simulator: None,
            socket_path: socket_path.into(),
            audit: None,
            stopping: false,
            last_published_generation: None,
            snapshot_publisher: None,
            intent_snapshot_publisher: None,
            seen_exchange_events: std::collections::HashSet::new(),
            exchange_event_order: std::collections::VecDeque::new(),
            metrics: std::sync::Arc::new(RuntimeMetrics::default()),
            last_remote_reconcile_unix_nanos: 0,
            async_order_entry: None,
            async_order_query: None,
            async_execution_streams: Vec::new(),
        }
    }

    pub fn with_audit(
        application: ExecutionApplication,
        socket_path: impl Into<PathBuf>,
        audit: impl ExecutionAuditSink + 'static,
    ) -> Self {
        Self {
            application,
            simulator: None,
            socket_path: socket_path.into(),
            audit: Some(Box::new(audit)),
            stopping: false,
            last_published_generation: None,
            snapshot_publisher: None,
            intent_snapshot_publisher: None,
            seen_exchange_events: std::collections::HashSet::new(),
            exchange_event_order: std::collections::VecDeque::new(),
            metrics: std::sync::Arc::new(RuntimeMetrics::default()),
            last_remote_reconcile_unix_nanos: 0,
            async_order_entry: None,
            async_order_query: None,
            async_execution_streams: Vec::new(),
        }
    }
}

impl<E, Q, S> ExecutionProcess<E, Q, S> {
    pub fn with_async_order_entry<T>(self, connection: Option<T>) -> ExecutionProcess<T, Q, S> {
        ExecutionProcess {
            application: self.application,
            simulator: self.simulator,
            socket_path: self.socket_path,
            audit: self.audit,
            stopping: self.stopping,
            last_published_generation: self.last_published_generation,
            snapshot_publisher: self.snapshot_publisher,
            intent_snapshot_publisher: self.intent_snapshot_publisher,
            seen_exchange_events: self.seen_exchange_events,
            exchange_event_order: self.exchange_event_order,
            metrics: self.metrics,
            last_remote_reconcile_unix_nanos: self.last_remote_reconcile_unix_nanos,
            async_order_entry: connection,
            async_order_query: self.async_order_query,
            async_execution_streams: self.async_execution_streams,
        }
    }

    pub fn with_async_order_query<T>(self, connection: Option<T>) -> ExecutionProcess<E, T, S> {
        ExecutionProcess {
            application: self.application,
            simulator: self.simulator,
            socket_path: self.socket_path,
            audit: self.audit,
            stopping: self.stopping,
            last_published_generation: self.last_published_generation,
            snapshot_publisher: self.snapshot_publisher,
            intent_snapshot_publisher: self.intent_snapshot_publisher,
            seen_exchange_events: self.seen_exchange_events,
            exchange_event_order: self.exchange_event_order,
            metrics: self.metrics,
            last_remote_reconcile_unix_nanos: self.last_remote_reconcile_unix_nanos,
            async_order_entry: self.async_order_entry,
            async_order_query: connection,
            async_execution_streams: self.async_execution_streams,
        }
    }

    pub fn with_async_execution_stream<T>(self, source: Option<T>) -> ExecutionProcess<E, Q, T> {
        self.with_async_execution_streams(source.into_iter().collect())
    }

    pub fn with_async_execution_streams<T>(self, sources: Vec<T>) -> ExecutionProcess<E, Q, T> {
        ExecutionProcess {
            application: self.application,
            simulator: self.simulator,
            socket_path: self.socket_path,
            audit: self.audit,
            stopping: self.stopping,
            last_published_generation: self.last_published_generation,
            snapshot_publisher: self.snapshot_publisher,
            intent_snapshot_publisher: self.intent_snapshot_publisher,
            seen_exchange_events: self.seen_exchange_events,
            exchange_event_order: self.exchange_event_order,
            metrics: self.metrics,
            last_remote_reconcile_unix_nanos: self.last_remote_reconcile_unix_nanos,
            async_order_entry: self.async_order_entry,
            async_order_query: self.async_order_query,
            async_execution_streams: sources,
        }
    }

    pub fn with_simulator(mut self, simulator: ExecutionSimulator) -> Self {
        self.simulator = Some(simulator);
        self
    }

    pub fn with_snapshot_publisher<P>(mut self, publisher: P) -> Self
    where
        P: ExecutionSnapshotPublisher + 'static,
    {
        self.snapshot_publisher = Some(Box::new(publisher));
        self
    }

    pub fn with_intent_snapshot_publisher(
        mut self,
        publisher: impl IntentSnapshotPublisher + 'static,
    ) -> Self {
        self.intent_snapshot_publisher = Some(Box::new(publisher));
        self
    }

    pub async fn run(mut self) -> Result<(), Box<dyn std::error::Error>>
    where
        E: AsyncOrderEntryConnection + 'static,
        Q: AsyncOrderQueryConnection + 'static,
        S: AsyncOrderEventSource + 'static,
    {
        info!(event = "process_starting", component = "execution", socket = %self.socket_path.display(), "execution process starting");
        remove_socket(&self.socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket_path)?;
        let (command_sender, command_receiver) = std::sync::mpsc::sync_channel(256);
        let (query_sender, query_receiver) = std::sync::mpsc::sync_channel(512);
        let (exchange_sender, exchange_receiver) = std::sync::mpsc::sync_channel(4096);
        // Keep the exchange mailbox alive even when no provider stream is
        // configured.  A missing stream is a valid degraded mode, not a
        // signal for the state owner to shut down.
        let _exchange_sender_guard = exchange_sender.clone();
        let stream_stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let stream_task = self.start_stream_consumer(
            exchange_sender.clone(),
            stream_stop.clone(),
            std::sync::Arc::clone(&self.metrics),
        );
        let (async_stream_shutdown, async_stream_shutdown_rx) = tokio::sync::watch::channel(false);
        let async_stream_tasks = self.start_async_stream_consumers(
            exchange_sender,
            async_stream_shutdown_rx,
            std::sync::Arc::clone(&self.metrics),
        );
        let (async_gateway_shutdown, async_gateway_shutdown_rx) =
            tokio::sync::watch::channel(false);
        let async_gateway_task = self.start_async_gateway_worker(async_gateway_shutdown_rx.clone());
        let async_query_gateway_task =
            self.start_async_query_gateway_worker(async_gateway_shutdown_rx);
        let gateway_stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let gateway_task = if async_gateway_task.is_none() {
            self.start_gateway_worker(gateway_stop.clone())
        } else {
            None
        };
        let query_gateway_stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let query_gateway_task = if async_query_gateway_task.is_none() {
            self.start_query_gateway_worker(query_gateway_stop.clone())
        } else {
            None
        };
        let router = Router::new()
            .fallback(execution_http_handler)
            .with_state(ExecutionIngress {
                command_tx: command_sender,
                query_tx: query_sender,
                metrics: std::sync::Arc::clone(&self.metrics),
            });
        let server = tokio::spawn(async move { axum::serve(listener, router).await });
        kairos_workspace::logging::record_gauge("kairos.process.ready", 1);
        info!(event = "process_ready", component = "execution", socket = %self.socket_path.display(), "execution control socket ready");
        let socket_path = self.socket_path.clone();
        let state_task = tokio::task::spawn_blocking(move || {
            self.state_loop(command_receiver, query_receiver, exchange_receiver)
        });
        state_task
            .await
            .map_err(|error| format!("execution state task failed: {error}"))?
            .map_err(|error| error.to_string())?;
        stream_stop.store(true, std::sync::atomic::Ordering::Release);
        let _ = async_stream_shutdown.send(true);
        for task in async_stream_tasks {
            if let Err(error) = task.await {
                tracing::warn!(
                    event = "async_exchange_stream_task_failed",
                    component = "execution",
                    error = %error,
                    "async exchange stream task failed"
                );
            }
        }
        if let Some(task) = stream_task {
            if task.is_finished() {
                let _ = task.join();
            } else {
                tracing::warn!(
                    event = "exchange_stream_shutdown_deferred",
                    component = "execution",
                    "exchange stream did not stop before process teardown"
                );
            }
        }
        gateway_stop.store(true, std::sync::atomic::Ordering::Release);
        if let Some(task) = gateway_task {
            let _ = task.join();
        }
        query_gateway_stop.store(true, std::sync::atomic::Ordering::Release);
        if let Some(task) = query_gateway_task {
            let _ = task.join();
        }
        let _ = async_gateway_shutdown.send(true);
        if let Some(task) = async_gateway_task {
            if let Err(error) = task.await {
                tracing::warn!(event = "async_order_gateway_task_failed", component = "execution", error = %error, "async order gateway task failed");
            }
        }
        if let Some(task) = async_query_gateway_task {
            if let Err(error) = task.await {
                tracing::warn!(event = "async_order_query_gateway_task_failed", component = "execution", error = %error, "async order query gateway task failed");
            }
        }
        remove_socket(&socket_path)?;
        server.abort();
        let _ = server.await;
        info!(
            event = "process_stopped",
            component = "execution",
            "execution process stopped"
        );
        Ok(())
    }

    fn publish_snapshots(&mut self) -> Result<(), String> {
        let snapshot = self.application.snapshot();
        if self.last_published_generation == Some(snapshot.generation.get()) {
            return Ok(());
        }
        if let Some(publisher) = self.snapshot_publisher.as_mut() {
            publisher.publish(&snapshot)?;
        }
        if let Some(publisher) = self.intent_snapshot_publisher.as_mut() {
            publisher.publish(&snapshot)?;
        }
        debug!(
            event = "execution_snapshots_published",
            component = "execution",
            generation = snapshot.generation.get(),
            event_sequence = snapshot.event_sequence.get(),
            order_count = snapshot.orders.len(),
            intent_count = snapshot.intents.len(),
            "execution snapshots published"
        );
        self.last_published_generation = Some(snapshot.generation.get());
        Ok(())
    }

    fn accept_exchange_event(&mut self, event_id: &str) -> bool {
        const MAX_SEEN_EXCHANGE_EVENTS: usize = 100_000;
        if !self.seen_exchange_events.insert(event_id.to_owned()) {
            return false;
        }
        self.exchange_event_order.push_back(event_id.to_owned());
        if self.exchange_event_order.len() > MAX_SEEN_EXCHANGE_EVENTS {
            if let Some(expired) = self.exchange_event_order.pop_front() {
                self.seen_exchange_events.remove(&expired);
            }
        }
        true
    }

    fn start_async_stream_consumers(
        &mut self,
        sender: SyncSender<RemoteOrderEvent>,
        shutdown: tokio::sync::watch::Receiver<bool>,
        metrics: std::sync::Arc<RuntimeMetrics>,
    ) -> Vec<tokio::task::JoinHandle<()>>
    where
        S: AsyncOrderEventSource + 'static,
    {
        self.async_execution_streams
            .drain(..)
            .map(|mut stream| {
                let sender = sender.clone();
                let metrics = std::sync::Arc::clone(&metrics);
                let mut shutdown = shutdown.clone();
                tokio::spawn(async move {
            loop {
                let result = tokio::select! {
                    biased;
                    changed = shutdown.changed() => {
                        if changed.is_err() || *shutdown.borrow() {
                            break;
                        }
                        continue;
                    }
                    result = stream.next_order_event() => result,
                };
                match result {
                    Ok(envelope) => {
                        let message = remote_order_event_from_envelope(envelope);
                        metrics
                            .pending_exchange_events
                            .fetch_add(1, Ordering::Relaxed);
                        match sender.try_send(message) {
                            Ok(()) => {}
                            Err(std::sync::mpsc::TrySendError::Full(_)) => {
                                metrics
                                    .pending_exchange_events
                                    .fetch_sub(1, Ordering::Relaxed);
                                metrics.state_loop_errors.fetch_add(1, Ordering::Relaxed);
                                tracing::warn!(
                                    event = "exchange_event_mailbox_overflow",
                                    component = "execution",
                                    "execution event mailbox overflowed; stop stream and reconcile"
                                );
                                break;
                            }
                            Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                                metrics
                                    .pending_exchange_events
                                    .fetch_sub(1, Ordering::Relaxed);
                                break;
                            }
                        }
                    }
                    Err(error) => {
                        tracing::warn!(event = "async_exchange_stream_error", component = "execution", error = %error, "async exchange stream read failed");
                        let reconnect = tokio::select! {
                            biased;
                            changed = shutdown.changed() => {
                                if changed.is_err() || *shutdown.borrow() {
                                    break;
                                }
                                continue;
                            }
                            result = stream.reconnect_channel() => result,
                        };
                        match reconnect {
                            Ok(()) => {
                                kairos_workspace::logging::record_counter("kairos.reconnect", 1);
                            }
                            Err(reconnect_error) => {
                                kairos_workspace::logging::record_counter("kairos.retry", 1);
                                tracing::warn!(event = "async_exchange_stream_reconnect_failed", component = "execution", error = %reconnect_error, "async exchange stream reconnect failed");
                            }
                        }
                        tokio::select! {
                            _ = tokio::time::sleep(std::time::Duration::from_millis(250)) => {}
                            changed = shutdown.changed() => {
                                if changed.is_err() || *shutdown.borrow() {
                                    break;
                                }
                            }
                        }
                    }
                }
            }
            let _ = stream.disconnect_channel().await;
                })
            })
            .collect()
    }

    fn start_stream_consumer(
        &mut self,
        sender: SyncSender<RemoteOrderEvent>,
        stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
        metrics: std::sync::Arc<RuntimeMetrics>,
    ) -> Option<std::thread::JoinHandle<()>> {
        let mut stream = self.application.take_execution_stream()?;
        Some(std::thread::spawn(move || {
            use std::sync::atomic::Ordering;
            use std::time::Duration;
            loop {
                if stop.load(Ordering::Acquire) {
                    break;
                }
                match stream.try_next_order_event() {
                    Ok(Some(envelope)) => {
                        let message = remote_order_event_from_envelope(envelope);
                        metrics
                            .pending_exchange_events
                            .fetch_add(1, Ordering::Relaxed);
                        if sender.send(message).is_err() {
                            metrics
                                .pending_exchange_events
                                .fetch_sub(1, Ordering::Relaxed);
                            break;
                        }
                    }
                    Ok(None) => std::thread::sleep(Duration::from_millis(10)),
                    Err(error) => {
                        tracing::warn!(event = "exchange_stream_error", component = "execution", error = %error, "exchange stream read failed");
                        if let Err(reconnect_error) = stream.reconnect_channel() {
                            kairos_workspace::logging::record_counter("kairos.retry", 1);
                            tracing::warn!(
                                event = "exchange_stream_reconnect_failed",
                                component = "execution",
                                error = %reconnect_error,
                                "exchange stream reconnect failed"
                            );
                        } else {
                            kairos_workspace::logging::record_counter("kairos.reconnect", 1);
                            tracing::info!(
                                event = "exchange_stream_reconnected",
                                component = "execution",
                                "exchange stream reconnected"
                            );
                        }
                        std::thread::sleep(Duration::from_millis(250));
                    }
                }
            }
            let _ = stream.disconnect_channel();
        }))
    }

    fn start_gateway_worker(
        &mut self,
        stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
    ) -> Option<std::thread::JoinHandle<()>> {
        let connection = self.application.take_order_entry()?;
        let (proxy, worker) = QueuedOrderEntry::channel(connection, 256);
        self.application.install_order_entry(Box::new(proxy));
        Some(std::thread::spawn(move || worker.run(stop)))
    }

    fn start_async_gateway_worker(
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

    fn start_query_gateway_worker(
        &mut self,
        stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
    ) -> Option<std::thread::JoinHandle<()>> {
        let connection = self.application.take_order_query()?;
        let (proxy, worker) = QueuedOrderQuery::channel(connection, 128);
        self.application.install_order_query(Box::new(proxy));
        Some(std::thread::spawn(move || worker.run(stop)))
    }

    fn start_async_query_gateway_worker(
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

    fn state_loop(
        mut self,
        command_receiver: Receiver<ExecutionHttpRequest>,
        query_receiver: Receiver<ExecutionHttpRequest>,
        exchange_receiver: Receiver<RemoteOrderEvent>,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.flush_events()?;
        self.publish_snapshots()?;
        while !self.stopping {
            let now = now_unix_nanos();
            if self.application.has_order_query()
                && now.saturating_sub(self.last_remote_reconcile_unix_nanos) >= 5_000_000_000
            {
                self.last_remote_reconcile_unix_nanos = now;
                match self.application.reconcile_remote_orders(RemoteOrderQuery {
                    limit: Some(200),
                    ..RemoteOrderQuery::default()
                }) {
                    Ok(changed) if changed > 0 => {
                        tracing::info!(
                            event = "remote_order_reconciliation_completed",
                            component = "execution",
                            changed,
                            "remote order reconciliation completed"
                        );
                        self.flush_events()?;
                        self.publish_snapshots()?;
                    }
                    Ok(_) => {}
                    Err(error) => tracing::warn!(
                        event = "remote_order_reconciliation_failed",
                        component = "execution",
                        error = %error,
                        "remote order reconciliation failed"
                    ),
                }
            }
            if self.application.refresh_maker_quotes()? > 0 {
                self.flush_events()?;
                self.publish_snapshots()?;
            }
            if self
                .application
                .advance_due_intent_orders(now_unix_nanos(), EXCHANGE_BATCH_LIMIT)?
                > 0
            {
                self.flush_events()?;
                self.publish_snapshots()?;
            }
            if self.application.expire_due_intents(now_unix_nanos())? > 0 {
                self.flush_events()?;
                self.publish_snapshots()?;
            }
            let mut exchange_batch = 0;
            while exchange_batch < EXCHANGE_BATCH_LIMIT {
                match exchange_receiver.try_recv() {
                    Ok(event) => {
                        self.metrics
                            .pending_exchange_events
                            .fetch_sub(1, Ordering::Relaxed);
                        self.apply_exchange_event(event)?;
                        exchange_batch += 1;
                    }
                    Err(std::sync::mpsc::TryRecvError::Empty) => break,
                    Err(std::sync::mpsc::TryRecvError::Disconnected) => return Ok(()),
                }
            }
            if exchange_batch > 0 {
                self.flush_events()?;
                self.publish_snapshots()?;
                self.metrics
                    .exchange_batches
                    .fetch_add(1, Ordering::Relaxed);
                self.metrics
                    .max_exchange_batch
                    .fetch_max(exchange_batch, Ordering::Relaxed);
                if let Ok(request) = command_receiver.try_recv() {
                    self.handle_http_request(request)?;
                } else if let Ok(request) = query_receiver.try_recv() {
                    self.handle_http_request(request)?;
                }
                continue;
            }
            if let Ok(request) = command_receiver.try_recv() {
                self.metrics
                    .pending_commands
                    .fetch_sub(1, Ordering::Relaxed);
                self.handle_http_request(request)?;
                continue;
            }
            if let Ok(request) = query_receiver.try_recv() {
                self.metrics.pending_queries.fetch_sub(1, Ordering::Relaxed);
                self.handle_http_request(request)?;
                continue;
            }

            match exchange_receiver.recv_timeout(std::time::Duration::from_millis(10)) {
                Ok(event) => {
                    self.metrics
                        .pending_exchange_events
                        .fetch_sub(1, Ordering::Relaxed);
                    self.apply_exchange_event(event)?;
                    let mut batch = 1;
                    while batch < EXCHANGE_BATCH_LIMIT {
                        match exchange_receiver.try_recv() {
                            Ok(event) => {
                                self.metrics
                                    .pending_exchange_events
                                    .fetch_sub(1, Ordering::Relaxed);
                                self.apply_exchange_event(event)?;
                                batch += 1;
                            }
                            Err(std::sync::mpsc::TryRecvError::Empty)
                            | Err(std::sync::mpsc::TryRecvError::Disconnected) => break,
                        }
                    }
                    self.flush_events()?;
                    self.publish_snapshots()?;
                    self.metrics
                        .exchange_batches
                        .fetch_add(1, Ordering::Relaxed);
                    self.metrics
                        .max_exchange_batch
                        .fetch_max(batch, Ordering::Relaxed);
                }
                Err(RecvTimeoutError::Disconnected) => break,
                Err(RecvTimeoutError::Timeout) => {}
            }
        }
        Ok(())
    }

    fn handle_http_request(
        &mut self,
        request: ExecutionHttpRequest,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let operation_started = std::time::Instant::now();
        let response =
            self.handle_request(&request.target, &String::from_utf8_lossy(&request.body));
        let _ = request
            .response
            .send(response.map_err(|error| error.to_string()));
        if request_class(&request.target) == RequestClass::Command {
            self.flush_events()?;
            self.publish_snapshots()?;
        }
        self.metrics.last_operation_micros.store(
            operation_started.elapsed().as_micros() as u64,
            Ordering::Relaxed,
        );
        Ok(())
    }

    fn apply_exchange_event(
        &mut self,
        event: RemoteOrderEvent,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.metrics
            .exchange_events_applied
            .fetch_add(1, Ordering::Relaxed);
        if self.accept_exchange_event(&event.event_id) {
            if let Err(error) = self.application.apply_remote_execution_event(event.event) {
                tracing::warn!(event = "exchange_event_rejected", component = "execution", error = %error, "exchange event was not applied");
            }
        }
        Ok(())
    }

    fn register_simulation_order(
        &mut self,
        order: &crate::domain::ExecutionOrder,
    ) -> Result<(), String> {
        let Some(simulator) = self.simulator.as_mut() else {
            return Ok(());
        };
        if simulator.order(&order.order_id).is_some() {
            return Ok(());
        }
        simulator
            .submit(SimulationOrderRequest {
                order_id: kairos_domain_types::OrderId::new(order.order_id.to_string())
                    .map_err(|error| error.to_string())?,
                instrument_id: kairos_domain_types::InstrumentId::new(
                    order.instrument_id.to_string(),
                )
                .map_err(|error| error.to_string())?,
                side: order.side,
                order_type: order.order_type,
                quantity: kairos_domain_types::Quantity::new(
                    order.quantity.mantissa(),
                    order.quantity.scale(),
                )
                .map_err(|error| error.to_string())?,
                limit_price: order
                    .limit_price
                    .map(|price| kairos_domain_types::Price::new(price.mantissa(), price.scale()))
                    .transpose()
                    .map_err(|error| error.to_string())?,
                // Execution's lifecycle timestamp is wall-clock based, while
                // replay observations use dataset time.  The StrategyHost
                // forwards the current observation immediately after the
                // intent, so the simulator must not compare those unrelated
                // clocks here.
                submitted_at_unix_nanos: 0.into(),
            })
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    fn apply_simulated_market(
        &mut self,
        event: kairos_market_contract::model::MarketObservation,
    ) -> Result<Vec<crate::services::simulator::SimulationFill>, String> {
        let Some(simulator) = self.simulator.as_mut() else {
            return Err("execution simulator is not enabled".into());
        };
        simulator.apply_market_event(event)?;
        let fills = simulator.take_fills();
        for fill in &fills {
            self.application
                .record_fill(ExecutionFillReport {
                    fill_id: fill.fill_id.clone(),
                    order_id: fill.order_id.clone(),
                    quantity: fill.quantity,
                    price: fill.price,
                    fee: fill.fee,
                    occurred_at_unix_nanos: Some(fill.occurred_at_unix_nanos),
                })
                .map_err(|error| error.to_string())?;
        }
        Ok(fills)
    }

    fn handle_request(
        &mut self,
        target: &str,
        body: &str,
    ) -> Result<(u16, Value), Box<dyn std::error::Error>> {
        let started = Instant::now();
        let (path, query) = target.split_once('?').unwrap_or((target, ""));
        info!(event = "control_request", component = "execution", path = %path, "execution control request received");
        let (status, payload) = match path {
            HEALTH_PATH => (
                200,
                json!({"status":"ready","pid":std::process::id(),"actor_id":self.application.snapshot().actor_id,"generation":self.application.snapshot().generation,"event_sequence":self.application.snapshot().event_sequence,"order_count":self.application.snapshot().orders.len(),"dependency_watermarks":self.application.dependency_watermarks(),"runtime_metrics":self.metrics.snapshot()}),
            ),
            SNAPSHOT_PATH => (200, serde_json::to_value(self.application.snapshot())?),
            "/v1/intents" => (200, json!({"intents": self.application.intents()})),
            "/v1/intent" => match self
                .application
                .intent(&query_value(query, "intent_id").unwrap_or_default())
            {
                Some(intent) => (200, serde_json::to_value(intent)?),
                None => (
                    404,
                    json!({"error":{"code":"execution.intent_not_found","message":"intent not found","retryable":false}}),
                ),
            },
            "/v1/intent-events" => (
                200,
                json!({"events": self.application.intent_events_after(
                    query_value(query, "intent_id").as_deref(),
                    query_value(query, "after_sequence").and_then(|value| value.parse().ok()).unwrap_or_default(),
                    query_value(query, "limit").and_then(|value| value.parse().ok()),
                )}),
            ),
            "/v1/intent-hedge" => match self
                .application
                .hedge_requirement(&query_value(query, "intent_id").unwrap_or_default())
            {
                Ok(requirement) => (200, serde_json::to_value(requirement)?),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/orders" => (
                200,
                json!({"orders": self.application.orders(query_value(query, "account_id").as_deref())}),
            ),
            "/v1/open-orders" => (
                200,
                json!({"orders": self.application.orders(query_value(query, "account_id").as_deref()).into_iter().filter(|order| !order.status.terminal()).collect::<Vec<_>>() }),
            ),
            "/v1/history" => (
                200,
                json!({"orders": self.application.orders(query_value(query, "account_id").as_deref())}),
            ),
            "/v1/remote-open-orders" => {
                match self.application.remote_open_orders(remote_query(query)) {
                    Ok(orders) => (200, json!({"orders": orders})),
                    Err(error) => (422, json!({"error": error.to_string()})),
                }
            }
            "/v1/remote-history" => match self.application.remote_history(remote_query(query)) {
                Ok(orders) => (200, json!({"orders": orders})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/remote-order" => match self.application.remote_detail(remote_query(query)) {
                Ok(order) => (200, json!(order)),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/reconcile-remote" => match self
                .application
                .reconcile_remote_orders(remote_query(query))
            {
                Ok(changed) => (200, json!({"changed": changed})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/unknown-remote-orders" => (
                200,
                json!({"orders": self.application.unknown_remote_orders()}),
            ),
            "/v1/link-unknown-remote" => {
                let remote_order_id = query_value(query, "remote_order_id").unwrap_or_default();
                let local_order_id = query_value(query, "local_order_id").unwrap_or_default();
                match self
                    .application
                    .link_unknown_remote_order(&remote_order_id, &local_order_id)
                {
                    Ok(order) => (200, serde_json::to_value(order)?),
                    Err(error) => (422, json!({"error": error.to_string()})),
                }
            }
            "/v1/stream/next" | "/v1/stream/consume" => (
                410,
                json!({"error":"exchange execution streams are consumed internally by the execution process"}),
            ),
            "/v1/events" => (
                200,
                json!({"events": self.application.events(query_value(query, "order_id").as_deref())}),
            ),
            "/v1/fills" => (
                200,
                json!({"fills": self.application.fills(query_value(query, "order_id").as_deref())}),
            ),
            "/v1/trace" => (
                200,
                json!({"events": self.application.trace(&query_value(query, "order_id").unwrap_or_default())}),
            ),
            "/v1/audit" => match self.audit_events(audit_query(query)) {
                Ok(events) => (200, json!({"events": events})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/journal" => match self.audit_events(audit_query(query)) {
                Ok(events) => (
                    200,
                    json!({"order_id": query_value(query, "order_id"), "entries": events}),
                ),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/backtest" => match serde_json::from_str::<BacktestRequest>(body)
                .map_err(|error| error.to_string())
                .and_then(BacktestApplication::evaluate)
            {
                Ok(metrics) => (200, serde_json::to_value(metrics)?),
                Err(error) => (422, json!({"error": error})),
            },
            "/v1/backtest/run" => match serde_json::from_str::<BacktestRequest>(body)
                .map_err(|error| error.to_string())
                .and_then(BacktestApplication::run)
            {
                Ok(result) => (200, serde_json::to_value(result)?),
                Err(error) => (422, json!({"error": error})),
            },
            "/v1/submit" => match serde_json::from_str::<SubmitOrder>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .submit(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => {
                    self.register_simulation_order(&order)?;
                    (202, serde_json::to_value(order)?)
                }
                Err(error) => (422, json!({"error":error})),
            },
            "/v1/intents/submit" => {
                match serde_json::from_str::<CommandEnvelope<IntentPayload>>(body)
                    .map_err(|error| error.to_string())
                    .and_then(|command| {
                        if command.schema_version != 1
                            || command.command_id.trim().is_empty()
                            || command.idempotency_key.trim().is_empty()
                            || command.operation != "execution.submit_intent"
                            || command.strategy_id != command.payload.intent.strategy_id
                            || command.instance_id != command.payload.intent.instance_id
                            || (!command.launch_id.is_empty()
                                && command.launch_id != command.payload.intent.launch_id)
                        {
                            return Err("invalid execution intent command envelope".into());
                        }
                        self.application
                            .submit_intent_with_idempotency(
                                command.payload.intent,
                                command.idempotency_key,
                            )
                            .map_err(|error| error.to_string())
                    }) {
                    Ok((intent, duplicate)) => {
                        for order_id in &intent.order_ids {
                            if let Some(order) = self
                                .application
                                .orders(None)
                                .into_iter()
                                .find(|order| order.order_id.as_str() == order_id.as_str())
                            {
                                self.register_simulation_order(&order)?;
                            }
                        }
                        (
                            202,
                            json!({"schema_version":1,"status":if duplicate { "duplicate" } else { "accepted" },"result":intent}),
                        )
                    }
                    Err(error) => (
                        422,
                        json!({"schema_version":1,"status":"rejected","error":{"code":"execution.intent_invalid","message":error,"retryable":false}}),
                    ),
                }
            }
            "/v1/backtest/market" => {
                match serde_json::from_str::<kairos_market_contract::model::MarketObservation>(body)
                    .map_err(|error| error.to_string())
                    .and_then(|event| self.apply_simulated_market(event))
                {
                    Ok(fills) => (200, json!({"fills": fills})),
                    Err(error) => (422, json!({"error": error})),
                }
            }
            "/v1/intents/cancel" => match serde_json::from_str::<CancelIntent>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .cancel_intent(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(intent) => (
                    202,
                    json!({"schema_version":1,"status":"cancel_requested","result":intent}),
                ),
                Err(error) => (
                    422,
                    json!({"schema_version":1,"status":"rejected","error":{"code":"execution.intent_cancel_invalid","message":error,"retryable":false}}),
                ),
            },
            "/v1/intents/expire" => match serde_json::from_str::<ExpireIntent>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .expire_intent(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(intent) => (
                    202,
                    json!({"schema_version":1,"status":"expired","result":intent}),
                ),
                Err(error) => (
                    422,
                    json!({"schema_version":1,"status":"rejected","error":{"code":"execution.intent_expire_invalid","message":error,"retryable":false}}),
                ),
            },
            "/v1/intents/refresh-quote" => {
                match serde_json::from_str::<CommandEnvelope<RefreshQuoteIntent>>(body)
                    .map_err(|error| error.to_string())
                    .and_then(|command| {
                        if command.schema_version != 1
                            || command.operation != "execution.refresh_quote"
                            || command.instance_id.trim().is_empty()
                        {
                            return Err("invalid quote refresh command envelope".into());
                        }
                        self.application
                            .refresh_quote_intent(command.payload)
                            .map_err(|error| error.to_string())
                    }) {
                    Ok(intent) => (
                        202,
                        json!({"schema_version":1,"status":"quote_refreshed","result":intent}),
                    ),
                    Err(error) => (
                        422,
                        json!({"schema_version":1,"status":"rejected","error":{"code":"execution.quote_refresh_invalid","message":error,"retryable":false}}),
                    ),
                }
            }
            "/v1/preview-submit" => match serde_json::from_str::<SubmitOrder>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .preview_submit(&request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (200, serde_json::to_value(order)?),
                Err(error) => (422, json!({"error":error})),
            },
            "/v1/cancel" => match serde_json::from_str::<CancelOrder>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .cancel(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (202, serde_json::to_value(order)?),
                Err(error) => (422, json!({"error":error})),
            },
            "/v1/replace" => match serde_json::from_str::<ReplaceOrder>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .replace(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (202, serde_json::to_value(order)?),
                Err(error) => (422, json!({"error":error})),
            },
            "/v1/fill" => match serde_json::from_str::<ExecutionFillReport>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .record_fill(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (202, serde_json::to_value(order)?),
                Err(error) => (422, json!({"error":error})),
            },
            STOP_PATH => {
                self.stopping = true;
                (202, json!({"status":"stopping"}))
            }
            _ => (404, json!({"error":"unknown execution control path"})),
        };
        info!(event = "control_response", component = "execution", path = %path, status, duration_ms = started.elapsed().as_millis(), "execution control response sent");
        Ok((status, payload))
    }

    fn flush_events(&mut self) -> Result<(), String> {
        let Some(audit) = self.audit.as_mut() else {
            return Ok(());
        };
        let durable_events = self
            .application
            .pending_outbox(1024)
            .map_err(|error| error.to_string())?;
        kairos_workspace::logging::record_gauge(
            "kairos.outbox.pending",
            durable_events.len() as u64,
        );
        let now_unix_nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos() as u64)
            .unwrap_or_default();
        let oldest_age_ms = durable_events
            .first()
            .map(|entry| now_unix_nanos.saturating_sub(entry.created_at_unix_nanos) / 1_000_000)
            .unwrap_or_default();
        kairos_workspace::logging::record_gauge("kairos.outbox.oldest_age", oldest_age_ms);
        let checkpoint_age_ms = self
            .application
            .latest_checkpoint_unix_nanos()
            .map_err(|error| error.to_string())?
            .map(|created_at| now_unix_nanos.saturating_sub(created_at) / 1_000_000)
            .unwrap_or_default();
        kairos_workspace::logging::record_gauge("kairos.checkpoint.age", checkpoint_age_ms);
        let mut acknowledged = Vec::with_capacity(durable_events.len());
        let mut durable_orders = Vec::new();
        let mut durable_intents = Vec::new();
        for entry in &durable_events {
            match &entry.event {
                ExecutionOutboxEvent::Order(event) => durable_orders.push(event.clone()),
                ExecutionOutboxEvent::Intent(event) => durable_intents.push(event.clone()),
            }
            acknowledged.push(entry.id);
        }
        audit.publish_batch(&durable_orders, &durable_intents)?;
        let events = self.application.drain_events();
        let intent_events = self.application.drain_intent_events();
        audit.publish_batch(&events, &intent_events)?;
        if !events.is_empty() || !intent_events.is_empty() {
            info!(
                event = "execution_audit_flushed",
                component = "execution",
                order_event_count = events.len(),
                intent_event_count = intent_events.len(),
                "execution audit events flushed"
            );
        }
        self.application
            .acknowledge_outbox(&acknowledged)
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    fn audit_events(
        &mut self,
        query: ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, crate::application::ExecutionError> {
        if let Some(audit) = self.audit.as_mut() {
            audit
                .query(&query)
                .map_err(crate::application::ExecutionError::Persistence)
        } else {
            self.application.audit_events(query)
        }
    }
}

fn query_value(query: &str, key: &str) -> Option<String> {
    query
        .split('&')
        .find_map(|part| part.strip_prefix(&format!("{key}=")).map(str::to_owned))
}

fn remote_order_event_from_envelope(
    envelope: ExternalEventEnvelope<ExternalExecutionEvent>,
) -> RemoteOrderEvent {
    let event_id = envelope.provider_event_id.clone().unwrap_or_else(|| {
        format!(
            "{}:{:?}:{}",
            envelope.payload.order_id,
            envelope.payload.status,
            envelope.payload.occurred_at_unix_nanos.get()
        )
    });
    let event = envelope.payload;
    RemoteOrderEvent {
        event_id,
        connection_id: envelope.binding_id,
        event: crate::application::RemoteOrderUpdate {
            order_id: event.order_id,
            symbol: event.symbol,
            status: crate::application::service::remote_status(&format!("{:?}", event.status)),
            fill_quantity: event
                .fill_quantity
                .and_then(|value| format_decimal(value).parse().ok()),
            fill_price: event
                .fill_price
                .and_then(|value| format_decimal(value).parse().ok()),
            execution_id: event.execution_id,
            fee_currency: event.fee_currency,
            fee_amount: event
                .fee_amount
                .and_then(|value| format_decimal(value).parse().ok()),
            occurred_at_unix_nanos: event.occurred_at_unix_nanos,
            reason: event.reason,
        },
    }
}

fn format_decimal(value: kairos_integration::application::DecimalValue) -> String {
    if value.scale == 0 {
        return value.mantissa.to_string();
    }
    let negative = value.mantissa < 0;
    let digits = value.mantissa.unsigned_abs().to_string();
    let scale = usize::from(value.scale);
    let body = if digits.len() <= scale {
        format!("0.{}{}", "0".repeat(scale - digits.len()), digits)
    } else {
        let split = digits.len() - scale;
        format!("{}.{}", &digits[..split], &digits[split..])
    };
    if negative {
        format!("-{body}")
    } else {
        body
    }
}

fn remote_query(query: &str) -> RemoteOrderQuery {
    RemoteOrderQuery {
        binding_id: query_value(query, "binding_id"),
        symbol: query_value(query, "symbol")
            .and_then(|value| kairos_domain_types::Symbol::new(value).ok()),
        order_id: query_value(query, "order_id")
            .and_then(|value| kairos_domain_types::OrderId::new(value).ok()),
        limit: query_value(query, "limit").and_then(|value| value.parse().ok()),
        since_unix_nanos: query_value(query, "since_unix_nanos")
            .and_then(|value| value.parse::<u64>().ok())
            .map(kairos_domain_types::UnixNanos::from),
    }
}

fn audit_query(query: &str) -> ExecutionAuditQuery {
    ExecutionAuditQuery {
        order_id: query_value(query, "order_id")
            .and_then(|value| kairos_domain_types::OrderId::new(value).ok()),
        remote_order_id: query_value(query, "remote_order_id")
            .and_then(|value| kairos_domain_types::RemoteOrderId::new(value).ok()),
        status: query_value(query, "status"),
        limit: query_value(query, "limit").and_then(|value| value.parse().ok()),
        ..Default::default()
    }
}
fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}
async fn execution_http_handler(
    State(ingress): State<ExecutionIngress>,
    request: Request,
) -> Response {
    let started = Instant::now();
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
    let span = tracing::info_span!(
        "execution.control_request",
        component = "execution",
        method = %method,
        path = %path,
        status = tracing::field::Empty,
        duration_ms = tracing::field::Empty,
        result = tracing::field::Empty,
        error_code = tracing::field::Empty,
        retryable = tracing::field::Empty,
        trace_id = tracing::field::Empty,
        span_id = tracing::field::Empty
    );
    kairos_workspace::logging::record_counter("kairos.control.request", 1);
    kairos_workspace::logging::record_counter("kairos.operation", 1);
    kairos_workspace::logging::set_remote_parent(&span, request.headers());
    let response = execution_http_handler_inner(ingress, request)
        .instrument(span.clone())
        .await;
    let duration_ms = started.elapsed().as_secs_f64() * 1_000.0;
    span.record("status", response.status().as_u16());
    span.record("duration_ms", duration_ms);
    span.record(
        "result",
        if response.status().is_success() {
            "accepted"
        } else {
            "rejected"
        },
    );
    kairos_workspace::logging::record_duration_ms("kairos.control.request.duration", duration_ms);
    kairos_workspace::logging::record_duration_ms("kairos.operation.duration", duration_ms);
    if response.status().is_server_error() {
        kairos_workspace::logging::mark_span_error(&span, "control.internal_error", true);
        kairos_workspace::logging::record_counter("kairos.control.request.failed", 1);
        kairos_workspace::logging::record_counter("kairos.operation.failed", 1);
    } else if !response.status().is_success() {
        span.record("error_code", "control.request_rejected");
        span.record("retryable", false);
    }
    tracing::info!(parent: &span, event = "control_request_completed", component = "execution", duration_ms, result = if response.status().is_success() { "accepted" } else { "rejected" }, "execution control request completed");
    response
}

async fn execution_http_handler_inner(ingress: ExecutionIngress, request: Request) -> Response {
    let target = request
        .uri()
        .path_and_query()
        .map(|value| value.as_str().to_owned())
        .unwrap_or_else(|| request.uri().path().to_owned());
    let body = match to_bytes(
        request.into_body(),
        kairos_workspace::control::MAX_HTTP_BODY_BYTES,
    )
    .await
    {
        Ok(body) => body.to_vec(),
        Err(_) => {
            return (
                StatusCode::PAYLOAD_TOO_LARGE,
                Json(json!({"error":"request body too large"})),
            )
                .into_response()
        }
    };
    let (response_sender, response_receiver) = oneshot::channel();
    let class = request_class(&target);
    let sender = match class {
        RequestClass::Command => &ingress.command_tx,
        RequestClass::Query => &ingress.query_tx,
    };
    match class {
        RequestClass::Command => {
            ingress
                .metrics
                .pending_commands
                .fetch_add(1, Ordering::Relaxed);
        }
        RequestClass::Query => {
            ingress
                .metrics
                .pending_queries
                .fetch_add(1, Ordering::Relaxed);
        }
    }
    if sender
        .try_send(ExecutionHttpRequest {
            target,
            body,
            response: response_sender,
        })
        .is_err()
    {
        match class {
            RequestClass::Command => {
                ingress
                    .metrics
                    .pending_commands
                    .fetch_sub(1, Ordering::Relaxed);
            }
            RequestClass::Query => {
                ingress
                    .metrics
                    .pending_queries
                    .fetch_sub(1, Ordering::Relaxed);
            }
        }
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"execution process is stopping"})),
        )
            .into_response();
    }
    match response_receiver.await {
        Ok(Ok((status, payload))) => (
            StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            Json(payload),
        )
            .into_response(),
        Ok(Err(error)) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error":error})),
        )
            .into_response(),
        Err(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"execution process did not respond"})),
        )
            .into_response(),
    }
}

fn request_class(target: &str) -> RequestClass {
    let path = target.split_once('?').map_or(target, |(path, _)| path);
    match path {
        "/v1/submit"
        | "/v1/intents/submit"
        | "/v1/intents/cancel"
        | "/v1/intents/expire"
        | "/v1/intents/refresh-quote"
        | "/v1/cancel"
        | "/v1/replace"
        | "/v1/fill"
        | "/v1/link-unknown-remote"
        | STOP_PATH => RequestClass::Command,
        _ => RequestClass::Query,
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::{request_class, ExecutionApplication, ExecutionProcess, RequestClass};
    use kairos_integration::application::{
        AsyncOrderEventSource, ExternalEventEnvelope, ExternalExecutionEvent, IntegrationError,
    };
    use kairos_integration::application::{
        ConnectionDescriptor, ConnectionHealth, ConnectionLifecycle, ConnectionState, OrderSide,
        ParticipantKind, ParticipantRef,
    };
    use kairos_integration::blocking::OrderEventSource;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{mpsc, Arc};

    #[test]
    fn mutation_and_query_ingress_are_classified_separately() {
        assert!(matches!(request_class("/v1/submit"), RequestClass::Command));
        assert!(matches!(
            request_class("/v1/intents/submit?x=1"),
            RequestClass::Command
        ));
        assert!(matches!(request_class("/v1/orders"), RequestClass::Query));
        assert!(matches!(request_class("/v1/health"), RequestClass::Query));
    }

    #[test]
    fn exchange_event_identity_is_idempotent_at_the_process_boundary() {
        let application = ExecutionApplication::with_dependencies("execution", None, None)
            .expect("fixture application");
        let mut process =
            ExecutionProcess::new(application, PathBuf::from("/tmp/execution-test.sock"));
        assert!(process.accept_exchange_event("fill-1"));
        assert!(!process.accept_exchange_event("fill-1"));
        assert!(process.accept_exchange_event("fill-2"));
    }

    struct ReconnectingStream {
        state: ConnectionState,
        calls: usize,
        reconnects: Arc<AtomicUsize>,
    }

    impl ReconnectingStream {
        fn new(reconnects: Arc<AtomicUsize>) -> Self {
            let identity = ConnectionDescriptor::new(
                "execution.test.reconnecting-stream",
                ParticipantRef::new(ParticipantKind::Exchange, "fixture").unwrap(),
                "execution-stream",
            )
            .unwrap();
            Self {
                state: ConnectionState::new(identity),
                calls: 0,
                reconnects,
            }
        }
    }

    impl OrderEventSource for ReconnectingStream {
        fn connect_channel(&mut self) -> Result<(), IntegrationError> {
            self.state.lifecycle = ConnectionLifecycle::Ready;
            Ok(())
        }

        fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
            self.state.lifecycle = ConnectionLifecycle::Stopped;
            Ok(())
        }

        fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
            self.reconnects.fetch_add(1, Ordering::SeqCst);
            self.connect_channel()
        }

        fn channel_health(&self) -> ConnectionHealth {
            ConnectionHealth {
                lifecycle: self.state.lifecycle,
                healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
                authenticated: true,
                last_error: None,
            }
        }

        fn try_next_order_event(
            &mut self,
        ) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError>
        {
            self.calls += 1;
            match self.calls {
                1 => Err(IntegrationError::Transport(
                    "simulated private stream disconnect".into(),
                )),
                2 => {
                    let event = ExternalExecutionEvent {
                        order_id: kairos_domain_types::OrderId::new("local-recovered-order")
                            .unwrap(),
                        symbol: kairos_domain_types::Symbol::new("BTCUSDT").unwrap(),
                        status: kairos_domain_types::OrderStatus::Filled,
                        side: Some(OrderSide::Buy),
                        order_type: None,
                        quantity: None,
                        limit_price: None,
                        filled_quantity: None,
                        remaining_quantity: None,
                        fill_quantity: None,
                        fill_price: None,
                        execution_id: Some(
                            kairos_domain_types::FillId::new("recovered-event-1").unwrap(),
                        ),
                        fee_currency: None,
                        fee_amount: None,
                        occurred_at_unix_nanos: 42.into(),
                        reason: "received after reconnect".into(),
                    };
                    Ok(Some(ExternalEventEnvelope {
                        binding_id: self.state.identity.binding_id.clone(),
                        channel_id: "execution.test.reconnecting-stream.orders".into(),
                        channel_epoch: self.reconnects.load(Ordering::SeqCst) as u64 + 1,
                        provider_event_id: Some("recovered-event-1".into()),
                        provider_sequence: None,
                        observed_at_unix_nanos: event.occurred_at_unix_nanos,
                        received_at_unix_nanos: event.occurred_at_unix_nanos,
                        payload: event,
                    }))
                }
                _ => Ok(None),
            }
        }
    }

    #[test]
    fn stream_consumer_reconnects_after_disconnect_and_delivers_next_event() {
        let reconnects = Arc::new(AtomicUsize::new(0));
        let stream = ReconnectingStream::new(Arc::clone(&reconnects));
        let application = ExecutionApplication::with_dependencies_and_query_and_stream(
            "execution",
            None,
            None,
            Some(Box::new(stream)),
            None,
        )
        .expect("fixture application");
        let mut process =
            ExecutionProcess::new(application, PathBuf::from("/tmp/execution-test.sock"));
        let (sender, receiver) = mpsc::sync_channel(1);
        let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let handle = process
            .start_stream_consumer(sender, Arc::clone(&stop), Arc::clone(&process.metrics))
            .expect("stream task");
        let event = receiver
            .recv_timeout(std::time::Duration::from_secs(2))
            .expect("event after reconnect");
        stop.store(true, Ordering::Release);
        handle.join().expect("stream task join");
        assert_eq!(event.event_id, "recovered-event-1");
        assert_eq!(reconnects.load(Ordering::SeqCst), 1);
    }

    struct AsyncReconnectingStream {
        calls: usize,
        reconnects: Arc<AtomicUsize>,
    }

    impl AsyncOrderEventSource for AsyncReconnectingStream {
        async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
            Ok(())
        }

        async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
            Ok(())
        }

        async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
            self.reconnects.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }

        fn channel_health(&self) -> ConnectionHealth {
            ConnectionHealth {
                lifecycle: ConnectionLifecycle::Ready,
                healthy: true,
                authenticated: true,
                last_error: None,
            }
        }

        async fn next_order_event(
            &mut self,
        ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
            self.calls += 1;
            match self.calls {
                1 => Err(IntegrationError::Transport(
                    "simulated async stream disconnect".into(),
                )),
                2 => {
                    let event = ExternalExecutionEvent {
                        order_id: kairos_domain_types::OrderId::new("async-recovered-order")
                            .unwrap(),
                        symbol: kairos_domain_types::Symbol::new("BTCUSDT").unwrap(),
                        status: kairos_domain_types::OrderStatus::Filled,
                        side: Some(OrderSide::Buy),
                        order_type: None,
                        quantity: None,
                        limit_price: None,
                        filled_quantity: None,
                        remaining_quantity: None,
                        fill_quantity: None,
                        fill_price: None,
                        execution_id: Some(
                            kairos_domain_types::FillId::new("async-recovered-event").unwrap(),
                        ),
                        fee_currency: None,
                        fee_amount: None,
                        occurred_at_unix_nanos: 43.into(),
                        reason: "received by async runtime".into(),
                    };
                    Ok(ExternalEventEnvelope {
                        binding_id: "execution.test.async-stream".into(),
                        channel_id: "execution.test.async-stream.orders".into(),
                        channel_epoch: 2,
                        provider_event_id: Some("async-recovered-event".into()),
                        provider_sequence: None,
                        observed_at_unix_nanos: event.occurred_at_unix_nanos,
                        received_at_unix_nanos: event.occurred_at_unix_nanos,
                        payload: event,
                    })
                }
                _ => std::future::pending().await,
            }
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn async_stream_consumer_awaits_without_polling_and_reconnects() {
        let reconnects = Arc::new(AtomicUsize::new(0));
        let application = ExecutionApplication::with_dependencies("execution", None, None)
            .expect("fixture application");
        let mut process =
            ExecutionProcess::new(application, PathBuf::from("/tmp/execution-async-test.sock"))
                .with_async_execution_stream(Some(AsyncReconnectingStream {
                    calls: 0,
                    reconnects: Arc::clone(&reconnects),
                }));
        let (sender, receiver) = mpsc::sync_channel(1);
        let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
        let mut handles =
            process.start_async_stream_consumers(sender, shutdown_rx, Arc::clone(&process.metrics));
        assert_eq!(handles.len(), 1);
        let handle = handles.pop().expect("async stream task");
        let event = tokio::task::spawn_blocking(move || {
            receiver.recv_timeout(std::time::Duration::from_secs(2))
        })
        .await
        .unwrap()
        .expect("event after async reconnect");
        shutdown.send(true).unwrap();
        handle.await.unwrap();
        assert_eq!(event.event_id, "async-recovered-event");
        assert_eq!(reconnects.load(Ordering::SeqCst), 1);
    }
}
