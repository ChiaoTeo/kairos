//! Account process. It owns lifecycle and control transport, not
//! account business state; the latter remains inside AccountActor.

use std::path::{Path, PathBuf};
use std::time::Duration;
use std::time::Instant;
use std::time::SystemTime;

use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json, Router,
};
use serde_json::{json, Value};
use tokio::net::UnixListener;
use tokio::sync::mpsc::Sender;
use tokio::sync::{mpsc, oneshot};
use tokio::time::{self, MissedTickBehavior};

use crate::application::{
    AccountApplication, AccountDataQuery, AccountRefreshReport, AccountsSnapshot, MarkToMarket,
    RefreshAccount,
};
use crate::domain::{AccountFill, AccountOrderObservation};
use crate::services::integration::AccountAsyncEventSource;
use kairos_domain_types::AccountId;
use kairos_integration::application::IntegrationError;
use kairos_workspace::runtime::{HEALTH_PATH, SNAPSHOT_PATH, STOP_PATH};
use tracing::{debug, error, info, warn, Instrument};

pub struct AccountProcess {
    application: AccountApplication,
    account_id: String,
    socket_path: PathBuf,
    refresh_interval: Duration,
    health_file: Option<PathBuf>,
    publisher: Option<Box<dyn AccountSnapshotPublisher>>,
    stop_requested: bool,
    last_error: Option<String>,
    last_refresh: Option<AccountRefreshReport>,
    stream_enabled: bool,
    lease_file: Option<PathBuf>,
    lease_instance_id: Option<String>,
    snapshot_dirty: bool,
    refresh_started: Option<Instant>,
    async_account_streams: Vec<AccountAsyncEventSource>,
    async_provider_tasks: Vec<tokio::task::JoinHandle<()>>,
}

/// Application-owned publication capability. Concrete transport publishers
/// are selected by composition and injected into the process facade.
pub trait AccountSnapshotPublisher: Send {
    fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String>;
}

struct AccountHttpRequest {
    target: String,
    body: Vec<u8>,
    response: oneshot::Sender<Result<(u16, Value), String>>,
}

impl AccountProcess {
    pub fn new(
        application: AccountApplication,
        account_id: impl Into<String>,
        socket_path: impl Into<PathBuf>,
        refresh_interval: Duration,
        health_file: Option<PathBuf>,
        publisher: Option<Box<dyn AccountSnapshotPublisher>>,
    ) -> Result<Self, String> {
        let account_id = account_id.into();
        if account_id.trim().is_empty() {
            return Err("account process account_id is required".into());
        }
        if refresh_interval.is_zero() {
            return Err("account refresh interval must be positive".into());
        }
        let stream_enabled = application.has_stream();
        Ok(Self {
            application,
            account_id,
            socket_path: socket_path.into(),
            refresh_interval,
            health_file,
            publisher,
            stop_requested: false,
            last_error: None,
            last_refresh: None,
            stream_enabled,
            lease_file: None,
            lease_instance_id: None,
            snapshot_dirty: true,
            refresh_started: None,
            async_account_streams: Vec::new(),
            async_provider_tasks: Vec::new(),
        })
    }
}

impl AccountProcess {
    pub(crate) fn with_async_account_streams(
        mut self,
        streams: Vec<AccountAsyncEventSource>,
    ) -> Self {
        self.async_account_streams = streams;
        self
    }

    pub fn with_async_provider_tasks(mut self, tasks: Vec<tokio::task::JoinHandle<()>>) -> Self {
        self.async_provider_tasks = tasks;
        self
    }

    pub fn with_trade_lease(
        mut self,
        lease_file: impl Into<PathBuf>,
        instance_id: impl Into<String>,
    ) -> Self {
        self.lease_file = Some(lease_file.into());
        self.lease_instance_id = Some(instance_id.into());
        self
    }

    pub fn application(&self) -> &AccountApplication {
        &self.application
    }

    pub async fn run(mut self) -> Result<(), Box<dyn std::error::Error>> {
        info!(event = "process_starting", component = "account", account_id = %self.account_id, socket = %self.socket_path.display(), refresh_interval_ms = self.refresh_interval.as_millis(), stream_enabled = self.stream_enabled, "account process starting");
        remove_socket(&self.socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket_path)?;
        const CONTROL_QUEUE_CAPACITY: usize = 256;
        const STREAM_BATCH_SIZE: usize = 64;
        const REFRESH_RESULT_POLL_MS: u64 = 10;
        let (sender, mut receiver) = mpsc::channel(CONTROL_QUEUE_CAPACITY);
        let router = Router::new()
            .fallback(account_http_handler)
            .with_state(sender);
        let server = tokio::spawn(async move { axum::serve(listener, router).await });
        kairos_workspace::logging::record_gauge("kairos.process.ready", 1);
        info!(event = "process_ready", component = "account", socket = %self.socket_path.display(), "account control socket ready");
        let mut interval = time::interval(self.refresh_interval);
        interval.set_missed_tick_behavior(MissedTickBehavior::Delay);
        let mut refresh_result_poll = time::interval(Duration::from_millis(REFRESH_RESULT_POLL_MS));
        refresh_result_poll.set_missed_tick_behavior(MissedTickBehavior::Skip);
        let stream_wakeup = self.application.stream_wakeup();
        let (async_stream_shutdown, async_stream_shutdown_rx) = tokio::sync::watch::channel(false);
        let async_stream_overflow = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let async_stream_overflow_wakeup = std::sync::Arc::new(tokio::sync::Notify::new());
        let (async_event_sender, mut async_event_receiver) = mpsc::channel(256);
        let async_stream_tasks = self.start_async_stream_consumers(
            async_event_sender,
            async_stream_shutdown_rx,
            std::sync::Arc::clone(&async_stream_overflow),
            std::sync::Arc::clone(&async_stream_overflow_wakeup),
        );
        let async_stream_enabled = !async_stream_tasks.is_empty();
        interval.tick().await;
        self.schedule_refresh();
        if let Err(error) = self.publish_snapshot_if_dirty() {
            self.last_error = Some(error);
        }
        let _ = self
            .write_health(if self.lease_valid() {
                "ready"
            } else {
                "unavailable"
            })
            .await;
        while !self.stop_requested {
            tokio::select! {
                Some(request) = receiver.recv() => {
                    let response = self.handle_request(&request.target, &String::from_utf8_lossy(&request.body));
                    let _ = request.response.send(response.map_err(|error| error.to_string()));
                }
                _ = stream_wakeup.notified(), if self.stream_enabled => {
                    if let Some(error) = self.application.take_persistence_error() {
                        warn!(event = "account_persistence_failed", component = "account", error = %error, "account persistence worker reported an error");
                        self.last_error = Some(error);
                    }
                    let started = Instant::now();
                    match self.application.poll_stream_batch(STREAM_BATCH_SIZE) {
                        Ok(applied) if applied > 0 => {
                            info!(
                                event = "account_stream_batch_applied",
                                component = "account",
                                applied,
                                queue_depth = self.application.stream_queue_depth(),
                                duration_ms = started.elapsed().as_millis(),
                                "account stream batch applied"
                            );
                            self.snapshot_dirty = true;
                            if self.application.stream_queue_depth() > 0 {
                                // `Notify` intentionally coalesces permits. A
                                // full batch schedules one more drain so an
                                // already-buffered tail cannot wait for a new
                                // provider event.
                                stream_wakeup.notify_one();
                            }
                            if let Err(error) = self.publish_snapshot_if_dirty() {
                                error!(event = "snapshot_publish_failed", component = "account", error = %error, "account snapshot publication failed");
                                self.last_error = Some(error);
                            }
                        }
                        Ok(_) => {}
                        Err(error) => {
                            warn!(event = "stream_poll_failed", component = "account", error = %error, "account stream poll failed");
                            self.last_error = Some(error.to_string());
                            // Stream continuity is no longer trustworthy. A
                            // snapshot refresh, not continued delta guessing,
                            // is the recovery boundary.
                            self.schedule_refresh();
                        }
                    }
                }
                Some(result) = async_event_receiver.recv(), if async_stream_enabled => {
                    match result {
                        Ok(event) => match crate::services::integration::map_event(event)
                            .and_then(|event| self.application.apply_event(event).map_err(|error| error.to_string()))
                        {
                            Ok(applied) if applied > 0 => {
                                self.snapshot_dirty = true;
                                if let Err(error) = self.publish_snapshot_if_dirty() {
                                    self.last_error = Some(error);
                                }
                            }
                            Ok(_) => {}
                            Err(error) => {
                                warn!(event = "async_account_event_apply_failed", component = "account", error = %error, "async account event could not be applied");
                                self.last_error = Some(error);
                                self.schedule_refresh();
                            }
                        },
                        Err(error) => {
                            warn!(event = "async_account_stream_failed", component = "account", error = %error, "async account stream reported a continuity failure");
                            self.last_error = Some(error.to_string());
                            self.schedule_refresh();
                        }
                    }
                }
                _ = async_stream_overflow_wakeup.notified(), if async_stream_enabled => {
                    if async_stream_overflow.swap(false, std::sync::atomic::Ordering::AcqRel) {
                        let error = "async account event queue overflowed; snapshot resynchronization is required".to_string();
                        warn!(event = "async_account_stream_overflow", component = "account", error = %error, "async account stream continuity was lost");
                        self.last_error = Some(error);
                        self.schedule_refresh();
                    }
                }
                _ = interval.tick() => {
                    if let Some(error) = self.application.take_persistence_error() {
                        warn!(event = "account_persistence_failed", component = "account", error = %error, "account persistence worker reported an error");
                        self.last_error = Some(error);
                    }
                    self.drain_refresh();
                    self.schedule_refresh();
                    if let Err(error) = self.publish_snapshot_if_dirty() {
                        error!(event = "snapshot_publish_failed", component = "account", error = %error, "account snapshot publication failed");
                        self.last_error = Some(error);
                    }
                    let _ = self.write_health(if self.last_error.is_some() || !self.lease_valid() { "degraded" } else { "ready" }).await;
                }
                _ = refresh_result_poll.tick() => {
                    let refresh_changed = self.drain_refresh();
                    if let Err(error) = self.publish_snapshot_if_dirty() {
                        error!(event = "snapshot_publish_failed", component = "account", error = %error, "account snapshot publication failed");
                        self.last_error = Some(error);
                    }
                    if refresh_changed {
                        let _ = self.write_health(if self.last_error.is_some() || !self.lease_valid() { "degraded" } else { "ready" }).await;
                    }
                }
            }
        }
        let _ = async_stream_shutdown.send(true);
        for task in async_stream_tasks {
            if let Err(error) = task.await {
                warn!(event = "async_account_stream_task_failed", component = "account", error = %error, "async account stream task failed");
            }
        }
        for task in self.async_provider_tasks.drain(..) {
            task.abort();
            let _ = task.await;
        }
        remove_socket(&self.socket_path)?;
        server.abort();
        let _ = server.await;
        info!(event = "process_stopped", component = "account", account_id = %self.account_id, "account process stopped");
        Ok(())
    }

    fn start_async_stream_consumers(
        &mut self,
        sender: mpsc::Sender<
            Result<
                kairos_integration::application::capabilities::account_facts::ExternalAccountEvent,
                IntegrationError,
            >,
        >,
        shutdown: tokio::sync::watch::Receiver<bool>,
        overflowed: std::sync::Arc<std::sync::atomic::AtomicBool>,
        overflow_wakeup: std::sync::Arc<tokio::sync::Notify>,
    ) -> Vec<tokio::task::JoinHandle<()>> {
        std::mem::take(&mut self.async_account_streams)
            .into_iter()
            .map(|mut stream| {
                let sender = sender.clone();
                let mut shutdown = shutdown.clone();
                let overflowed = std::sync::Arc::clone(&overflowed);
                let overflow_wakeup = std::sync::Arc::clone(&overflow_wakeup);
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
                            result = stream.next_account_event() => result,
                        };
                        let should_reconnect = result.is_err();
                        match sender.try_send(result) {
                            Ok(()) => {}
                            Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => break,
                            Err(tokio::sync::mpsc::error::TrySendError::Full(_)) => {
                                overflowed.store(true, std::sync::atomic::Ordering::Release);
                                overflow_wakeup.notify_one();
                                break;
                            }
                        }
                        if should_reconnect {
                            if let Err(error) = stream.reconnect_channel().await {
                                tracing::warn!(event = "async_account_stream_reconnect_failed", component = "account", error = %error, "async account stream reconnect failed");
                                tokio::select! {
                                    _ = tokio::time::sleep(Duration::from_millis(250)) => {}
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

    fn schedule_refresh(&mut self) {
        if self.application.refresh_pending() {
            return;
        }
        if let Err(error) = self.application.start_refresh(RefreshAccount {
            account_id: match AccountId::new(self.account_id.clone()) {
                Ok(value) => value,
                Err(error) => {
                    self.last_error = Some(error.to_string());
                    return;
                }
            },
            segments: Vec::new(),
        }) {
            self.last_error = Some(error.to_string());
        } else {
            self.refresh_started = Some(Instant::now());
            info!(
                event = "account_refresh_queued",
                component = "account",
                account_id = %self.account_id,
                "account refresh queued"
            );
        }
    }

    fn drain_refresh(&mut self) -> bool {
        let generation_before = self.application.generation();
        match self.application.poll_refresh() {
            Ok(Some(report)) => {
                let duration_ms = self
                    .refresh_started
                    .take()
                    .map(|started| started.elapsed().as_millis())
                    .unwrap_or_default();
                self.last_error = report.issues.first().map(|issue| issue.error.clone());
                info!(
                    event = "account_refresh_worker_completed",
                    component = "account",
                    account_id = %report.account_id,
                    refreshed_segments = report.refreshed_segments.len(),
                    issues = report.issues.len(),
                    duration_ms,
                    "account refresh worker completed"
                );
                self.last_refresh = Some(report);
                self.snapshot_dirty |= self.application.generation() != generation_before;
                true
            }
            Ok(None) => false,
            Err(error) => {
                self.refresh_started = None;
                self.last_error = Some(error.to_string());
                kairos_workspace::logging::record_counter("kairos.operation.failed", 1);
                true
            }
        }
    }

    fn handle_request(
        &mut self,
        target: &str,
        body: &str,
    ) -> Result<(u16, Value), Box<dyn std::error::Error>> {
        let started = Instant::now();
        let generation_before = self.application.generation();
        let (path, query) = target.split_once('?').unwrap_or((target, ""));
        info!(event = "control_request", component = "account", path = %path, "account control request received");
        let account_query = parse_account_query(query, &self.account_id);
        let (status, body) = match path {
            HEALTH_PATH => (200, self.health_json()),
            SNAPSHOT_PATH => (
                200,
                serde_json::to_value(self.application.snapshot_query(&account_query))?,
            ),
            "/v1/balances" => {
                let (accounts, rows) = self.application.balances_query_with_rows(&account_query);
                (
                    200,
                    json!({
                        "accounts": accounts,
                        "rows": rows,
                        "page": account_query.page,
                        "page_size": account_query.page_size,
                    }),
                )
            }
            "/v1/positions" => (
                200,
                json!({"accounts": self.application.positions_query(&account_query)}),
            ),
            "/v1/open-orders" => (
                200,
                json!({"accounts": self.application.open_orders_query(&account_query)}),
            ),
            "/v1/simulated-fill" => self.json_command(body, |application, body| {
                let fill: AccountFill =
                    serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .apply_simulated_fill(fill)
                    .map(|_| json!({"status":"applied"}))
                    .map_err(|error| error.to_string())
            }),
            "/v1/mark-to-market" => self.json_command(body, |application, body| {
                let value: MarkToMarket =
                    serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .mark_to_market(value)
                    .map(|_| json!({"status":"applied"}))
                    .map_err(|error| error.to_string())
            }),
            "/v1/fill" => self.json_command(body, |application, body| {
                let fill: AccountFill =
                    serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .apply_event(crate::domain::AccountEvent::Fill(fill))
                    .map(|applied| json!({"status":"applied", "events": applied}))
                    .map_err(|error| error.to_string())
            }),
            "/v1/order-event" => self.json_command(body, |application, body| {
                let event: kairos_account_contract::client::OrderEvent =
                    serde_json::from_slice(body).map_err(|error| error.to_string())?;
                let active = matches!(
                    event.status.to_ascii_lowercase().as_str(),
                    "acknowledged" | "partially_filled" | "open" | "new"
                );
                let observation = AccountOrderObservation {
                    order_id: kairos_domain_types::OrderId::new(event.order_id)
                        .map_err(|error| error.to_string())?,
                    remote_order_id: event
                        .remote_order_id
                        .map(kairos_domain_types::RemoteOrderId::new)
                        .transpose()
                        .map_err(|error| error.to_string())?,
                    status: match event.status.to_ascii_lowercase().as_str() {
                        "pending" => kairos_domain_types::OrderStatus::Pending,
                        "acknowledged" | "new" | "open" => {
                            kairos_domain_types::OrderStatus::Acknowledged
                        }
                        "accepted" => kairos_domain_types::OrderStatus::Accepted,
                        "partially_filled" | "partial" => {
                            kairos_domain_types::OrderStatus::PartiallyFilled
                        }
                        "filled" => kairos_domain_types::OrderStatus::Filled,
                        "canceled" | "cancelled" => kairos_domain_types::OrderStatus::Canceled,
                        "rejected" => kairos_domain_types::OrderStatus::Rejected,
                        "expired" => kairos_domain_types::OrderStatus::Expired,
                        _ => kairos_domain_types::OrderStatus::Unknown,
                    },
                    filled_quantity: Some(
                        kairos_domain_types::Quantity::new(
                            event.filled_quantity.mantissa,
                            event.filled_quantity.scale,
                        )
                        .map_err(|error| error.to_string())?,
                    ),
                    active,
                    observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(
                        event.occurred_at_unix_nanos,
                    ),
                };
                application
                    .apply_event(crate::domain::AccountEvent::OrderObserved(observation))
                    .map(|applied| json!({"status":"applied", "events": applied}))
                    .map_err(|error| error.to_string())
            }),
            "/v1/market-profiles" => (200, json!({"profiles": self.application.market_profiles()})),
            "/v1/capabilities" => (
                200,
                json!({"capabilities": self.application.capabilities(Some(&self.account_id))}),
            ),
            "/v1/fees" => (
                200,
                json!({"fees": self.application.fee_schedules(Some(&self.account_id))}),
            ),
            "/v1/fills" => match serde_json::from_str::<AccountFill>(body) {
                Ok(fill) => match self
                    .application
                    .apply_event(crate::domain::AccountEvent::Fill(fill))
                {
                    Ok(applied) => (202, json!({"status": "accepted", "events": applied})),
                    Err(error) => (422, json!({"error": error.to_string()})),
                },
                Err(error) => (
                    400,
                    json!({"error": format!("invalid account fill: {error}")}),
                ),
            },
            "/v1/refresh" => {
                self.schedule_refresh();
                (
                    if self.application.refresh_pending() {
                        202
                    } else {
                        503
                    },
                    json!({
                        "health": self.health_json(),
                        "refresh": self.last_refresh,
                    }),
                )
            }
            "/v1/reconcile" => {
                let result = self
                    .application
                    .reconcile(crate::application::ReconcileAccount {
                        account_id: match AccountId::new(self.account_id.clone()) {
                            Ok(value) => value,
                            Err(error) => return Ok((503, json!({"error": error.to_string()}))),
                        },
                        segments: Vec::new(),
                    });
                match result {
                    Ok(report) => (200, json!({"reconcile": report})),
                    Err(error) => (503, json!({"error": error.to_string()})),
                }
            }
            STOP_PATH => {
                self.stop_requested = true;
                (202, json!({"status":"stopping"}))
            }
            _ => (404, json!({"error":"unknown account control path"})),
        };
        self.snapshot_dirty |= self.application.generation() != generation_before;
        if let Err(error) = self.publish_snapshot_if_dirty() {
            self.last_error = Some(error);
        }
        info!(event = "control_response", component = "account", path = %path, status, duration_ms = started.elapsed().as_millis(), "account control response sent");
        Ok((status, body))
    }

    fn publish_snapshot_if_dirty(&mut self) -> Result<(), String> {
        if !self.snapshot_dirty {
            return Ok(());
        }
        let Some(publisher) = self.publisher.as_mut() else {
            return Ok(());
        };
        let snapshot = self.application.snapshot_shared();
        kairos_workspace::logging::record_gauge(
            "kairos.snapshot.generation",
            snapshot.generation.get(),
        );
        let started = Instant::now();
        let result = publisher.publish(&snapshot);
        if result.is_ok() {
            debug!(
                event = "account_snapshot_published",
                component = "account",
                generation = snapshot.generation.get(),
                event_sequence = snapshot.event_sequence.get(),
                account_count = snapshot.accounts.len(),
                duration_ms = started.elapsed().as_millis(),
                "account snapshot published"
            );
        }
        if result.is_ok() {
            self.snapshot_dirty = false;
        }
        result
    }

    fn json_command<F>(&mut self, body: &str, handler: F) -> (u16, Value)
    where
        F: FnOnce(&mut AccountApplication, &[u8]) -> Result<Value, String>,
    {
        match handler(&mut self.application, body.as_bytes()) {
            Ok(value) => (202, value),
            Err(error) => (422, json!({"error": error})),
        }
    }

    fn health_json(&self) -> Value {
        json!({"status": if self.last_error.is_some() { "degraded" } else if !self.lease_valid() { "unavailable" } else { "ready" }, "pid": std::process::id(), "account_id": self.account_id, "actor_id": self.application.actor_id(), "generation": self.application.generation(), "event_sequence": self.application.event_sequence(), "stream_queue_depth": self.application.stream_queue_depth(), "persistence_queue_depth": self.application.persistence_queue_depth(), "refresh_pending": self.application.refresh_pending(), "last_error": self.last_error, "last_refresh": self.last_refresh, "lease_valid": self.lease_valid()})
    }

    fn lease_valid(&self) -> bool {
        let (Some(path), Some(instance_id)) = (&self.lease_file, &self.lease_instance_id) else {
            return true;
        };
        let Ok(bytes) = std::fs::read(path) else {
            return false;
        };
        let Ok(value) = serde_json::from_slice::<Value>(&bytes) else {
            return false;
        };
        if value.get("launch_instance_id").and_then(Value::as_str) != Some(instance_id.as_str()) {
            return false;
        }
        let Ok(modified) = std::fs::metadata(path).and_then(|metadata| metadata.modified()) else {
            return false;
        };
        SystemTime::now()
            .duration_since(modified)
            .map(|age| age <= Duration::from_secs(60))
            .unwrap_or(false)
    }

    async fn write_health(&self, status: &str) -> Result<(), std::io::Error> {
        let Some(path) = &self.health_file else {
            return Ok(());
        };
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let temporary = path.with_extension("tmp");
        let payload = serde_json::to_vec(&json!({"status":status,"account_id":self.account_id,"actor_id":self.application.actor_id(),"generation":self.application.generation(),"event_sequence":self.application.event_sequence(),"last_error":self.last_error})).map_err(std::io::Error::other)?;
        tokio::fs::write(&temporary, payload).await?;
        tokio::fs::rename(temporary, path).await
    }
}

async fn account_http_handler(
    State(sender): State<Sender<AccountHttpRequest>>,
    request: Request,
) -> Response {
    let started = Instant::now();
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
    let span = tracing::info_span!(
        "account.control_request",
        component = "account",
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
    let queue_depth = sender.max_capacity() - sender.capacity();
    kairos_workspace::logging::set_remote_parent(&span, request.headers());
    let response = account_http_handler_inner(sender, request)
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
    kairos_workspace::logging::record_gauge("kairos.queue.depth", queue_depth as u64);
    if response.status().is_server_error() {
        kairos_workspace::logging::mark_span_error(&span, "control.internal_error", true);
        kairos_workspace::logging::record_counter("kairos.control.request.failed", 1);
        kairos_workspace::logging::record_counter("kairos.operation.failed", 1);
    } else if !response.status().is_success() {
        span.record("error_code", "control.request_rejected");
        span.record("retryable", false);
    }
    tracing::info!(parent: &span, event = "control_request_completed", component = "account", duration_ms, result = if response.status().is_success() { "accepted" } else { "rejected" }, "account control request completed");
    response
}

async fn account_http_handler_inner(
    sender: Sender<AccountHttpRequest>,
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
    if sender
        .send(AccountHttpRequest {
            target,
            body,
            response: response_sender,
        })
        .await
        .is_err()
    {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"account process is stopping"})),
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
            Json(json!({"error":"account process did not respond"})),
        )
            .into_response(),
    }
}

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn parse_account_query(query: &str, account_id: &str) -> AccountDataQuery {
    let mut request = AccountDataQuery {
        account_id: AccountId::new(account_id).ok(),
        ..Default::default()
    };
    for pair in query.split('&').filter(|value| !value.is_empty()) {
        let Some((key, value)) = pair.split_once('=') else {
            continue;
        };
        match key {
            "segment" => {
                if let Ok(value) = crate::domain::SegmentKey::new(value) {
                    request.segments.push(value);
                }
            }
            "symbol" => request.symbol = kairos_domain_types::Symbol::new(value).ok(),
            "limit" => request.limit = value.parse().ok(),
            "include_zero" => request.include_zero = value == "true" || value == "1",
            "page" => request.page = value.parse().ok(),
            "page_size" => request.page_size = value.parse().ok(),
            _ => {}
        }
    }
    request
}
