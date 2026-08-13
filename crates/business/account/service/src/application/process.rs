//! Account process. It owns lifecycle and control transport, not
//! account business state; the latter remains inside AccountActor.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
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
use crate::services::integration::{
    AccountAsyncEventSource, AccountAsyncSnapshotGateway, AccountInstrumentResolver,
};
use crate::services::refresh::RefreshFetch;
use kairos_domain_types::AccountId;
use kairos_integration::application::{ConnectionHealth, ConnectionLifecycle, IntegrationError};
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
    lease_file: Option<PathBuf>,
    lease_instance_id: Option<String>,
    snapshot_dirty: bool,
    refresh_started: Option<Instant>,
    async_account_streams: Vec<AccountAsyncEventSource>,
    instrument_resolver: AccountInstrumentResolver,
    async_snapshot_source: Option<AccountAsyncSnapshotGateway>,
    async_refresh_pending: bool,
    async_refresh_task: Option<tokio::task::JoinHandle<()>>,
    initial_refresh_complete: bool,
    async_stream_health: BTreeMap<String, ConnectionHealth>,
    stream_resync_required: BTreeSet<String>,
    external_event_watermarks: BTreeMap<(String, String), (u64, Option<u64>)>,
    external_event_ids: BTreeSet<(String, String, String)>,
    external_event_id_order: VecDeque<(String, String, String)>,
    recovery_events: VecDeque<kairos_integration::application::ExternalAccountEventEnvelope>,
    recovery_overflowed: bool,
    async_event_queue_depth: std::sync::Arc<std::sync::atomic::AtomicUsize>,
    business_time_unix_nanos: Option<u64>,
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

struct AsyncRefreshCompletion {
    source: AccountAsyncSnapshotGateway,
    account_id: String,
    fetches: Vec<RefreshFetch>,
}

struct AsyncStreamHealthUpdate {
    binding_id: String,
    health: ConnectionHealth,
}

impl AccountProcess {
    pub fn new(
        mut application: AccountApplication,
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
        let async_snapshot_source = application.take_async_snapshot_source();
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
            lease_file: None,
            lease_instance_id: None,
            snapshot_dirty: true,
            refresh_started: None,
            async_account_streams: Vec::new(),
            instrument_resolver: AccountInstrumentResolver::default(),
            async_snapshot_source,
            async_refresh_pending: false,
            async_refresh_task: None,
            initial_refresh_complete: false,
            async_stream_health: BTreeMap::new(),
            stream_resync_required: BTreeSet::new(),
            external_event_watermarks: BTreeMap::new(),
            external_event_ids: BTreeSet::new(),
            external_event_id_order: VecDeque::new(),
            recovery_events: VecDeque::new(),
            recovery_overflowed: false,
            async_event_queue_depth: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            business_time_unix_nanos: None,
        })
    }
}

impl AccountProcess {
    pub(crate) fn with_async_account_streams(
        mut self,
        streams: Vec<AccountAsyncEventSource>,
    ) -> Self {
        self.async_stream_health = streams
            .iter()
            .map(|stream| {
                (
                    stream.binding_id().to_owned(),
                    ConnectionHealth {
                        lifecycle: ConnectionLifecycle::Created,
                        healthy: false,
                        authenticated: false,
                        last_error: None,
                    },
                )
            })
            .collect();
        self.async_account_streams = streams;
        self
    }

    pub(crate) fn with_instrument_resolver(mut self, resolver: AccountInstrumentResolver) -> Self {
        self.instrument_resolver = resolver;
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
        let async_stream_overflow = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let async_stream_overflow_wakeup = std::sync::Arc::new(tokio::sync::Notify::new());
        let (async_event_sender, mut async_event_receiver) = mpsc::channel(256);
        let (async_stream_health_sender, mut async_stream_health_receiver) = mpsc::channel(64);
        let (async_refresh_sender, mut async_refresh_receiver) = mpsc::channel(1);
        let async_stream_tasks = self.start_async_stream_consumers(
            async_event_sender,
            async_stream_shutdown_rx,
            std::sync::Arc::clone(&async_stream_overflow),
            std::sync::Arc::clone(&async_stream_overflow_wakeup),
            async_stream_health_sender,
            std::sync::Arc::clone(&self.async_event_queue_depth),
        );
        let async_stream_enabled = !async_stream_tasks.is_empty();
        let legacy_refresh_enabled = self.application.has_refresh_worker();
        interval.tick().await;
        self.schedule_refresh(&async_refresh_sender);
        if let Err(error) = self.publish_snapshot_if_dirty() {
            self.last_error = Some(error);
        }
        let _ = self.write_health(self.business_status()).await;
        while !self.stop_requested {
            tokio::select! {
                Some(request) = receiver.recv() => {
                    let response = self.handle_request(
                        &request.target,
                        &String::from_utf8_lossy(&request.body),
                        &async_refresh_sender,
                    );
                    let _ = request.response.send(response.map_err(|error| error.to_string()));
                }
                Some(result) = async_event_receiver.recv(), if async_stream_enabled => {
                    self.async_event_queue_depth.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
                    match result {
                        Ok(event) if self.refresh_pending() || !self.initial_refresh_complete => {
                            if !self.buffer_recovery_event(event) {
                                self.last_error = Some(
                                    "account recovery event buffer overflowed; another snapshot resynchronization is required".into(),
                                );
                            }
                        }
                        Ok(event) => match self.apply_external_event(event) {
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
                                self.schedule_refresh(&async_refresh_sender);
                            }
                        },
                        Err(error) => {
                            warn!(event = "async_account_stream_failed", component = "account", error = %error, "async account stream reported a continuity failure");
                            self.last_error = Some(error.to_string());
                            self.schedule_refresh(&async_refresh_sender);
                        }
                    }
                }
                Some(update) = async_stream_health_receiver.recv(), if async_stream_enabled => {
                    self.apply_stream_health_update(update);
                    let _ = self.write_health(self.business_status()).await;
                }
                _ = async_stream_overflow_wakeup.notified(), if async_stream_enabled => {
                    if async_stream_overflow.swap(false, std::sync::atomic::Ordering::AcqRel) {
                        let error = "async account event queue overflowed; snapshot resynchronization is required".to_string();
                        warn!(event = "async_account_stream_overflow", component = "account", error = %error, "async account stream continuity was lost");
                        self.last_error = Some(error);
                        self.schedule_refresh(&async_refresh_sender);
                    }
                }
                Some(completion) = async_refresh_receiver.recv(), if self.async_refresh_pending => {
                    let resync_required = self.finish_async_refresh(completion);
                    if resync_required {
                        self.schedule_refresh(&async_refresh_sender);
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
        sender: mpsc::Sender<
            Result<kairos_integration::application::ExternalAccountEventEnvelope, IntegrationError>,
        >,
        shutdown: tokio::sync::watch::Receiver<bool>,
        overflowed: std::sync::Arc<std::sync::atomic::AtomicBool>,
        overflow_wakeup: std::sync::Arc<tokio::sync::Notify>,
        health_sender: mpsc::Sender<AsyncStreamHealthUpdate>,
        queue_depth: std::sync::Arc<std::sync::atomic::AtomicUsize>,
    ) -> Vec<tokio::task::JoinHandle<()>> {
        std::mem::take(&mut self.async_account_streams)
            .into_iter()
            .map(|mut stream| {
                let sender = sender.clone();
                let mut shutdown = shutdown.clone();
                let overflowed = std::sync::Arc::clone(&overflowed);
                let overflow_wakeup = std::sync::Arc::clone(&overflow_wakeup);
                let health_sender = health_sender.clone();
                let queue_depth = std::sync::Arc::clone(&queue_depth);
                tokio::spawn(async move {
                    let binding_id = stream.binding_id().to_owned();
                    if let Err(error) = stream.connect_channel().await {
                        queue_depth.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        if sender.try_send(Err(error)).is_err() {
                            queue_depth.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
                        }
                    }
                    let _ = health_sender
                        .send(AsyncStreamHealthUpdate {
                            binding_id: binding_id.clone(),
                            health: stream.channel_health(),
                        })
                        .await;
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
                        queue_depth.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        match sender.try_send(result) {
                            Ok(()) => {}
                            Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => {
                                queue_depth.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
                                break;
                            }
                            Err(tokio::sync::mpsc::error::TrySendError::Full(_)) => {
                                queue_depth.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
                                overflowed.store(true, std::sync::atomic::Ordering::Release);
                                overflow_wakeup.notify_one();
                                let _ = health_sender.try_send(AsyncStreamHealthUpdate {
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
                                let _ = stream.reconnect_channel().await;
                                let _ = health_sender
                                    .send(AsyncStreamHealthUpdate {
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
                                tokio::select! {
                                    _ = tokio::time::sleep(Duration::from_millis(250)) => {}
                                    changed = shutdown.changed() => {
                                        if changed.is_err() || *shutdown.borrow() {
                                            break;
                                        }
                                    }
                                }
                            }
                            let _ = health_sender
                                .send(AsyncStreamHealthUpdate {
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

    fn schedule_refresh(&mut self, async_sender: &mpsc::Sender<AsyncRefreshCompletion>) {
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
            segments: Vec::new(),
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
            let sender = async_sender.clone();
            let account_id = self.account_id.clone();
            self.async_refresh_pending = true;
            self.refresh_started = Some(Instant::now());
            self.async_refresh_task = Some(tokio::spawn(async move {
                let fetches = source.fetch(segments).await;
                let _ = sender
                    .send(AsyncRefreshCompletion {
                        source,
                        account_id,
                        fetches,
                    })
                    .await;
            }));
            info!(
                event = "account_async_refresh_started",
                component = "account",
                account_id = %self.account_id,
                "account async refresh started"
            );
            return;
        }
        if let Err(error) = self.application.start_refresh(request) {
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

    fn finish_async_refresh(&mut self, completion: AsyncRefreshCompletion) -> bool {
        self.async_refresh_task.take();
        self.async_snapshot_source = Some(completion.source);
        self.async_refresh_pending = false;
        let generation_before = self.application.generation();
        let duration_ms = self
            .refresh_started
            .take()
            .map(|started| started.elapsed().as_millis())
            .unwrap_or_default();
        match self
            .application
            .apply_refresh_fetches(&completion.account_id, completion.fetches)
        {
            Ok(report) => {
                self.initial_refresh_complete |= report.issues.is_empty();
                if report.issues.is_empty() {
                    self.external_event_watermarks.clear();
                }
                self.last_error = report.issues.first().map(|issue| issue.error.clone());
                info!(
                    event = "account_async_refresh_completed",
                    component = "account",
                    account_id = %report.account_id,
                    refreshed_segments = report.refreshed_segments.len(),
                    issues = report.issues.len(),
                    duration_ms,
                    "account async refresh completed"
                );
                self.last_refresh = Some(report);
                self.snapshot_dirty |= self.application.generation() != generation_before;
                if self
                    .last_refresh
                    .as_ref()
                    .is_some_and(|report| report.issues.is_empty())
                {
                    if self.recovery_overflowed {
                        self.recovery_events.clear();
                        self.recovery_overflowed = false;
                        self.last_error = Some(
                            "account recovery event buffer overflowed; snapshot must be repeated"
                                .into(),
                        );
                        return true;
                    }
                    if let Err(error) = self.replay_recovery_events() {
                        self.last_error = Some(error);
                        return true;
                    }
                    self.complete_stream_resyncs();
                }
            }
            Err(error) => {
                self.last_error = Some(error.to_string());
                kairos_workspace::logging::record_counter("kairos.operation.failed", 1);
            }
        }
        false
    }

    fn buffer_recovery_event(
        &mut self,
        event: kairos_integration::application::ExternalAccountEventEnvelope,
    ) -> bool {
        const RECOVERY_EVENT_CAPACITY: usize = 4_096;
        if self.recovery_events.len() >= RECOVERY_EVENT_CAPACITY {
            self.recovery_overflowed = true;
            return false;
        }
        self.recovery_events.push_back(event);
        true
    }

    fn apply_stream_health_update(&mut self, update: AsyncStreamHealthUpdate) {
        let binding_id = update.binding_id;
        let mut health = update.health;
        if health.lifecycle == ConnectionLifecycle::Degraded
            && health
                .last_error
                .as_deref()
                .is_some_and(|error| error.contains("resync required"))
        {
            self.stream_resync_required.insert(binding_id.clone());
        }
        if health.lifecycle == ConnectionLifecycle::Ready
            && self.stream_resync_required.contains(&binding_id)
        {
            health.lifecycle = ConnectionLifecycle::Degraded;
            health.healthy = false;
            health.last_error = Some("channel reconnected; awaiting snapshot resync".into());
        }
        self.async_stream_health.insert(binding_id, health);
    }

    fn complete_stream_resyncs(&mut self) {
        for binding_id in std::mem::take(&mut self.stream_resync_required) {
            let Some(health) = self.async_stream_health.get_mut(&binding_id) else {
                continue;
            };
            if health.last_error.as_deref() == Some("channel reconnected; awaiting snapshot resync")
                && health.authenticated
            {
                health.lifecycle = ConnectionLifecycle::Ready;
                health.healthy = true;
                health.last_error = None;
            } else if !health.healthy {
                self.stream_resync_required.insert(binding_id);
            }
        }
    }

    fn replay_recovery_events(&mut self) -> Result<(), String> {
        while let Some(event) = self.recovery_events.pop_front() {
            match self.apply_external_event(event) {
                Ok(applied) => self.snapshot_dirty |= applied > 0,
                Err(error) => {
                    self.recovery_events.clear();
                    return Err(error);
                }
            }
        }
        Ok(())
    }

    fn apply_external_event(
        &mut self,
        envelope: kairos_integration::application::ExternalAccountEventEnvelope,
    ) -> Result<usize, String> {
        let telemetry_binding_id = envelope.binding_id.clone();
        let telemetry_channel_id = envelope.channel_id.clone();
        let telemetry_channel_epoch = envelope.channel_epoch;
        let telemetry_provider_sequence = envelope.provider_sequence;
        let telemetry_event_id_present = envelope.provider_event_id.is_some();
        let telemetry_event_kind = external_account_event_kind(&envelope.payload);
        let generation_before = self.application.generation();
        let event_sequence_before = self.application.event_sequence();
        let channel_key = (envelope.binding_id.clone(), envelope.channel_id.clone());
        if envelope.provider_event_id.as_ref().is_some_and(|event_id| {
            self.external_event_ids.contains(&(
                envelope.binding_id.clone(),
                envelope.channel_id.clone(),
                event_id.clone(),
            ))
        }) {
            return Ok(0);
        }

        if let Some((current_epoch, current_sequence)) =
            self.external_event_watermarks.get(&channel_key).copied()
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
            .apply_event(event)
            .map_err(|error| error.to_string())?;
        self.external_event_watermarks.insert(
            channel_key,
            (envelope.channel_epoch, envelope.provider_sequence),
        );
        if let Some(event_id) = envelope.provider_event_id {
            const RETAINED_EVENT_IDS: usize = 4_096;
            let key = (envelope.binding_id, envelope.channel_id, event_id);
            if self.external_event_ids.insert(key.clone()) {
                self.external_event_id_order.push_back(key);
                if self.external_event_id_order.len() > RETAINED_EVENT_IDS {
                    if let Some(expired) = self.external_event_id_order.pop_front() {
                        self.external_event_ids.remove(&expired);
                    }
                }
            }
        }
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
        self.async_refresh_pending || self.application.refresh_pending()
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
                self.initial_refresh_complete |= report.issues.is_empty();
                if report.issues.is_empty() {
                    self.external_event_watermarks.clear();
                }
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
        async_refresh_sender: &mpsc::Sender<AsyncRefreshCompletion>,
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
            "/v1/time/advance" => {
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
                self.schedule_refresh(async_refresh_sender);
                (
                    if self.refresh_pending() { 202 } else { 503 },
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
        let provider_channels = self
            .async_stream_health
            .iter()
            .map(|(binding_id, health)| {
                json!({
                    "binding_id": binding_id,
                    "lifecycle": connection_lifecycle_name(health.lifecycle),
                    "healthy": health.healthy,
                    "authenticated": health.authenticated,
                    "last_error": health.last_error,
                    "required": true,
                })
            })
            .collect::<Vec<_>>();
        json!({"status": self.business_status(), "pid": std::process::id(), "account_id": self.account_id, "actor_id": self.application.actor_id(), "generation": self.application.generation(), "event_sequence": self.application.event_sequence(), "business_time_unix_nanos": self.business_time_unix_nanos, "stream_queue_depth": self.async_event_queue_depth.load(std::sync::atomic::Ordering::Relaxed) + self.recovery_events.len(), "persistence_queue_depth": self.application.persistence_queue_depth(), "refresh_pending": self.refresh_pending(), "initial_refresh_complete": self.initial_refresh_complete, "provider_channels": provider_channels, "last_error": self.last_error, "last_refresh": self.last_refresh, "lease_valid": self.lease_valid()})
    }

    fn business_status(&self) -> &'static str {
        if !self.lease_valid() {
            return "unavailable";
        }
        if !self.initial_refresh_complete {
            return "starting";
        }
        if self
            .async_stream_health
            .values()
            .any(|health| !health.healthy || !health.authenticated)
        {
            return "unavailable";
        }
        if self.last_error.is_some() {
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::composition::account::compose_in_memory_account_application;
    use crate::composition::empty_snapshot;
    use crate::domain::{AccountSegment, ExternalAccountIdentity, SegmentKey};
    use kairos_integration::application::{
        ExternalAccountEvent, ExternalAccountSnapshot, ExternalAccountStatus, ExternalEventEnvelope,
    };

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
        ExternalEventEnvelope {
            participant: kairos_integration::application::ParticipantRef::new(
                kairos_integration::application::ParticipantKind::Exchange,
                "test",
            )
            .unwrap(),
            binding_id: "account.test".into(),
            channel_id: "private".into(),
            channel_epoch: 1,
            provider_event_id: Some(event_id.into()),
            provider_sequence: Some(sequence),
            observed_at_unix_nanos: sequence.into(),
            received_at_unix_nanos: sequence.into(),
            payload: ExternalAccountEvent::Snapshot(ExternalAccountSnapshot {
                segment_key: kairos_domain_types::SegmentKey::new("spot").unwrap(),
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

    #[test]
    fn readiness_requires_initial_refresh() {
        let mut process = process();
        assert_eq!(process.business_status(), "starting");
        process.initial_refresh_complete = true;
        assert_eq!(process.business_status(), "ready");
    }

    #[test]
    fn external_event_envelope_deduplicates_and_detects_sequence_gaps() {
        let mut process = process();
        assert_eq!(process.apply_external_event(envelope(1, "one")).unwrap(), 1);
        assert_eq!(process.apply_external_event(envelope(1, "one")).unwrap(), 0);
        let error = process
            .apply_external_event(envelope(3, "three"))
            .unwrap_err();
        assert!(error.contains("sequence gap"));
    }

    #[test]
    fn snapshot_recovery_barrier_replays_buffered_events_in_sequence() {
        let mut process = process();
        assert!(process.buffer_recovery_event(envelope(1, "one")));
        assert!(process.buffer_recovery_event(envelope(2, "two")));
        assert_eq!(process.application.event_sequence(), 0);

        process.replay_recovery_events().unwrap();

        assert_eq!(process.application.event_sequence(), 2);
        assert!(process.recovery_events.is_empty());
        assert_eq!(
            process
                .external_event_watermarks
                .get(&("account.test".into(), "private".into())),
            Some(&(1, Some(2)))
        );
    }

    #[test]
    fn reconnected_stream_waits_for_snapshot_before_returning_ready() {
        let mut process = process();
        process.apply_stream_health_update(AsyncStreamHealthUpdate {
            binding_id: "account.test".into(),
            health: ConnectionHealth {
                lifecycle: ConnectionLifecycle::Degraded,
                healthy: false,
                authenticated: true,
                last_error: Some("consumer overflow; resync required".into()),
            },
        });
        process.apply_stream_health_update(AsyncStreamHealthUpdate {
            binding_id: "account.test".into(),
            health: ConnectionHealth {
                lifecycle: ConnectionLifecycle::Ready,
                healthy: true,
                authenticated: true,
                last_error: None,
            },
        });
        assert_eq!(
            process.async_stream_health["account.test"].lifecycle,
            ConnectionLifecycle::Degraded
        );

        process.complete_stream_resyncs();

        assert_eq!(
            process.async_stream_health["account.test"].lifecycle,
            ConnectionLifecycle::Ready
        );
        assert!(process.stream_resync_required.is_empty());
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
