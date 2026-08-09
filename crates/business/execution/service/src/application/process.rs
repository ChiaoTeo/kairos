use crate::application::{
    BacktestApplication, BacktestRequest, CancelOrder, ExecuteStrategyIntent, ExecutionApplication,
    ExecutionAuditEvent, ExecutionAuditQuery, ExecutionAuditSink, ExecutionFillReport,
    RemoteOrderQuery, ReplaceOrder, SubmitOrder,
};
use crate::composition::{SharedExecutionSnapshotPublisher, SharedIntentSnapshotPublisher};
use crate::services::actor::VenueOrderEvent;
use crate::services::gateway::{QueuedOrderEntry, QueuedOrderQuery};
use crate::services::persistence::ExecutionOutboxEvent;
use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json, Router,
};
use kairos_workspace::runtime::{HEALTH_PATH, SNAPSHOT_PATH, STOP_PATH};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use std::time::Instant;
use tokio::net::UnixListener;
use tokio::sync::oneshot;
use tracing::{debug, info};

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

pub struct ExecutionProcess {
    application: ExecutionApplication,
    socket_path: PathBuf,
    audit: Option<Box<dyn ExecutionAuditSink>>,
    stopping: bool,
    last_published_generation: Option<u64>,
    snapshot_publisher: Option<SharedExecutionSnapshotPublisher>,
    intent_snapshot_publisher: Option<SharedIntentSnapshotPublisher>,
    seen_venue_events: std::collections::HashSet<String>,
    venue_event_order: std::collections::VecDeque<String>,
    metrics: std::sync::Arc<RuntimeMetrics>,
}

struct ExecutionHttpRequest {
    target: String,
    body: Vec<u8>,
    response: oneshot::Sender<Result<(u16, Value), String>>,
}

const VENUE_BATCH_LIMIT: usize = 64;

#[derive(Default)]
struct RuntimeMetrics {
    pending_commands: AtomicUsize,
    pending_queries: AtomicUsize,
    pending_venue_events: AtomicUsize,
    venue_events_applied: AtomicU64,
    venue_batches: AtomicU64,
    max_venue_batch: AtomicUsize,
    state_loop_errors: AtomicU64,
    last_operation_micros: AtomicU64,
}

impl RuntimeMetrics {
    fn snapshot(&self) -> Value {
        json!({
            "pending_commands": self.pending_commands.load(Ordering::Relaxed),
            "pending_queries": self.pending_queries.load(Ordering::Relaxed),
            "pending_venue_events": self.pending_venue_events.load(Ordering::Relaxed),
            "venue_events_applied": self.venue_events_applied.load(Ordering::Relaxed),
            "venue_batches": self.venue_batches.load(Ordering::Relaxed),
            "max_venue_batch": self.max_venue_batch.load(Ordering::Relaxed),
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

impl ExecutionProcess {
    pub fn new(application: ExecutionApplication, socket_path: impl Into<PathBuf>) -> Self {
        Self {
            application,
            socket_path: socket_path.into(),
            audit: None,
            stopping: false,
            last_published_generation: None,
            snapshot_publisher: None,
            intent_snapshot_publisher: None,
            seen_venue_events: std::collections::HashSet::new(),
            venue_event_order: std::collections::VecDeque::new(),
            metrics: std::sync::Arc::new(RuntimeMetrics::default()),
        }
    }

    pub fn with_audit(
        application: ExecutionApplication,
        socket_path: impl Into<PathBuf>,
        audit: impl ExecutionAuditSink + 'static,
    ) -> Self {
        Self {
            application,
            socket_path: socket_path.into(),
            audit: Some(Box::new(audit)),
            stopping: false,
            last_published_generation: None,
            snapshot_publisher: None,
            intent_snapshot_publisher: None,
            seen_venue_events: std::collections::HashSet::new(),
            venue_event_order: std::collections::VecDeque::new(),
            metrics: std::sync::Arc::new(RuntimeMetrics::default()),
        }
    }

    pub fn with_snapshot_publisher(mut self, publisher: SharedExecutionSnapshotPublisher) -> Self {
        self.snapshot_publisher = Some(publisher);
        self
    }

    pub fn with_intent_snapshot_publisher(
        mut self,
        publisher: SharedIntentSnapshotPublisher,
    ) -> Self {
        self.intent_snapshot_publisher = Some(publisher);
        self
    }

    pub async fn run(mut self) -> Result<(), Box<dyn std::error::Error>> {
        info!(event = "process_starting", component = "execution", socket = %self.socket_path.display(), "execution process starting");
        remove_socket(&self.socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket_path)?;
        let (command_sender, command_receiver) = std::sync::mpsc::sync_channel(256);
        let (query_sender, query_receiver) = std::sync::mpsc::sync_channel(512);
        let (venue_sender, venue_receiver) = std::sync::mpsc::sync_channel(4096);
        // Keep the venue mailbox alive even when no provider stream is
        // configured.  A missing stream is a valid degraded mode, not a
        // signal for the state owner to shut down.
        let _venue_sender_guard = venue_sender.clone();
        let stream_stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let stream_task = self.start_stream_consumer(
            venue_sender,
            stream_stop.clone(),
            std::sync::Arc::clone(&self.metrics),
        );
        let gateway_stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let gateway_task = self.start_gateway_worker(gateway_stop.clone());
        let query_gateway_stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let query_gateway_task = self.start_query_gateway_worker(query_gateway_stop.clone());
        let router = Router::new()
            .fallback(execution_http_handler)
            .with_state(ExecutionIngress {
                command_tx: command_sender,
                query_tx: query_sender,
                metrics: std::sync::Arc::clone(&self.metrics),
            });
        let server = tokio::spawn(async move { axum::serve(listener, router).await });
        info!(event = "process_ready", component = "execution", socket = %self.socket_path.display(), "execution control socket ready");
        let socket_path = self.socket_path.clone();
        let state_task = tokio::task::spawn_blocking(move || {
            self.state_loop(command_receiver, query_receiver, venue_receiver)
        });
        state_task
            .await
            .map_err(|error| format!("execution state task failed: {error}"))?
            .map_err(|error| error.to_string())?;
        stream_stop.store(true, std::sync::atomic::Ordering::Release);
        if let Some(task) = stream_task {
            if task.is_finished() {
                let _ = task.join();
            } else {
                tracing::warn!(
                    event = "venue_stream_shutdown_deferred",
                    component = "execution",
                    "venue stream did not stop before process teardown"
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
        if self.last_published_generation == Some(snapshot.generation) {
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
            generation = snapshot.generation,
            event_sequence = snapshot.event_sequence,
            order_count = snapshot.orders.len(),
            intent_count = snapshot.intents.len(),
            "execution snapshots published"
        );
        self.last_published_generation = Some(snapshot.generation);
        Ok(())
    }

    fn accept_venue_event(&mut self, event_id: &str) -> bool {
        const MAX_SEEN_VENUE_EVENTS: usize = 100_000;
        if !self.seen_venue_events.insert(event_id.to_owned()) {
            return false;
        }
        self.venue_event_order.push_back(event_id.to_owned());
        if self.venue_event_order.len() > MAX_SEEN_VENUE_EVENTS {
            if let Some(expired) = self.venue_event_order.pop_front() {
                self.seen_venue_events.remove(&expired);
            }
        }
        true
    }

    fn start_stream_consumer(
        &mut self,
        sender: SyncSender<VenueOrderEvent>,
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
                match stream.next_execution_event() {
                    Ok(Some(event)) => {
                        let event_id = event.execution_id.clone().unwrap_or_else(|| {
                            format!(
                                "{}:{}:{}",
                                event.order_id, event.status, event.occurred_at_unix_nanos
                            )
                        });
                        let message = VenueOrderEvent {
                            event_id,
                            connection_id: "execution.venue.private-stream".into(),
                            event: crate::application::VenueOrderUpdate {
                                order_id: event.order_id,
                                symbol: event.symbol,
                                status: event.status,
                                fill_quantity: event.fill_quantity.map(format_decimal),
                                fill_price: event.fill_price.map(format_decimal),
                                execution_id: event.execution_id,
                                fee_currency: event.fee_currency,
                                fee_amount: event.fee_amount.map(format_decimal),
                                occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                                reason: event.reason,
                            },
                        };
                        metrics.pending_venue_events.fetch_add(1, Ordering::Relaxed);
                        if sender.send(message).is_err() {
                            metrics.pending_venue_events.fetch_sub(1, Ordering::Relaxed);
                            break;
                        }
                    }
                    Ok(None) => std::thread::sleep(Duration::from_millis(10)),
                    Err(error) => {
                        tracing::warn!(event = "venue_stream_error", component = "execution", error = %error, "venue stream read failed");
                        std::thread::sleep(Duration::from_millis(250));
                    }
                }
            }
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

    fn start_query_gateway_worker(
        &mut self,
        stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
    ) -> Option<std::thread::JoinHandle<()>> {
        let connection = self.application.take_order_query()?;
        let (proxy, worker) = QueuedOrderQuery::channel(connection, 128);
        self.application.install_order_query(Box::new(proxy));
        Some(std::thread::spawn(move || worker.run(stop)))
    }

    fn state_loop(
        mut self,
        command_receiver: Receiver<ExecutionHttpRequest>,
        query_receiver: Receiver<ExecutionHttpRequest>,
        venue_receiver: Receiver<VenueOrderEvent>,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.flush_events()?;
        self.publish_snapshots()?;
        while !self.stopping {
            let mut venue_batch = 0;
            while venue_batch < VENUE_BATCH_LIMIT {
                match venue_receiver.try_recv() {
                    Ok(event) => {
                        self.metrics
                            .pending_venue_events
                            .fetch_sub(1, Ordering::Relaxed);
                        self.apply_venue_event(event)?;
                        venue_batch += 1;
                    }
                    Err(std::sync::mpsc::TryRecvError::Empty) => break,
                    Err(std::sync::mpsc::TryRecvError::Disconnected) => return Ok(()),
                }
            }
            if venue_batch > 0 {
                self.flush_events()?;
                self.publish_snapshots()?;
                self.metrics.venue_batches.fetch_add(1, Ordering::Relaxed);
                self.metrics
                    .max_venue_batch
                    .fetch_max(venue_batch, Ordering::Relaxed);
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

            match venue_receiver.recv_timeout(std::time::Duration::from_millis(10)) {
                Ok(event) => {
                    self.metrics
                        .pending_venue_events
                        .fetch_sub(1, Ordering::Relaxed);
                    self.apply_venue_event(event)?;
                    let mut batch = 1;
                    while batch < VENUE_BATCH_LIMIT {
                        match venue_receiver.try_recv() {
                            Ok(event) => {
                                self.metrics
                                    .pending_venue_events
                                    .fetch_sub(1, Ordering::Relaxed);
                                self.apply_venue_event(event)?;
                                batch += 1;
                            }
                            Err(std::sync::mpsc::TryRecvError::Empty)
                            | Err(std::sync::mpsc::TryRecvError::Disconnected) => break,
                        }
                    }
                    self.flush_events()?;
                    self.publish_snapshots()?;
                    self.metrics.venue_batches.fetch_add(1, Ordering::Relaxed);
                    self.metrics
                        .max_venue_batch
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

    fn apply_venue_event(
        &mut self,
        event: VenueOrderEvent,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.metrics
            .venue_events_applied
            .fetch_add(1, Ordering::Relaxed);
        if self.accept_venue_event(&event.event_id) {
            if let Err(error) = self.application.apply_remote_execution_event(event.event) {
                tracing::warn!(event = "venue_event_rejected", component = "execution", error = %error, "venue event was not applied");
            }
        }
        Ok(())
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
            "/v1/intent-events" => (
                200,
                json!({"events": self.application.intent_events(query_value(query, "intent_id").as_deref())}),
            ),
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
            "/v1/stream/next" | "/v1/stream/consume" => (
                410,
                json!({"error":"venue execution streams are consumed internally by the execution process"}),
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
            "/v1/submit" => match serde_json::from_str::<SubmitOrder>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .submit(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (202, serde_json::to_value(order)?),
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
                    Ok((intent, duplicate)) => (
                        202,
                        json!({"schema_version":1,"status":if duplicate { "duplicate" } else { "accepted" },"result":intent}),
                    ),
                    Err(error) => (
                        422,
                        json!({"schema_version":1,"status":"rejected","error":{"code":"execution.intent_invalid","message":error,"retryable":false}}),
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

fn format_decimal(value: kairos_integration::DecimalValue) -> String {
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
        symbol: query_value(query, "symbol"),
        order_id: query_value(query, "order_id"),
        limit: query_value(query, "limit").and_then(|value| value.parse().ok()),
        since_unix_millis: query_value(query, "since_unix_millis")
            .and_then(|value| value.parse().ok()),
    }
}

fn audit_query(query: &str) -> ExecutionAuditQuery {
    ExecutionAuditQuery {
        order_id: query_value(query, "order_id"),
        venue_order_id: query_value(query, "venue_order_id"),
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
        "/v1/submit" | "/v1/intents/submit" | "/v1/cancel" | "/v1/replace" | "/v1/fill"
        | STOP_PATH => RequestClass::Command,
        _ => RequestClass::Query,
    }
}

#[cfg(test)]
mod tests {
    use super::{request_class, RequestClass};

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
}
