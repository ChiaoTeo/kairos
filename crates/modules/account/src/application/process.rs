//! Account process. It owns lifecycle and control transport, not
//! account business state; the latter remains inside AccountActor.

use std::collections::BTreeMap;
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
    AccountApplication, AccountBusinessEvent, AccountCurrentView, AccountRefreshReport,
    AccountSegmentCompleteness, AccountSegmentFreshness, AccountSegmentSyncLifecycle,
    AccountSegmentSyncMode, MarkToMarket, RefreshAccount,
};
use crate::domain::{
    AccountFill, FillId, InstrumentId, OrderSide, Price, Quantity, SegmentKey, SignedQuantity,
};
use crate::services::integration::{
    AccountAsyncEventSource, AccountAsyncSnapshotGateway, AccountInstrumentResolver,
};
use crate::services::refresh::RefreshFetch;
use crate::services::synchronization::{SegmentSyncLifecycle, SegmentSyncState};
use kairos_account_contract::SimulatedSettlement;
use kairos_integration::application::{ConnectionHealth, ConnectionLifecycle, IntegrationError};
use kairos_primitives::{AccountId, Currency, OrderId, UnixNanos};
use kairos_workspace::runtime::{HEALTH_PATH, STOP_PATH};
use tracing::{debug, error, info, warn, Instrument};

pub struct AccountProcess {
    application: AccountApplication,
    account_id: String,
    socket_path: PathBuf,
    refresh_interval: Duration,
    health_file: Option<PathBuf>,
    publisher: Option<Box<dyn FnMut(&AccountCurrentView) -> Result<(), String> + Send>>,
    event_publisher: Option<Box<dyn FnMut(&AccountBusinessEvent) -> Result<(), String>>>,
    stop_requested: bool,
    last_error: Option<String>,
    last_refresh: Option<AccountRefreshReport>,
    lease_file: Option<PathBuf>,
    lease_instance_id: Option<String>,
    snapshot_dirty: bool,
    refresh_started: Option<Instant>,
    async_account_streams: Vec<AccountAsyncEventSource>,
    instrument_resolver: AccountInstrumentResolver,
    async_snapshot_source: Option<AccountAsyncSnapshotGateway>,
    async_refresh_pending: bool,
    async_refresh_task: Option<tokio::task::JoinHandle<()>>,
    segment_sync: BTreeMap<SegmentKey, SegmentSyncState>,
    async_event_queue_depth: std::sync::Arc<std::sync::atomic::AtomicUsize>,
    business_time_unix_nanos: Option<u64>,
    simulation_commands_enabled: bool,
}

struct AccountHttpRequest {
    method: String,
    target: String,
    body: Vec<u8>,
    response: oneshot::Sender<Result<(u16, Value), String>>,
}

#[derive(Default, serde::Deserialize)]
struct AccountSegmentControlRequest {
    account_id: Option<String>,
    #[serde(default)]
    segments: Vec<SegmentKey>,
}

enum AsyncRefreshUpdate {
    Segment {
        account_id: String,
        fetch: RefreshFetch,
    },
    Finished {
        source: AccountAsyncSnapshotGateway,
    },
}

struct AsyncStreamHealthUpdate {
    segment_key: SegmentKey,
    binding_id: String,
    health: ConnectionHealth,
}

struct AsyncAccountEventUpdate {
    segment_key: SegmentKey,
    result: Result<kairos_integration::application::ExternalAccountEventEnvelope, IntegrationError>,
}

impl AccountProcess {
    pub fn new(
        mut application: AccountApplication,
        account_id: impl Into<String>,
        socket_path: impl Into<PathBuf>,
        refresh_interval: Duration,
        health_file: Option<PathBuf>,
        publisher: Option<Box<dyn FnMut(&AccountCurrentView) -> Result<(), String> + Send>>,
    ) -> Result<Self, String> {
        let account_id = account_id.into();
        if account_id.trim().is_empty() {
            return Err("account process account_id is required".into());
        }
        if refresh_interval.is_zero() {
            return Err("account refresh interval must be positive".into());
        }
        let async_snapshot_source = application.take_async_snapshot_source();
        let segment_sync = application
            .current_view_shared()
            .segments
            .iter()
            .map(|segment| {
                (
                    segment.segment_key.clone(),
                    SegmentSyncState::new(segment.segment_key.clone()),
                )
            })
            .collect();
        Ok(Self {
            application,
            account_id,
            socket_path: socket_path.into(),
            refresh_interval,
            health_file,
            publisher,
            event_publisher: None,
            stop_requested: false,
            last_error: None,
            last_refresh: None,
            lease_file: None,
            lease_instance_id: None,
            snapshot_dirty: true,
            refresh_started: None,
            async_account_streams: Vec::new(),
            instrument_resolver: AccountInstrumentResolver::default(),
            async_snapshot_source,
            async_refresh_pending: false,
            async_refresh_task: None,
            segment_sync,
            async_event_queue_depth: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            business_time_unix_nanos: None,
            simulation_commands_enabled: false,
        })
    }
}

impl AccountProcess {
    pub(crate) fn with_async_account_streams(
        mut self,
        streams: Vec<AccountAsyncEventSource>,
    ) -> Self {
        for stream in &streams {
            if let Some(state) = self.segment_sync.get_mut(stream.segment_key()) {
                state.add_stream(stream.binding_id().to_owned());
            }
        }
        self.async_account_streams = streams;
        self
    }

    pub(crate) fn with_instrument_resolver(mut self, resolver: AccountInstrumentResolver) -> Self {
        self.instrument_resolver = resolver;
        self
    }

    /// Enables the Account-owned paper settlement command. Composition may
    /// call this only for a local paper/simulated Account; live provider
    /// processes deliberately have no external business-state write ingress.
    pub(crate) fn with_simulation_commands_enabled(mut self) -> Self {
        self.simulation_commands_enabled = true;
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

    pub fn with_event_publisher<P>(mut self, publisher: P) -> Self
    where
        P: FnMut(&AccountBusinessEvent) -> Result<(), String> + 'static,
    {
        self.event_publisher = Some(Box::new(publisher));
        self
    }

    pub fn application(&self) -> &AccountApplication {
        &self.application
    }

    pub async fn run(mut self) -> Result<(), Box<dyn std::error::Error>> {
        info!(event = "process_starting", component = "account", account_id = %self.account_id, socket = %self.socket_path.display(), refresh_interval_ms = self.refresh_interval.as_millis(), "account process starting");
        remove_socket(&self.socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket_path)?;
        const CONTROL_QUEUE_CAPACITY: usize = 256;
        const REFRESH_RESULT_POLL_MS: u64 = 10;
        let (sender, mut receiver) = mpsc::channel(CONTROL_QUEUE_CAPACITY);
        let router = Router::new()
            .fallback(account_http_handler)
            .with_state(sender);
        let server = tokio::spawn(async move { axum::serve(listener, router).await });
        kairos_workspace::logging::record_gauge("kairos.process.ready", 0);
        info!(event = "process_control_listening", component = "account", socket = %self.socket_path.display(), "account control socket listening");
        let mut interval = time::interval(self.refresh_interval);
        interval.set_missed_tick_behavior(MissedTickBehavior::Delay);
        let mut refresh_result_poll = time::interval(Duration::from_millis(REFRESH_RESULT_POLL_MS));
        refresh_result_poll.set_missed_tick_behavior(MissedTickBehavior::Skip);
        let (async_stream_shutdown, async_stream_shutdown_rx) = tokio::sync::watch::channel(false);
        let (async_event_sender, mut async_event_receiver) = mpsc::channel(256);
        let (async_stream_health_sender, mut async_stream_health_receiver) = mpsc::channel(64);
        let (async_stream_overflow_sender, mut async_stream_overflow_receiver) =
            mpsc::unbounded_channel();
        let (async_refresh_sender, mut async_refresh_receiver) = mpsc::channel(8);
        let async_stream_tasks = self.start_async_stream_consumers(
            async_event_sender,
            async_stream_shutdown_rx,
            async_stream_overflow_sender,
            async_stream_health_sender,
            std::sync::Arc::clone(&self.async_event_queue_depth),
        );
        let async_stream_enabled = !async_stream_tasks.is_empty();
        let legacy_refresh_enabled = self.application.has_refresh_worker();
        interval.tick().await;
        self.prepare_initial_bootstrap();
        self.schedule_refresh(&async_refresh_sender);
        if let Err(error) = self.publish_snapshot_if_dirty() {
            self.last_error = Some(error);
        }
        let _ = self.write_health(self.business_status()).await;
        while !self.stop_requested {
            tokio::select! {
                Some(request) = receiver.recv() => {
                    let response = self.handle_request(
                        &request.method,
                        &request.target,
                        &String::from_utf8_lossy(&request.body),
                        &async_refresh_sender,
                    );
                    let _ = request.response.send(response.map_err(|error| error.to_string()));
                }
                Some(update) = async_event_receiver.recv(), if async_stream_enabled => {
                    self.async_event_queue_depth.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
                    let segment_key = update.segment_key;
                    match update.result {
                        Ok(event) if self.segment_requires_buffering(&segment_key) => {
                            if !self.buffer_recovery_event(&segment_key, event) {
                                self.last_error = Some(format!(
                                    "account segment {segment_key} recovery buffer overflowed; another snapshot resynchronization is required"
                                ));
                            }
                        }
                        Ok(event) => {
                            let recovery_event = event.clone();
                            match self.apply_external_event(&segment_key, event) {
                            Ok(applied) if applied > 0 => {
                                self.snapshot_dirty = true;
                                if let Err(error) = self.publish_snapshot_if_dirty() {
                                    self.last_error = Some(error);
                                }
                            }
                            Ok(_) => {}
                            Err(error) => {
                                warn!(event = "async_account_event_apply_failed", component = "account", error = %error, "async account event could not be applied");
                                let _ = self.buffer_recovery_event(&segment_key, recovery_event);
                                self.mark_segment_resync(&segment_key, error.clone());
                                self.last_error = Some(error);
                                self.schedule_refresh(&async_refresh_sender);
                            }
                        }
                        }
                        Err(error) => {
                            warn!(event = "async_account_stream_failed", component = "account", error = %error, "async account stream reported a continuity failure");
                            self.mark_segment_resync(&segment_key, error.to_string());
                            self.last_error = Some(error.to_string());
                            self.schedule_refresh(&async_refresh_sender);
                        }
                    }
                }
                Some(update) = async_stream_health_receiver.recv(), if async_stream_enabled => {
                    self.apply_stream_health_update(update);
                    if self.segment_sync.values().any(|state| state.refresh_requested) {
                        self.schedule_refresh(&async_refresh_sender);
                    }
                    let _ = self.write_health(self.business_status()).await;
                }
                Some(segment_key) = async_stream_overflow_receiver.recv(), if async_stream_enabled => {
                    let error = format!("async account event queue overflowed for segment {segment_key}; snapshot resynchronization is required");
                    warn!(event = "async_account_stream_overflow", component = "account", segment = %segment_key, error = %error, "async account stream continuity was lost");
                    self.mark_segment_resync(&segment_key, error.clone());
                    self.last_error = Some(error);
                    self.schedule_refresh(&async_refresh_sender);
                }
                Some(update) = async_refresh_receiver.recv(), if self.async_refresh_pending => {
                    match update {
                        AsyncRefreshUpdate::Segment { account_id, fetch } => {
                            self.finish_async_segment_refresh(&account_id, fetch);
                        }
                        AsyncRefreshUpdate::Finished { source } => {
                            self.async_refresh_task.take();
                            self.async_snapshot_source = Some(source);
                            self.async_refresh_pending = false;
                            self.refresh_started = None;
                            if self.segment_sync.values().any(|state| state.refresh_requested) {
                                self.schedule_refresh(&async_refresh_sender);
                            }
                        }
                    }
                    if let Err(error) = self.publish_snapshot_if_dirty() {
                        self.last_error = Some(error);
                    }
                    let _ = self.write_health(self.business_status()).await;
                }
                _ = interval.tick() => {
                    if let Some(error) = self.application.take_persistence_error() {
                        warn!(event = "account_persistence_failed", component = "account", error = %error, "account persistence worker reported an error");
                        self.last_error = Some(error);
                    }
                    self.drain_refresh();
                    self.evaluate_segment_freshness();
                    self.schedule_refresh(&async_refresh_sender);
                    if let Err(error) = self.publish_snapshot_if_dirty() {
                        error!(event = "snapshot_publish_failed", component = "account", error = %error, "account snapshot publication failed");
                        self.last_error = Some(error);
                    }
                    let _ = self.write_health(self.business_status()).await;
                }
                _ = refresh_result_poll.tick(), if legacy_refresh_enabled => {
                    let refresh_changed = self.drain_refresh();
                    if let Err(error) = self.publish_snapshot_if_dirty() {
                        error!(event = "snapshot_publish_failed", component = "account", error = %error, "account snapshot publication failed");
                        self.last_error = Some(error);
                    }
                    if refresh_changed {
                        let _ = self.write_health(self.business_status()).await;
                    }
                }
            }
        }
        for state in self.segment_sync.values_mut() {
            state.lifecycle = SegmentSyncLifecycle::Stopped;
        }
        let _ = async_stream_shutdown.send(true);
        for task in async_stream_tasks {
            if let Err(error) = task.await {
                warn!(event = "async_account_stream_task_failed", component = "account", error = %error, "async account stream task failed");
            }
        }
        if let Some(task) = self.async_refresh_task.take() {
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
        sender: mpsc::Sender<AsyncAccountEventUpdate>,
        shutdown: tokio::sync::watch::Receiver<bool>,
        overflow_sender: mpsc::UnboundedSender<SegmentKey>,
        health_sender: mpsc::Sender<AsyncStreamHealthUpdate>,
        queue_depth: std::sync::Arc<std::sync::atomic::AtomicUsize>,
    ) -> Vec<tokio::task::JoinHandle<()>> {
        std::mem::take(&mut self.async_account_streams)
            .into_iter()
            .map(|mut stream| {
                let sender = sender.clone();
                let mut shutdown = shutdown.clone();
                let overflow_sender = overflow_sender.clone();
                let health_sender = health_sender.clone();
                let queue_depth = std::sync::Arc::clone(&queue_depth);
                tokio::spawn(async move {
                    let binding_id = stream.binding_id().to_owned();
                    let segment_key = stream.segment_key().clone();
                    let mut reconnect_attempt = 0_u32;
                    if let Err(error) = stream.connect_channel().await {
                        reconnect_attempt = 1;
                        queue_depth.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        if sender
                            .try_send(AsyncAccountEventUpdate {
                                segment_key: segment_key.clone(),
                                result: Err(error),
                            })
                            .is_err()
                        {
                            queue_depth.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
                        }
                    }
                    let _ = health_sender
                        .send(AsyncStreamHealthUpdate {
                            segment_key: segment_key.clone(),
                            binding_id: binding_id.clone(),
                            health: stream.channel_health(),
                        })
                        .await;
                    loop {
                        if reconnect_attempt > 0 {
                            let delay = stream_reconnect_delay(&binding_id, reconnect_attempt);
                            tokio::select! {
                                _ = tokio::time::sleep(delay) => {}
                                changed = shutdown.changed() => {
                                    if changed.is_err() || *shutdown.borrow() {
                                        break;
                                    }
                                }
                            }
                        }
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
                        queue_depth.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        match sender.try_send(AsyncAccountEventUpdate {
                            segment_key: segment_key.clone(),
                            result,
                        }) {
                            Ok(()) => {}
                            Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => {
                                queue_depth.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
                                break;
                            }
                            Err(tokio::sync::mpsc::error::TrySendError::Full(_)) => {
                                queue_depth.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
                                let _ = overflow_sender.send(segment_key.clone());
                                let _ = health_sender.try_send(AsyncStreamHealthUpdate {
                                    segment_key: segment_key.clone(),
                                    binding_id: binding_id.clone(),
                                    health: ConnectionHealth {
                                        lifecycle: ConnectionLifecycle::Degraded,
                                        healthy: false,
                                        authenticated: stream.channel_health().authenticated,
                                        last_error: Some(
                                            "Account consumer queue overflowed; resync required"
                                                .into(),
                                        ),
                                    },
                                });
                                tokio::select! {
                                    permit = sender.reserve() => {
                                        if permit.is_err() {
                                            break;
                                        }
                                    }
                                    changed = shutdown.changed() => {
                                        if changed.is_err() || *shutdown.borrow() {
                                            break;
                                        }
                                    }
                                }
                                if stream.reconnect_channel().await.is_err() {
                                    reconnect_attempt = reconnect_attempt.saturating_add(1).max(1);
                                } else {
                                    reconnect_attempt = 0;
                                }
                                let _ = health_sender
                                    .send(AsyncStreamHealthUpdate {
                                        segment_key: segment_key.clone(),
                                        binding_id: binding_id.clone(),
                                        health: stream.channel_health(),
                                    })
                                    .await;
                                continue;
                            }
                        }
                        if should_reconnect {
                            if let Err(error) = stream.reconnect_channel().await {
                                tracing::warn!(event = "async_account_stream_reconnect_failed", component = "account", error = %error, "async account stream reconnect failed");
                                reconnect_attempt = reconnect_attempt.saturating_add(1).max(1);
                            } else {
                                reconnect_attempt = 0;
                            }
                            let _ = health_sender
                                .send(AsyncStreamHealthUpdate {
                                    segment_key: segment_key.clone(),
                                    binding_id: binding_id.clone(),
                                    health: stream.channel_health(),
                                })
                                .await;
                        }
                    }
                    let _ = stream.disconnect_channel().await;
                })
            })
            .collect()
    }

    fn schedule_refresh(&mut self, async_sender: &mpsc::Sender<AsyncRefreshUpdate>) {
        if self.refresh_pending() {
            return;
        }
        let request = RefreshAccount {
            account_id: match AccountId::new(self.account_id.clone()) {
                Ok(value) => value,
                Err(error) => {
                    self.last_error = Some(error.to_string());
                    return;
                }
            },
            segments: self
                .segment_sync
                .values()
                .filter(|state| state.refresh_requested)
                .map(|state| state.segment_key.clone())
                .collect(),
        };
        if let Some(mut source) = self.async_snapshot_source.take() {
            let segments = match self.application.selected_refresh_segments(&request) {
                Ok(segments) => segments,
                Err(error) => {
                    self.async_snapshot_source = Some(source);
                    self.last_error = Some(error.to_string());
                    return;
                }
            };
            for segment in &segments {
                if let Some(state) = self.segment_sync.get_mut(&segment.segment_key) {
                    state.refresh_requested = false;
                    state.begin_refresh();
                }
            }
            let sender = async_sender.clone();
            let account_id = self.account_id.clone();
            self.async_refresh_pending = true;
            self.refresh_started = Some(Instant::now());
            self.async_refresh_task = Some(tokio::spawn(async move {
                let (fetch_sender, mut fetch_receiver) = mpsc::channel(8);
                {
                    let fetch = source.fetch_incremental(segments, fetch_sender);
                    tokio::pin!(fetch);
                    loop {
                        tokio::select! {
                            value = fetch_receiver.recv() => {
                                let Some(fetch) = value else { break; };
                                if sender.send(AsyncRefreshUpdate::Segment {
                                    account_id: account_id.clone(),
                                    fetch,
                                }).await.is_err() {
                                    return;
                                }
                            }
                            _ = &mut fetch => break,
                        }
                    }
                }
                while let Some(fetch) = fetch_receiver.recv().await {
                    if sender
                        .send(AsyncRefreshUpdate::Segment {
                            account_id: account_id.clone(),
                            fetch,
                        })
                        .await
                        .is_err()
                    {
                        return;
                    }
                }
                let _ = sender.send(AsyncRefreshUpdate::Finished { source }).await;
            }));
            info!(
                event = "account_async_refresh_started",
                component = "account",
                account_id = %self.account_id,
                "account async refresh started"
            );
            return;
        }
        let selected = match self.application.selected_refresh_segments(&request) {
            Ok(segments) => segments,
            Err(error) => {
                self.last_error = Some(error.to_string());
                return;
            }
        };
        if let Err(error) = self.application.start_refresh(request) {
            self.last_error = Some(error.to_string());
        } else {
            for segment in selected {
                if let Some(state) = self.segment_sync.get_mut(&segment.segment_key) {
                    state.refresh_requested = false;
                    state.begin_refresh();
                }
            }
            self.refresh_started = Some(Instant::now());
            info!(
                event = "account_refresh_queued",
                component = "account",
                account_id = %self.account_id,
                "account refresh queued"
            );
        }
    }

    fn request_segment_refresh(
        &mut self,
        segments: &[SegmentKey],
        reconcile: bool,
    ) -> Result<Vec<SegmentKey>, String> {
        let selected = if segments.is_empty() {
            self.segment_sync.keys().cloned().collect::<Vec<_>>()
        } else {
            segments.to_vec()
        };
        for segment_key in &selected {
            let state = self
                .segment_sync
                .get_mut(segment_key)
                .ok_or_else(|| format!("account segment is not configured: {segment_key}"))?;
            state.refresh_requested = true;
            if reconcile {
                state.mark_resync("operator requested reconciliation");
            }
        }
        Ok(selected)
    }

    fn prepare_initial_bootstrap(&mut self) {
        for state in self.segment_sync.values_mut() {
            if state.mode == crate::services::synchronization::SegmentSyncMode::SnapshotOnly {
                state.refresh_requested = true;
            } else {
                state.lifecycle = SegmentSyncLifecycle::Bootstrapping;
            }
        }
    }

    fn finish_async_segment_refresh(&mut self, account_id: &str, fetch: RefreshFetch) {
        let generation_before = self.application.generation();
        let segment_key = fetch.segment.segment_key.clone();
        let elapsed_ms = fetch.elapsed_ms;
        let outcome = fetch
            .result
            .as_ref()
            .map(|snapshot| snapshot.observed_at_unix_nanos.get())
            .map_err(Clone::clone);
        match self
            .application
            .apply_refresh_fetches(account_id, vec![fetch])
        {
            Ok(report) => {
                let issue = report.issues.first().map(|issue| issue.error.clone());
                self.last_error.clone_from(&issue);
                info!(
                    event = "account_async_segment_refresh_completed",
                    component = "account",
                    account_id = %report.account_id,
                    segment = %segment_key,
                    refreshed = !report.refreshed_segments.is_empty(),
                    issues = report.issues.len(),
                    duration_ms = elapsed_ms,
                    "account async segment refresh completed"
                );
                self.last_refresh = Some(report);
                self.snapshot_dirty |= self.application.generation() != generation_before;
                match (outcome, issue) {
                    (Ok(observed_at), None) => {
                        let overflowed = self
                            .segment_sync
                            .get(&segment_key)
                            .is_some_and(|state| state.recovery_overflowed);
                        if let Some(state) = self.segment_sync.get_mut(&segment_key) {
                            state.snapshot_succeeded(observed_at, elapsed_ms);
                            if overflowed {
                                state.recovery_events.clear();
                                state.recovery_overflowed = false;
                                state.mark_resync(
                                    "segment recovery buffer overflowed; snapshot must be repeated",
                                );
                                return;
                            }
                        }
                        if let Err(error) = self.replay_recovery_events(&segment_key) {
                            self.mark_segment_resync(&segment_key, error.clone());
                            self.last_error = Some(error);
                            return;
                        }
                        self.complete_stream_resync(&segment_key);
                    }
                    (Err(error), _) | (_, Some(error)) => {
                        if let Some(state) = self.segment_sync.get_mut(&segment_key) {
                            state.snapshot_failed(error, elapsed_ms);
                        }
                    }
                }
            }
            Err(error) => {
                if let Some(state) = self.segment_sync.get_mut(&segment_key) {
                    state.snapshot_failed(error.to_string(), elapsed_ms);
                }
                self.last_error = Some(error.to_string());
                kairos_workspace::logging::record_counter("kairos.operation.failed", 1);
            }
        }
    }

    fn segment_requires_buffering(&self, segment_key: &SegmentKey) -> bool {
        self.segment_sync
            .get(segment_key)
            .is_some_and(SegmentSyncState::requires_buffering)
    }

    fn buffer_recovery_event(
        &mut self,
        segment_key: &SegmentKey,
        event: kairos_integration::application::ExternalAccountEventEnvelope,
    ) -> bool {
        self.segment_sync
            .get_mut(segment_key)
            .is_some_and(|state| state.buffer(event))
    }

    fn mark_segment_resync(&mut self, segment_key: &SegmentKey, error: impl Into<String>) {
        if let Some(state) = self.segment_sync.get_mut(segment_key) {
            state.mark_resync(error);
        }
    }

    fn apply_stream_health_update(&mut self, update: AsyncStreamHealthUpdate) {
        if let Some(state) = self.segment_sync.get_mut(&update.segment_key) {
            let can_bootstrap = update.health.lifecycle == ConnectionLifecycle::Ready
                && update.health.healthy
                && update.health.authenticated
                && !state.initial_snapshot_complete;
            state.update_channel_health(update.binding_id, update.health);
            if can_bootstrap {
                state.refresh_requested = true;
            }
        }
    }

    fn complete_stream_resync(&mut self, segment_key: &SegmentKey) {
        if let Some(state) = self.segment_sync.get_mut(segment_key) {
            state.complete_resync();
        }
    }

    fn replay_recovery_events(&mut self, segment_key: &SegmentKey) -> Result<(), String> {
        loop {
            let event = self
                .segment_sync
                .get_mut(segment_key)
                .and_then(|state| state.recovery_events.pop_front());
            let Some(event) = event else {
                break;
            };
            match self.apply_external_event(segment_key, event) {
                Ok(applied) => self.snapshot_dirty |= applied > 0,
                Err(error) => {
                    if let Some(state) = self.segment_sync.get_mut(segment_key) {
                        state.recovery_events.clear();
                    }
                    return Err(error);
                }
            }
        }
        Ok(())
    }

    fn apply_external_event(
        &mut self,
        segment_key: &SegmentKey,
        envelope: kairos_integration::application::ExternalAccountEventEnvelope,
    ) -> Result<usize, String> {
        validate_external_event_segment(segment_key, &envelope.payload)?;
        let telemetry_binding_id = envelope.binding_id.clone();
        let telemetry_channel_id = envelope.channel_id.clone();
        let telemetry_channel_epoch = envelope.channel_epoch;
        let telemetry_provider_sequence = envelope.provider_sequence;
        let telemetry_event_id_present = envelope.provider_event_id.is_some();
        let telemetry_event_kind = external_account_event_kind(&envelope.payload);
        let generation_before = self.application.generation();
        let event_sequence_before = self.application.event_sequence();
        let channel_key = (envelope.binding_id.clone(), envelope.channel_id.clone());
        let state = self
            .segment_sync
            .get(segment_key)
            .ok_or_else(|| format!("account event references unknown segment {segment_key}"))?;
        if envelope.provider_event_id.as_ref().is_some_and(|event_id| {
            state.event_ids.contains(&(
                envelope.binding_id.clone(),
                envelope.channel_id.clone(),
                event_id.clone(),
            ))
        }) {
            return Ok(0);
        }

        if let Some((current_epoch, current_sequence)) =
            state.event_watermarks.get(&channel_key).copied()
        {
            if envelope.channel_epoch < current_epoch {
                return Ok(0);
            }
            if envelope.channel_epoch == current_epoch {
                if let (Some(previous), Some(sequence)) =
                    (current_sequence, envelope.provider_sequence)
                {
                    if sequence <= previous {
                        return Ok(0);
                    }
                    if sequence > previous.saturating_add(1) {
                        return Err(format!(
                            "account stream sequence gap on {}/{}: expected {}, received {}",
                            envelope.binding_id,
                            envelope.channel_id,
                            previous.saturating_add(1),
                            sequence
                        ));
                    }
                }
            }
        }

        let event =
            crate::services::integration::map_event(envelope.payload, &self.instrument_resolver)?;
        let applied = self
            .application
            .apply_event_with_provenance(
                event,
                crate::application::AccountFactProvenance {
                    source_id: format!("{}:{}", envelope.participant.id, envelope.binding_id),
                    provider_event_id: envelope.provider_event_id.clone(),
                    provider_sequence: envelope.provider_sequence,
                    provider_occurred_at_unix_nanos: Some(envelope.observed_at_unix_nanos.into()),
                    provider_received_at_unix_nanos: Some(envelope.received_at_unix_nanos.into()),
                },
            )
            .map_err(|error| error.to_string())?;
        let state = self
            .segment_sync
            .get_mut(segment_key)
            .expect("validated Account segment sync state");
        state.event_watermarks.insert(
            channel_key,
            (envelope.channel_epoch, envelope.provider_sequence),
        );
        if let Some(event_id) = envelope.provider_event_id {
            let key = (envelope.binding_id, envelope.channel_id, event_id);
            if state.event_ids.insert(key.clone()) {
                state.event_id_order.push_back(key);
                if state.event_id_order.len() > crate::services::synchronization::RETAINED_EVENT_IDS
                {
                    if let Some(expired) = state.event_id_order.pop_front() {
                        state.event_ids.remove(&expired);
                    }
                }
            }
        }
        state.mark_event_success(envelope.observed_at_unix_nanos.get());
        if applied > 0 {
            info!(
                event = "async_account_event_applied",
                component = "account",
                source = "private_stream",
                binding_id = %telemetry_binding_id,
                channel_id = %telemetry_channel_id,
                channel_epoch = telemetry_channel_epoch,
                provider_sequence = ?telemetry_provider_sequence,
                provider_event_id_present = telemetry_event_id_present,
                event_kind = telemetry_event_kind,
                applied_events = applied,
                generation_before,
                generation_after = self.application.generation(),
                event_sequence_before,
                event_sequence_after = self.application.event_sequence(),
                "async account event applied"
            );
        }
        Ok(applied)
    }

    fn refresh_pending(&self) -> bool {
        self.async_refresh_pending
            || self.application.refresh_pending()
            || self
                .segment_sync
                .values()
                .any(|state| state.refresh_pending)
    }

    fn evaluate_segment_freshness(&mut self) {
        let stale_after = self.refresh_interval.saturating_mul(2);
        let unavailable_after = self.refresh_interval.saturating_mul(6);
        let now = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
            .min(u64::MAX as u128) as u64;
        for state in self.segment_sync.values_mut() {
            state.evaluate_freshness(now, stale_after, unavailable_after);
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
                let issues = report
                    .issues
                    .iter()
                    .map(|issue| (issue.segment_key.clone(), issue.error.clone()))
                    .collect::<BTreeMap<_, _>>();
                let observed = self
                    .application
                    .current_view_shared()
                    .segments
                    .iter()
                    .map(|segment| {
                        (
                            segment.segment_key.clone(),
                            segment.observed_at_unix_nanos.get(),
                        )
                    })
                    .collect::<BTreeMap<_, _>>();
                let pending = self
                    .segment_sync
                    .iter()
                    .filter(|(_, state)| state.refresh_pending)
                    .map(|(key, _)| key.clone())
                    .collect::<Vec<_>>();
                for segment_key in pending {
                    if let Some(error) = issues.get(&segment_key) {
                        if let Some(state) = self.segment_sync.get_mut(&segment_key) {
                            state.snapshot_failed(error.clone(), duration_ms as u64);
                        }
                        continue;
                    }
                    if let Some(state) = self.segment_sync.get_mut(&segment_key) {
                        state.snapshot_succeeded(
                            observed.get(&segment_key).copied().unwrap_or_default(),
                            duration_ms as u64,
                        );
                    }
                    if let Err(error) = self.replay_recovery_events(&segment_key) {
                        self.mark_segment_resync(&segment_key, error.clone());
                        self.last_error = Some(error);
                        continue;
                    }
                    self.complete_stream_resync(&segment_key);
                }
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
        method: &str,
        target: &str,
        body: &str,
        async_refresh_sender: &mpsc::Sender<AsyncRefreshUpdate>,
    ) -> Result<(u16, Value), Box<dyn std::error::Error>> {
        let started = Instant::now();
        let generation_before = self.application.generation();
        let (path, _query) = target.split_once('?').unwrap_or((target, ""));
        info!(event = "control_request", component = "account", path = %path, "account control request received");
        if method == "GET" && path != HEALTH_PATH {
            return Ok((
                405,
                json!({"error":"REST business queries are disabled; read the typed mmap view"}),
            ));
        }
        if path == HEALTH_PATH && method != "GET" {
            return Ok((405, json!({"error":"health only supports GET"})));
        }
        let (status, body) = match path {
            HEALTH_PATH => (200, self.health_json()),
            "/v1/simulation/settlements" if self.simulation_commands_enabled => {
                self.json_command(body, |application, body| {
                    let settlement: SimulatedSettlement =
                        serde_json::from_slice(body).map_err(|error| error.to_string())?;
                    application
                        .apply_simulated_fill(simulated_account_fill(settlement)?)
                        .map(|_| json!({"status":"applied"}))
                        .map_err(|error| error.to_string())
                })
            }
            "/v1/simulation/settlements" => (
                403,
                json!({
                    "error":"simulation settlement is disabled for this Account process"
                }),
            ),
            "/v1/mark-to-market" if self.simulation_commands_enabled => {
                self.json_command(body, |application, body| {
                    let value: MarkToMarket =
                        serde_json::from_slice(body).map_err(|error| error.to_string())?;
                    application
                        .mark_to_market(value)
                        .map(|_| json!({"status":"applied"}))
                        .map_err(|error| error.to_string())
                })
            }
            "/v1/time/advance" if self.simulation_commands_enabled => {
                let event_time = serde_json::from_str::<Value>(body)
                    .ok()
                    .and_then(|value| value.get("event_time_unix_nanos").and_then(Value::as_u64));
                match event_time {
                    Some(event_time)
                        if self
                            .business_time_unix_nanos
                            .is_none_or(|current| event_time >= current) =>
                    {
                        self.business_time_unix_nanos = Some(event_time);
                        (
                            200,
                            json!({"status":"advanced", "event_time_unix_nanos": event_time}),
                        )
                    }
                    Some(_) => (
                        409,
                        json!({"error":"account business time cannot move backwards"}),
                    ),
                    None => (400, json!({"error":"event_time_unix_nanos is required"})),
                }
            }
            "/v1/mark-to-market" | "/v1/time/advance" => (
                403,
                json!({
                    "error":"simulation command is disabled for this Account process"
                }),
            ),
            "/v1/refresh" => {
                let request = match parse_segment_control_request(body, &self.account_id) {
                    Ok(request) => request,
                    Err(error) => return Ok((400, json!({"error": error}))),
                };
                let selected = match self.request_segment_refresh(&request.segments, false) {
                    Ok(selected) => selected,
                    Err(error) => return Ok((400, json!({"error": error}))),
                };
                self.schedule_refresh(async_refresh_sender);
                (
                    if self.refresh_pending() { 202 } else { 503 },
                    json!({
                        "health": self.health_json(),
                        "refresh": {
                            "status": "scheduled",
                            "account_id": self.account_id,
                            "segments": selected,
                        },
                    }),
                )
            }
            "/v1/reconcile" => {
                let request = match parse_segment_control_request(body, &self.account_id) {
                    Ok(request) => request,
                    Err(error) => return Ok((400, json!({"error": error}))),
                };
                match self.request_segment_refresh(&request.segments, true) {
                    Ok(selected) => {
                        self.schedule_refresh(async_refresh_sender);
                        (
                            if self.refresh_pending() { 202 } else { 503 },
                            json!({
                                "health": self.health_json(),
                                "reconcile": {
                                    "status": "scheduled",
                                    "account_id": self.account_id,
                                    "segments": selected,
                                },
                            }),
                        )
                    }
                    Err(error) => (400, json!({"error": error})),
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
        if !self.snapshot_dirty && self.application.pending_business_event().is_none() {
            return Ok(());
        }
        if self.publisher.is_none() && self.event_publisher.is_none() {
            return Ok(());
        }
        let mut event_error = None;
        while let Some(event) = self.application.pending_business_event().cloned() {
            if let Some(publisher) = self.event_publisher.as_mut() {
                if let Err(error) = publisher(&event) {
                    event_error = Some(format!("account event publication failed: {error}"));
                    break;
                }
            }
            if let Err(error) = self.application.acknowledge_business_event() {
                event_error = Some(format!("account event acknowledgement failed: {error}"));
                break;
            }
        }
        if self.snapshot_dirty {
            let mut view = (*self.application.current_view_shared()).clone();
            self.enrich_current_view(&mut view);
            kairos_workspace::logging::record_gauge(
                "kairos.snapshot.generation",
                view.generation.get(),
            );
            let started = Instant::now();
            if let Some(publisher) = self.publisher.as_mut() {
                publisher(&view)?;
            }
            debug!(
                event = "account_snapshot_published",
                component = "account",
                generation = view.generation.get(),
                event_sequence = self.application.event_sequence(),
                segment_count = view.segments.len(),
                duration_ms = started.elapsed().as_millis(),
                "account snapshot published"
            );
            self.snapshot_dirty = false;
        }
        event_error.map_or(Ok(()), Err)
    }

    fn enrich_current_view(&self, view: &mut AccountCurrentView) {
        for segment in &mut view.segments {
            let Some(state) = self.segment_sync.get(&segment.segment_key) else {
                continue;
            };
            segment.sync_mode = match state.mode {
                crate::services::synchronization::SegmentSyncMode::SnapshotThenStream => {
                    AccountSegmentSyncMode::SnapshotThenStream
                }
                crate::services::synchronization::SegmentSyncMode::SnapshotOnly => {
                    AccountSegmentSyncMode::SnapshotOnly
                }
            };
            segment.sync_lifecycle = match state.lifecycle {
                SegmentSyncLifecycle::Configured => AccountSegmentSyncLifecycle::Configured,
                SegmentSyncLifecycle::Bootstrapping => AccountSegmentSyncLifecycle::Bootstrapping,
                SegmentSyncLifecycle::Live => AccountSegmentSyncLifecycle::Live,
                SegmentSyncLifecycle::SnapshotCurrent => {
                    AccountSegmentSyncLifecycle::SnapshotCurrent
                }
                SegmentSyncLifecycle::Degraded => AccountSegmentSyncLifecycle::Degraded,
                SegmentSyncLifecycle::Resyncing => AccountSegmentSyncLifecycle::Resyncing,
                SegmentSyncLifecycle::Unavailable => AccountSegmentSyncLifecycle::Unavailable,
                SegmentSyncLifecycle::Stopped => AccountSegmentSyncLifecycle::Stopped,
            };
            segment.freshness = match state.lifecycle {
                SegmentSyncLifecycle::Live | SegmentSyncLifecycle::SnapshotCurrent => {
                    segment.freshness
                }
                SegmentSyncLifecycle::Degraded | SegmentSyncLifecycle::Stopped => {
                    AccountSegmentFreshness::Stale
                }
                SegmentSyncLifecycle::Resyncing => AccountSegmentFreshness::Resyncing,
                SegmentSyncLifecycle::Unavailable => AccountSegmentFreshness::Unavailable,
                SegmentSyncLifecycle::Configured | SegmentSyncLifecycle::Bootstrapping => {
                    AccountSegmentFreshness::Unknown
                }
            };
            segment.completeness = if state.initial_snapshot_complete {
                AccountSegmentCompleteness::Complete
            } else {
                AccountSegmentCompleteness::Unknown
            };
            segment.snapshot_watermark = state.last_snapshot_at_unix_nanos;
            segment.event_watermark = state
                .event_watermarks
                .values()
                .filter_map(|(_, sequence)| *sequence)
                .max();
            segment.channel_epoch = state
                .event_watermarks
                .values()
                .map(|(epoch, _)| *epoch)
                .max();
            segment.last_event_at_unix_nanos = state.last_event_at_unix_nanos;
            segment.last_success_at_unix_nanos = state.last_success_at_unix_nanos;
            segment.last_error.clone_from(&state.last_error);
            segment.recovery_buffer_depth = state.recovery_events.len() as u64;
        }
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
        let now_unix_nanos = SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
            .min(u64::MAX as u128) as u64;
        let provider_channels = self
            .segment_sync
            .iter()
            .flat_map(|(segment_key, state)| {
                state
                    .channel_health
                    .iter()
                    .map(move |(binding_id, health)| {
                        json!({
                            "segment_key": segment_key,
                            "binding_id": binding_id,
                            "lifecycle": connection_lifecycle_name(health.lifecycle),
                            "healthy": health.healthy,
                            "authenticated": health.authenticated,
                            "last_error": health.last_error,
                        })
                    })
            })
            .collect::<Vec<_>>();
        let segments = self
            .segment_sync
            .values()
            .map(|state| {
                let event_watermark = state
                    .event_watermarks
                    .values()
                    .filter_map(|(_, sequence)| *sequence)
                    .max();
                let channel_epoch = state
                    .event_watermarks
                    .values()
                    .map(|(epoch, _)| *epoch)
                    .max();
                json!({
                    "segment_key": state.segment_key,
                    "mode": state.mode,
                    "lifecycle": state.lifecycle,
                    "freshness": sync_freshness_name(state.lifecycle),
                    "refresh_pending": state.refresh_pending,
                    "initial_snapshot_complete": state.initial_snapshot_complete,
                    "last_snapshot_at_unix_nanos": state.last_snapshot_at_unix_nanos,
                    "snapshot_age_ms": state.last_snapshot_at_unix_nanos.map(|value| now_unix_nanos.saturating_sub(value) / 1_000_000),
                    "last_event_at_unix_nanos": state.last_event_at_unix_nanos,
                    "event_age_ms": state.last_event_at_unix_nanos.map(|value| now_unix_nanos.saturating_sub(value) / 1_000_000),
                    "last_success_at_unix_nanos": state.last_success_at_unix_nanos,
                    "last_refresh_duration_ms": state.last_refresh_duration_ms,
                    "provider_watermarks": state.event_watermarks.len(),
                    "event_watermark": event_watermark,
                    "channel_epoch": channel_epoch,
                    "recovery_buffer_depth": state.recovery_events.len(),
                    "recovery_overflowed": state.recovery_overflowed,
                    "reconnect_attempts": state.reconnect_attempts,
                    "last_error": state.last_error,
                })
            })
            .collect::<Vec<_>>();
        json!({
            "status": self.business_status(),
            "pid": std::process::id(),
            "refresh_pending": self.refresh_pending(),
            "all_segments_bootstrapped": self.segment_sync.values().all(|state| state.initial_snapshot_complete),
            "lease_valid": self.lease_valid(),
            "dependencies": { "provider_channels": provider_channels },
            "segments": segments,
            "consumer_queue_depth": self.async_event_queue_depth.load(std::sync::atomic::Ordering::Relaxed),
            "last_error": self.last_error,
        })
    }

    fn business_status(&self) -> &'static str {
        if !self.lease_valid() {
            return "unavailable";
        }
        if self.segment_sync.is_empty()
            || self
                .segment_sync
                .values()
                .all(|state| !state.initial_snapshot_complete)
                && self
                    .segment_sync
                    .values()
                    .any(|state| state.lifecycle == SegmentSyncLifecycle::Unavailable)
        {
            return "unavailable";
        }
        if self.segment_sync.values().any(|state| {
            !state.initial_snapshot_complete
                && matches!(
                    state.lifecycle,
                    SegmentSyncLifecycle::Degraded | SegmentSyncLifecycle::Unavailable
                )
        }) {
            return "degraded";
        }
        if self
            .segment_sync
            .values()
            .any(|state| !state.initial_snapshot_complete)
        {
            return "starting";
        }
        if self.last_error.is_some() || self.segment_sync.values().any(|state| !state.ready()) {
            "degraded"
        } else {
            "ready"
        }
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
        kairos_workspace::logging::record_gauge(
            "kairos.process.ready",
            u64::from(status == "ready"),
        );
        let Some(path) = &self.health_file else {
            return Ok(());
        };
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let temporary = path.with_extension("tmp");
        let payload = serde_json::to_vec(&self.health_json()).map_err(std::io::Error::other)?;
        tokio::fs::write(&temporary, payload).await?;
        tokio::fs::rename(temporary, path).await
    }
}

fn simulated_account_fill(value: SimulatedSettlement) -> Result<AccountFill, String> {
    Ok(AccountFill {
        fill_id: FillId::new(value.fill_id).map_err(|error| error.to_string())?,
        order_id: Some(OrderId::new(value.order_id).map_err(|error| error.to_string())?),
        segment_key: SegmentKey::new(value.segment_key).map_err(|error| error.to_string())?,
        instrument_id: InstrumentId::new(value.instrument_id).map_err(|error| error.to_string())?,
        quantity: Quantity::new(value.quantity.mantissa, value.quantity.scale)
            .map_err(|error| error.to_string())?,
        price: Price::new(value.price.mantissa, value.price.scale)
            .map_err(|error| error.to_string())?,
        side: match value.side.trim().to_ascii_lowercase().as_str() {
            "buy" => OrderSide::Buy,
            "sell" => OrderSide::Sell,
            _ => return Err("simulated settlement side must be buy or sell".into()),
        },
        settlement_asset: Some(
            Currency::new(value.settlement_asset).map_err(|error| error.to_string())?,
        ),
        settlement_delta: Some(
            SignedQuantity::new(
                value.settlement_delta.mantissa,
                value.settlement_delta.scale,
            )
            .map_err(|error| error.to_string())?,
        ),
        fee_asset: value
            .fee_asset
            .map(Currency::new)
            .transpose()
            .map_err(|error| error.to_string())?,
        fee_amount: value
            .fee_amount
            .map(|amount| SignedQuantity::new(amount.mantissa, amount.scale))
            .transpose()
            .map_err(|error| error.to_string())?,
        occurred_at_unix_nanos: UnixNanos::new(value.occurred_at_unix_nanos),
    })
}

fn external_account_event_kind(
    event: &kairos_integration::application::ExternalAccountEvent,
) -> &'static str {
    match event {
        kairos_integration::application::ExternalAccountEvent::Snapshot(_) => "snapshot",
        kairos_integration::application::ExternalAccountEvent::Order(_) => "order",
        kairos_integration::application::ExternalAccountEvent::Fill(_) => "fill",
        kairos_integration::application::ExternalAccountEvent::Batch(_) => "batch",
    }
}

fn validate_external_event_segment(
    expected: &SegmentKey,
    event: &kairos_integration::application::ExternalAccountEvent,
) -> Result<(), String> {
    use kairos_integration::application::ExternalAccountEvent;
    match event {
        ExternalAccountEvent::Snapshot(snapshot) if &snapshot.segment_key != expected => {
            Err(format!(
                "account stream segment mismatch: binding owns {expected}, snapshot is {}",
                snapshot.segment_key
            ))
        }
        ExternalAccountEvent::Fill(fill) if &fill.segment_key != expected => Err(format!(
            "account stream segment mismatch: binding owns {expected}, fill is {}",
            fill.segment_key
        )),
        ExternalAccountEvent::Batch(events) => {
            for event in events {
                validate_external_event_segment(expected, event)?;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

fn connection_lifecycle_name(value: ConnectionLifecycle) -> &'static str {
    match value {
        ConnectionLifecycle::Created => "created",
        ConnectionLifecycle::Starting => "starting",
        ConnectionLifecycle::Ready => "ready",
        ConnectionLifecycle::Degraded => "degraded",
        ConnectionLifecycle::Stopping => "stopping",
        ConnectionLifecycle::Stopped => "stopped",
        ConnectionLifecycle::Failed => "failed",
    }
}

fn sync_freshness_name(value: SegmentSyncLifecycle) -> &'static str {
    match value {
        SegmentSyncLifecycle::Live | SegmentSyncLifecycle::SnapshotCurrent => "fresh",
        SegmentSyncLifecycle::Degraded => "stale",
        SegmentSyncLifecycle::Resyncing => "resyncing",
        SegmentSyncLifecycle::Unavailable | SegmentSyncLifecycle::Stopped => "unavailable",
        SegmentSyncLifecycle::Configured | SegmentSyncLifecycle::Bootstrapping => "unknown",
    }
}

fn stream_reconnect_delay(binding_id: &str, attempt: u32) -> Duration {
    let exponent = attempt.saturating_sub(1).min(5);
    let base_ms = 250_u64.saturating_mul(1_u64 << exponent);
    let jitter_ms = binding_id
        .bytes()
        .fold(u64::from(attempt) * 37, |value, byte| {
            value.wrapping_add(u64::from(byte))
        })
        % 250;
    Duration::from_millis((base_ms + jitter_ms).min(10_000))
}

fn parse_segment_control_request(
    body: &str,
    process_account_id: &str,
) -> Result<AccountSegmentControlRequest, String> {
    let request = if body.is_empty() {
        AccountSegmentControlRequest::default()
    } else {
        serde_json::from_str(body).map_err(|error| error.to_string())?
    };
    if request
        .account_id
        .as_deref()
        .is_some_and(|account_id| account_id != process_account_id)
    {
        return Err(format!(
            "control request account does not match process account: {process_account_id}"
        ));
    }
    Ok(request)
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
    if path == HEALTH_PATH && response.status().is_success() {
        tracing::debug!(parent: &span, event = "control_request_completed", component = "account", duration_ms, result = "accepted", "account health request completed");
    } else if response.status().is_success() {
        tracing::info!(parent: &span, event = "control_request_completed", component = "account", duration_ms, result = "accepted", "account control request completed");
    } else {
        tracing::warn!(parent: &span, event = "control_request_completed", component = "account", duration_ms, result = "rejected", "account control request failed");
    }
    response
}

async fn account_http_handler_inner(
    sender: Sender<AccountHttpRequest>,
    request: Request,
) -> Response {
    let method = request.method().as_str().to_owned();
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
            method,
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::composition::account::compose_in_memory_account_application;
    use crate::composition::empty_snapshot;
    use crate::domain::{AccountSegment, ExternalAccountIdentity, SegmentKey};
    use kairos_integration::application::{
        ExternalAccountEvent, ExternalAccountSnapshot, ExternalAccountStatus, ExternalEventEnvelope,
    };
    use std::sync::{Arc, Mutex};

    #[derive(Clone, Default)]
    struct CapturingEventPublisher(Arc<Mutex<Vec<AccountBusinessEvent>>>);

    impl CapturingEventPublisher {
        fn publish(&self, event: &AccountBusinessEvent) -> Result<(), String> {
            self.0.lock().unwrap().push(event.clone());
            Ok(())
        }
    }

    #[derive(Clone, Default)]
    struct CountingSnapshotPublisher(Arc<Mutex<usize>>);

    impl CountingSnapshotPublisher {
        fn publish(&self, _view: &AccountCurrentView) -> Result<(), String> {
            *self.0.lock().unwrap() += 1;
            Ok(())
        }
    }

    #[derive(Clone, Default)]
    struct CapturingCurrentViewPublisher(Arc<Mutex<Option<AccountCurrentView>>>);

    impl CapturingCurrentViewPublisher {
        fn publish(&self, view: &AccountCurrentView) -> Result<(), String> {
            *self.0.lock().unwrap() = Some(view.clone());
            Ok(())
        }
    }

    #[derive(Clone, Default)]
    struct FailOnceEventPublisher(Arc<Mutex<usize>>);

    impl FailOnceEventPublisher {
        fn publish(&self, _event: &AccountBusinessEvent) -> Result<(), String> {
            let mut attempts = self.0.lock().unwrap();
            *attempts += 1;
            if *attempts == 1 {
                Err("event transport unavailable".into())
            } else {
                Ok(())
            }
        }
    }

    fn process() -> AccountProcess {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("test", "main").unwrap(),
            segment_key: SegmentKey::new("spot").unwrap(),
            environment: "test".into(),
            account_model: Some("no_margin".into()),
        };
        let application = compose_in_memory_account_application(
            vec![segment],
            BTreeMap::from([("spot".into(), empty_snapshot("spot"))]),
            None,
        )
        .unwrap();
        AccountProcess::new(
            application,
            "main",
            "/tmp/kairos-account-process-test.sock",
            Duration::from_secs(1),
            None,
            None,
        )
        .unwrap()
    }

    fn envelope(sequence: u64, event_id: &str) -> ExternalEventEnvelope<ExternalAccountEvent> {
        envelope_for("spot", "account.test.spot", sequence, event_id)
    }

    fn envelope_for(
        segment: &str,
        binding_id: &str,
        sequence: u64,
        event_id: &str,
    ) -> ExternalEventEnvelope<ExternalAccountEvent> {
        ExternalEventEnvelope {
            participant: kairos_integration::application::ParticipantRef::new(
                kairos_integration::application::ParticipantKind::Exchange,
                "test",
            )
            .unwrap(),
            binding_id: binding_id.into(),
            channel_id: "private".into(),
            channel_epoch: 1,
            provider_event_id: Some(event_id.into()),
            provider_sequence: Some(sequence),
            observed_at_unix_nanos: sequence.into(),
            received_at_unix_nanos: sequence.into(),
            payload: ExternalAccountEvent::Snapshot(ExternalAccountSnapshot {
                segment_key: kairos_primitives::SegmentKey::new(segment).unwrap(),
                balances: Vec::new(),
                collateral: Vec::new(),
                positions: Vec::new(),
                open_orders: Vec::new(),
                status: ExternalAccountStatus::Ready,
                observed_at_unix_nanos: sequence.into(),
                equity: None,
                initial_equity: None,
                net_profit: None,
                account_model: None,
                margin_mode: None,
                position_mode: None,
                partial: false,
            }),
        }
    }

    fn spot_key() -> SegmentKey {
        SegmentKey::new("spot").unwrap()
    }

    fn funding_key() -> SegmentKey {
        SegmentKey::new("funding").unwrap()
    }

    fn two_segment_process() -> AccountProcess {
        let segments = ["spot", "funding"]
            .into_iter()
            .map(|key| AccountSegment {
                identity: ExternalAccountIdentity::new("test", "main").unwrap(),
                segment_key: SegmentKey::new(key).unwrap(),
                environment: "test".into(),
                account_model: Some("no_margin".into()),
            })
            .collect();
        let application = compose_in_memory_account_application(
            segments,
            BTreeMap::from([
                ("spot".into(), empty_snapshot("spot")),
                ("funding".into(), empty_snapshot("funding")),
            ]),
            None,
        )
        .unwrap();
        AccountProcess::new(
            application,
            "main",
            "/tmp/kairos-account-two-segment-process-test.sock",
            Duration::from_secs(1),
            None,
            None,
        )
        .unwrap()
    }

    #[test]
    fn readiness_requires_initial_refresh() {
        let mut process = process();
        assert_eq!(process.business_status(), "starting");
        process
            .segment_sync
            .get_mut(&spot_key())
            .unwrap()
            .snapshot_succeeded(1, 1);
        assert_eq!(process.business_status(), "ready");
    }

    #[test]
    fn operator_reconcile_targets_only_requested_segment() {
        let mut process = two_segment_process();

        process
            .request_segment_refresh(std::slice::from_ref(&funding_key()), true)
            .unwrap();

        assert!(!process.segment_sync[&spot_key()].refresh_requested);
        assert!(process.segment_sync[&funding_key()].refresh_requested);
        assert_eq!(
            process.segment_sync[&funding_key()].lifecycle,
            SegmentSyncLifecycle::Resyncing
        );
    }

    #[test]
    fn initial_snapshot_publication_never_synthesizes_account_events() {
        let capture = CapturingEventPublisher::default();
        let observed = capture.0.clone();
        let mut process = process().with_event_publisher(move |event| capture.publish(event));

        process.publish_snapshot_if_dirty().unwrap();

        assert!(observed.lock().unwrap().is_empty());
    }

    #[test]
    fn actor_transition_publishes_the_direct_account_business_event() {
        let capture = CapturingEventPublisher::default();
        let observed = capture.0.clone();
        let mut process = process().with_event_publisher(move |event| capture.publish(event));
        process.publish_snapshot_if_dirty().unwrap();

        assert_eq!(
            process
                .apply_external_event(&spot_key(), envelope(1, "one"))
                .unwrap(),
            1
        );
        process.snapshot_dirty = true;
        process.publish_snapshot_if_dirty().unwrap();

        let events = observed.lock().unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].sequence.get(), 1);
        assert_eq!(events[0].account_id.as_str(), "main");
        assert_eq!(
            events[0].provenance.as_ref().unwrap().source_id,
            "test:account.test.spot"
        );
        assert_eq!(
            events[0]
                .provenance
                .as_ref()
                .unwrap()
                .provider_event_id
                .as_deref(),
            Some("one")
        );
        assert!(!events[0].changes.is_empty());
    }

    #[test]
    fn event_publication_failure_does_not_block_snapshot_and_is_retried() {
        let snapshots = CountingSnapshotPublisher::default();
        let snapshot_count = snapshots.0.clone();
        let events = FailOnceEventPublisher::default();
        let event_attempts = events.0.clone();
        let mut process = process().with_event_publisher(move |event| events.publish(event));
        process.publisher = Some(Box::new(move |view| snapshots.publish(view)));
        process.publish_snapshot_if_dirty().unwrap();

        assert_eq!(
            process
                .apply_external_event(&spot_key(), envelope(1, "one"))
                .unwrap(),
            1
        );
        process.snapshot_dirty = true;
        let error = process.publish_snapshot_if_dirty().unwrap_err();

        assert!(error.contains("event transport unavailable"));
        assert_eq!(*snapshot_count.lock().unwrap(), 2);
        assert!(!process.snapshot_dirty);
        assert!(process.application.pending_business_event().is_some());

        process.publish_snapshot_if_dirty().unwrap();
        assert_eq!(*event_attempts.lock().unwrap(), 2);
        assert_eq!(*snapshot_count.lock().unwrap(), 2);
        assert!(process.application.pending_business_event().is_none());
    }

    #[test]
    fn publisher_failure_outbox_survives_process_reconstruction() {
        let directory = tempfile::tempdir().unwrap();
        let state = directory.path().join("account-state.json");
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("test", "main").unwrap(),
            segment_key: SegmentKey::new("spot").unwrap(),
            environment: "test".into(),
            account_model: Some("no_margin".into()),
        };
        let application = compose_in_memory_account_application(
            vec![segment.clone()],
            BTreeMap::from([("spot".into(), empty_snapshot("spot"))]),
            Some(state.clone()),
        )
        .unwrap();
        let mut first = AccountProcess::new(
            application,
            "main",
            directory.path().join("first.sock"),
            Duration::from_secs(1),
            None,
            None,
        )
        .unwrap()
        .with_event_publisher({
            let publisher = FailOnceEventPublisher::default();
            move |event| publisher.publish(event)
        });
        first.publish_snapshot_if_dirty().unwrap();
        first
            .apply_external_event(&spot_key(), envelope(1, "durable-one"))
            .unwrap();
        first.snapshot_dirty = true;
        assert!(first.publish_snapshot_if_dirty().is_err());
        drop(first);

        let restored =
            compose_in_memory_account_application(vec![segment], BTreeMap::new(), Some(state))
                .unwrap();
        let capture = CapturingEventPublisher::default();
        let observed = capture.0.clone();
        let mut second = AccountProcess::new(
            restored,
            "main",
            directory.path().join("second.sock"),
            Duration::from_secs(1),
            None,
            None,
        )
        .unwrap()
        .with_event_publisher(move |event| capture.publish(event));

        second.publish_snapshot_if_dirty().unwrap();

        let events = observed.lock().unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].sequence.get(), 1);
        assert_eq!(
            events[0]
                .provenance
                .as_ref()
                .and_then(|value| value.provider_event_id.as_deref()),
            Some("durable-one")
        );
        assert!(second.application.pending_business_event().is_none());
    }

    #[test]
    fn actor_transition_emits_every_required_strategy_account_change() {
        let capture = CapturingEventPublisher::default();
        let observed = capture.0.clone();
        let mut process = process().with_event_publisher(move |event| capture.publish(event));
        process.publish_snapshot_if_dirty().unwrap();

        let mut snapshot = empty_snapshot("spot");
        snapshot.balances = vec![crate::domain::Balance {
            asset_id: crate::domain::AssetId::new("asset:test:USD").unwrap(),
            asset_code: kairos_primitives::Currency::new("USD").unwrap(),
            total: crate::domain::SignedQuantity::new(10_000, 2).unwrap(),
            available: Some(crate::domain::SignedQuantity::new(9_000, 2).unwrap()),
            locked: Some(crate::domain::SignedQuantity::new(1_000, 2).unwrap()),
            borrowed: None,
            interest: None,
        }];
        snapshot.positions = vec![crate::domain::Position {
            instrument_id: crate::domain::InstrumentId::new("instrument:test:BTCUSD").unwrap(),
            market_id: Some(kairos_primitives::MarketId::new("market:test:BTCUSD").unwrap()),
            position_side: kairos_primitives::PositionSide::Net,
            quantity: crate::domain::SignedQuantity::new(2, 0).unwrap(),
            average_price: Some(crate::domain::Price::new(50_000, 2).unwrap()),
            mark_price: Some(crate::domain::Price::new(51_000, 2).unwrap()),
            unrealized_pnl: Some(crate::domain::Money::new(2_000, 2).unwrap()),
            realized_pnl: None,
            updated_at_unix_nanos: 1.into(),
        }];
        snapshot.equity = Some(crate::domain::Money::new(102_000, 2).unwrap());
        snapshot.status = crate::domain::AccountStatus::Suspended;
        snapshot.observed_at_unix_nanos = 1.into();

        process
            .application
            .apply_event(crate::domain::AccountEvent::Snapshot(snapshot))
            .unwrap();
        process.snapshot_dirty = true;
        process.publish_snapshot_if_dirty().unwrap();

        let events = observed.lock().unwrap();
        let changes = &events.last().unwrap().changes;
        assert!(changes.iter().any(|change| matches!(
            change,
            crate::application::AccountBusinessChange::Balance { .. }
        )));
        assert!(changes.iter().any(|change| matches!(
            change,
            crate::application::AccountBusinessChange::Position { .. }
        )));
        assert!(changes.iter().any(|change| matches!(
            change,
            crate::application::AccountBusinessChange::Equity { .. }
        )));
        assert!(changes.iter().any(|change| matches!(
            change,
            crate::application::AccountBusinessChange::Status { .. }
        )));
    }

    #[test]
    fn external_event_envelope_deduplicates_and_detects_sequence_gaps() {
        let mut process = process();
        assert_eq!(
            process
                .apply_external_event(&spot_key(), envelope(1, "one"))
                .unwrap(),
            1
        );
        assert_eq!(
            process
                .apply_external_event(&spot_key(), envelope(1, "one"))
                .unwrap(),
            0
        );
        let error = process
            .apply_external_event(&spot_key(), envelope(3, "three"))
            .unwrap_err();
        assert!(error.contains("sequence gap"));
    }

    #[test]
    fn simulation_settlement_route_is_disabled_by_default() {
        let mut process = process();
        let (sender, _receiver) = mpsc::channel(1);
        let (status, _) = process
            .handle_request("POST", "/v1/simulation/settlements", "{}", &sender)
            .unwrap();
        assert_eq!(status, 403);

        process.simulation_commands_enabled = true;
        let (status, _) = process
            .handle_request("POST", "/v1/simulation/settlements", "{}", &sender)
            .unwrap();
        assert_eq!(status, 422);
    }

    #[test]
    fn snapshot_recovery_barrier_replays_buffered_events_in_sequence() {
        let mut process = process();
        assert!(process.buffer_recovery_event(&spot_key(), envelope(1, "one")));
        assert!(process.buffer_recovery_event(&spot_key(), envelope(2, "two")));
        assert_eq!(process.application.event_sequence(), 0);

        process.replay_recovery_events(&spot_key()).unwrap();

        assert_eq!(process.application.event_sequence(), 2);
        assert!(process.segment_sync[&spot_key()].recovery_events.is_empty());
        assert_eq!(
            process.segment_sync[&spot_key()]
                .event_watermarks
                .get(&("account.test.spot".into(), "private".into())),
            Some(&(1, Some(2)))
        );
    }

    #[test]
    fn reconnected_stream_waits_for_snapshot_before_returning_ready() {
        let mut process = process();
        process
            .segment_sync
            .get_mut(&spot_key())
            .unwrap()
            .add_stream("account.test".into());
        process.apply_stream_health_update(AsyncStreamHealthUpdate {
            segment_key: spot_key(),
            binding_id: "account.test".into(),
            health: ConnectionHealth {
                lifecycle: ConnectionLifecycle::Degraded,
                healthy: false,
                authenticated: true,
                last_error: Some("consumer overflow; resync required".into()),
            },
        });
        process.apply_stream_health_update(AsyncStreamHealthUpdate {
            segment_key: spot_key(),
            binding_id: "account.test".into(),
            health: ConnectionHealth {
                lifecycle: ConnectionLifecycle::Ready,
                healthy: true,
                authenticated: true,
                last_error: None,
            },
        });
        assert_eq!(
            process.segment_sync[&spot_key()].channel_health["account.test"].lifecycle,
            ConnectionLifecycle::Degraded
        );

        process
            .segment_sync
            .get_mut(&spot_key())
            .unwrap()
            .snapshot_succeeded(1, 1);
        process.complete_stream_resync(&spot_key());

        assert_eq!(
            process.segment_sync[&spot_key()].channel_health["account.test"].lifecycle,
            ConnectionLifecycle::Ready
        );
        assert!(process.segment_sync[&spot_key()]
            .resync_required_bindings
            .is_empty());
    }

    #[test]
    fn segment_snapshot_barrier_does_not_clear_another_segment_watermark() {
        let mut process = two_segment_process();
        process
            .apply_external_event(
                &spot_key(),
                envelope_for("spot", "account.test.spot", 1, "spot-one"),
            )
            .unwrap();
        process
            .apply_external_event(
                &funding_key(),
                envelope_for("funding", "account.test.funding", 1, "funding-one"),
            )
            .unwrap();

        process
            .segment_sync
            .get_mut(&spot_key())
            .unwrap()
            .snapshot_succeeded(2, 1);

        assert!(process.segment_sync[&spot_key()]
            .event_watermarks
            .is_empty());
        assert_eq!(
            process.segment_sync[&funding_key()]
                .event_watermarks
                .get(&("account.test.funding".into(), "private".into())),
            Some(&(1, Some(1)))
        );
    }

    #[test]
    fn one_segment_resync_does_not_block_another_segment_event() {
        let mut process = two_segment_process();
        for key in [spot_key(), funding_key()] {
            process
                .segment_sync
                .get_mut(&key)
                .unwrap()
                .snapshot_succeeded(1, 1);
        }
        process
            .segment_sync
            .get_mut(&spot_key())
            .unwrap()
            .add_stream("account.test.spot".into());
        process.mark_segment_resync(&spot_key(), "spot gap");

        assert!(process.segment_requires_buffering(&spot_key()));
        assert!(!process.segment_requires_buffering(&funding_key()));
        assert_eq!(
            process
                .apply_external_event(
                    &funding_key(),
                    envelope_for("funding", "account.test.funding", 2, "funding-two"),
                )
                .unwrap(),
            1
        );
        assert_eq!(
            process.segment_sync[&spot_key()].lifecycle,
            SegmentSyncLifecycle::Resyncing
        );
        assert!(process.segment_sync[&funding_key()].ready());
    }

    #[test]
    fn streaming_segment_connects_before_its_initial_snapshot_barrier() {
        let mut process = two_segment_process();
        process
            .segment_sync
            .get_mut(&spot_key())
            .unwrap()
            .add_stream("account.test.spot".into());

        process.prepare_initial_bootstrap();

        assert_eq!(
            process.segment_sync[&spot_key()].lifecycle,
            SegmentSyncLifecycle::Bootstrapping
        );
        assert!(!process.segment_sync[&spot_key()].refresh_requested);
        assert!(process.segment_sync[&funding_key()].refresh_requested);

        process.apply_stream_health_update(AsyncStreamHealthUpdate {
            segment_key: spot_key(),
            binding_id: "account.test.spot".into(),
            health: ConnectionHealth {
                lifecycle: ConnectionLifecycle::Ready,
                healthy: true,
                authenticated: true,
                last_error: None,
            },
        });
        assert!(process.segment_sync[&spot_key()].refresh_requested);
    }

    #[test]
    fn published_current_view_contains_segment_sync_quality() {
        let capture = CapturingCurrentViewPublisher::default();
        let observed = capture.0.clone();
        let mut process = two_segment_process();
        process.publisher = Some(Box::new(move |view| capture.publish(view)));
        process
            .segment_sync
            .get_mut(&spot_key())
            .unwrap()
            .add_stream("account.test.spot".into());
        process
            .segment_sync
            .get_mut(&spot_key())
            .unwrap()
            .snapshot_succeeded(10, 3);
        process.apply_stream_health_update(AsyncStreamHealthUpdate {
            segment_key: spot_key(),
            binding_id: "account.test.spot".into(),
            health: ConnectionHealth {
                lifecycle: ConnectionLifecycle::Ready,
                healthy: true,
                authenticated: true,
                last_error: None,
            },
        });
        process
            .apply_external_event(
                &spot_key(),
                envelope_for("spot", "account.test.spot", 11, "spot-eleven"),
            )
            .unwrap();
        process.snapshot_dirty = true;

        process.publish_snapshot_if_dirty().unwrap();

        let view = observed.lock().unwrap().clone().unwrap();
        let spot = view
            .segments
            .iter()
            .find(|segment| segment.segment_key == spot_key())
            .unwrap();
        assert_eq!(spot.sync_mode, AccountSegmentSyncMode::SnapshotThenStream);
        assert_eq!(spot.sync_lifecycle, AccountSegmentSyncLifecycle::Live);
        assert_eq!(spot.completeness, AccountSegmentCompleteness::Complete);
        assert_eq!(spot.snapshot_watermark, Some(10));
        assert_eq!(spot.event_watermark, Some(11));
        assert_eq!(spot.channel_epoch, Some(1));
        assert_eq!(spot.recovery_buffer_depth, 0);
    }

    #[test]
    fn stream_reconnect_backoff_is_bounded_and_segment_stable() {
        let first = stream_reconnect_delay("account.test.spot", 1);
        let second = stream_reconnect_delay("account.test.spot", 2);
        let saturated = stream_reconnect_delay("account.test.spot", 100);

        assert!(second > first);
        assert_eq!(first, stream_reconnect_delay("account.test.spot", 1));
        assert!(saturated <= Duration::from_secs(10));
    }
}

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}
