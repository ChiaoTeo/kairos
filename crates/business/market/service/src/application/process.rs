//! Market process runtime and strategy command boundary.

use crate::application::MarketApplication;
use crate::domain::snapshot::MarketCurrentView;
use crate::services::control::{
    spawn_server as spawn_control_server, EngineCommand, MarketHttpResponse,
};
use crate::services::event_publication::{EventFanout, EventPublication};
use crate::services::reference::{resolve_market, resolve_option_markets};
use crate::services::reference_projection::ReferenceProjection;
use crate::services::sources::SourceActivator;
use crate::{MarketDescriptor, SubscriptionId};
use kairos_domain_types::Sequence;
use kairos_protocol::InstanceIdentity;
use kairos_workspace::runtime::{HEALTH_PATH, SNAPSHOT_PATH, STOP_PATH};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::net::UnixListener;
use tokio::sync::mpsc;
use tokio::sync::mpsc::error::TrySendError;
use tokio::sync::mpsc::{Receiver, Sender};
use tokio::time::{self, MissedTickBehavior};
use tracing::{error, info, warn};

const MAX_COMMAND_RESULTS: usize = 4_096;

#[derive(Debug, Deserialize)]
struct SubscribePayload {
    subject: String,
    selectors: Vec<String>,
    exchange: Option<String>,
    market_type: Option<String>,
    #[serde(default)]
    asset_type: Option<String>,
    #[serde(default)]
    params: BTreeMap<String, Value>,
    dynamic: bool,
}

#[derive(Debug, Deserialize)]
struct CommandEnvelope<T> {
    schema_version: u16,
    command_id: String,
    idempotency_key: String,
    operation: String,
    strategy_id: String,
    #[serde(default)]
    launch_id: Option<String>,
    instance_id: String,
    payload: T,
}

#[derive(Debug, Deserialize)]
struct SubscribeRequest {
    request_id: String,
    strategy_id: String,
    launch_id: Option<String>,
    instance_id: String,
    subject: String,
    selectors: Vec<String>,
    exchange: Option<String>,
    market_type: Option<String>,
    #[serde(default)]
    asset_type: Option<String>,
    params: BTreeMap<String, Value>,
    dynamic: bool,
}

#[derive(Debug, Deserialize)]
struct UnsubscribePayload {
    subscription_id: String,
}

#[derive(Debug, Default, Deserialize)]
struct ReleaseOwnerPayload {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceEvent {
    pub sequence: Sequence,
}

pub trait ReferenceChangeSource {
    fn next_event(&mut self) -> Result<Option<ReferenceEvent>, String>;
}

pub struct MarketProcess {
    actor_task: MarketActorTask,
    socket_path: PathBuf,
    event_socket_path: PathBuf,
    reference_events: Option<Box<dyn ReferenceChangeSource>>,
    reference_event_sequence: Option<Sequence>,
    reference_poll_interval: Duration,
    publication_queue_capacity: usize,
    lifecycle_guard: Option<Box<dyn Send>>,
    event_publisher: Option<kairos_transport::AeronBytePublisher>,
}

pub(crate) struct MarketProcessSettings {
    pub(crate) snapshot_interval: Duration,
    pub(crate) freshness_check_interval: Duration,
    pub(crate) freshness_max_age: Duration,
    pub(crate) reference_recovery_interval: Duration,
    pub(crate) shutdown_timeout: Duration,
    pub(crate) publication_queue_capacity: usize,
}

/// Application-owned publication capability. Concrete storage and wire
/// encoding are selected by composition and never cross this boundary.
pub trait MarketSnapshotPublisher: Send {
    fn publish(&mut self, snapshot: &MarketCurrentView) -> Result<(), String>;
}

enum MarketHistoryRecorder {
    Noop,
    Jsonl(crate::services::history::JsonlMarketHistoryRecorder),
}

impl MarketHistoryRecorder {
    async fn record(&mut self, events: &[(u64, crate::MarketEvent)]) -> Result<(), String> {
        match self {
            Self::Noop => Ok(()),
            Self::Jsonl(recorder) => recorder.record(events).await,
        }
    }

    async fn shutdown(&mut self) -> Result<(), String> {
        match self {
            Self::Noop => Ok(()),
            Self::Jsonl(recorder) => recorder.shutdown().await,
        }
    }
}

/// The sole asynchronous task which serializes every MarketActor mutation.
/// This is task/mailbox mechanics around the Actor, not a second business
/// runtime or state owner.
struct MarketActorTask {
    application: MarketApplication,
    publisher: Box<dyn MarketSnapshotPublisher>,
    event_actor_id: String,
    event_identity: InstanceIdentity,
    snapshot_interval: Duration,
    freshness_check_interval: Duration,
    freshness_max_age: Duration,
    reference_recovery_interval: Duration,
    shutdown_timeout: Duration,
    stop_requested: bool,
    reference: ReferenceProjection,
    command_results: BTreeMap<String, CachedCommandResult>,
    source_activator: Option<Box<dyn SourceActivator>>,
    history_recorder: MarketHistoryRecorder,
}

#[derive(Clone)]
struct CachedCommandResult {
    request_body: String,
    status: u16,
    payload: Value,
}

impl MarketProcess {
    pub fn new<P: MarketSnapshotPublisher + 'static>(
        application: MarketApplication,
        publisher: P,
        socket_path: impl Into<PathBuf>,
        event_socket_path: impl Into<PathBuf>,
        interval: Duration,
    ) -> Result<Self, String> {
        Self::new_with_identity(
            application,
            publisher,
            socket_path,
            event_socket_path,
            interval,
            InstanceIdentity::default(),
        )
    }

    pub fn new_with_identity<P: MarketSnapshotPublisher + 'static>(
        application: MarketApplication,
        publisher: P,
        socket_path: impl Into<PathBuf>,
        event_socket_path: impl Into<PathBuf>,
        interval: Duration,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        Self::new_configured(
            application,
            publisher,
            socket_path,
            event_socket_path,
            identity,
            MarketProcessSettings {
                snapshot_interval: interval,
                freshness_check_interval: interval,
                freshness_max_age: Duration::from_secs(5),
                reference_recovery_interval: interval,
                shutdown_timeout: Duration::from_secs(5),
                publication_queue_capacity: 256,
            },
        )
    }

    pub(crate) fn new_configured<P: MarketSnapshotPublisher + 'static>(
        application: MarketApplication,
        publisher: P,
        socket_path: impl Into<PathBuf>,
        event_socket_path: impl Into<PathBuf>,
        identity: InstanceIdentity,
        settings: MarketProcessSettings,
    ) -> Result<Self, String> {
        Self::new_configured_with_activator(
            application,
            publisher,
            socket_path,
            event_socket_path,
            identity,
            settings,
            None,
        )
    }

    pub(crate) fn new_configured_with_activator<P: MarketSnapshotPublisher + 'static>(
        application: MarketApplication,
        publisher: P,
        socket_path: impl Into<PathBuf>,
        event_socket_path: impl Into<PathBuf>,
        identity: InstanceIdentity,
        settings: MarketProcessSettings,
        source_activator: Option<Box<dyn SourceActivator>>,
    ) -> Result<Self, String> {
        for (name, value) in [
            ("snapshot interval", settings.snapshot_interval),
            (
                "freshness check interval",
                settings.freshness_check_interval,
            ),
            ("freshness max age", settings.freshness_max_age),
            (
                "reference recovery interval",
                settings.reference_recovery_interval,
            ),
            ("shutdown timeout", settings.shutdown_timeout),
        ] {
            if value.is_zero() {
                return Err(format!("market process {name} must be positive"));
            }
        }
        if settings.publication_queue_capacity == 0 {
            return Err("market publication queue capacity must be positive".into());
        }
        let event_actor_id = application.snapshot().actor_id;
        Ok(Self {
            actor_task: MarketActorTask {
                application,
                publisher: Box::new(publisher),
                event_actor_id: event_actor_id.to_string(),
                event_identity: identity,
                snapshot_interval: settings.snapshot_interval,
                freshness_check_interval: settings.freshness_check_interval,
                freshness_max_age: settings.freshness_max_age,
                reference_recovery_interval: settings.reference_recovery_interval,
                shutdown_timeout: settings.shutdown_timeout,
                stop_requested: false,
                reference: ReferenceProjection::new(),
                command_results: BTreeMap::new(),
                source_activator,
                history_recorder: MarketHistoryRecorder::Noop,
            },
            socket_path: socket_path.into(),
            event_socket_path: event_socket_path.into(),
            reference_events: None,
            reference_event_sequence: None,
            reference_poll_interval: settings.reference_recovery_interval,
            publication_queue_capacity: settings.publication_queue_capacity,
            lifecycle_guard: None,
            event_publisher: None,
        })
    }

    pub(crate) fn with_lifecycle_guard<T: Send + 'static>(mut self, guard: T) -> Self {
        self.lifecycle_guard = Some(Box::new(guard));
        self
    }

    pub fn with_aeron_event_publisher(
        mut self,
        publisher: kairos_transport::AeronBytePublisher,
    ) -> Self {
        self.event_publisher = Some(publisher);
        self
    }

    pub fn with_reference_database(mut self, path: impl Into<PathBuf>) -> Self {
        self.actor_task.reference.configure(path);
        self
    }

    pub fn with_reference_events<S: ReferenceChangeSource + 'static>(mut self, source: S) -> Self {
        self.reference_events = Some(Box::new(source));
        self.actor_task.reference.require_recovery(None);
        self
    }

    pub(crate) fn with_history_recorder(
        mut self,
        recorder: crate::services::history::JsonlMarketHistoryRecorder,
    ) -> Self {
        self.actor_task.history_recorder = MarketHistoryRecorder::Jsonl(recorder);
        self
    }

    pub async fn run(self) -> Result<(), Box<dyn std::error::Error>> {
        let MarketProcess {
            actor_task,
            socket_path,
            event_socket_path,
            mut reference_events,
            mut reference_event_sequence,
            reference_poll_interval,
            publication_queue_capacity,
            lifecycle_guard,
            event_publisher,
        } = self;
        let _lifecycle_guard = lifecycle_guard;
        remove_socket(&socket_path)?;
        remove_socket(&event_socket_path)?;
        if let Some(parent) = socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        if let Some(parent) = event_socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&socket_path)?;
        let event_listener = UnixListener::bind(&event_socket_path)?;
        let snapshot_interval = actor_task.snapshot_interval;
        let publication_shutdown_timeout = actor_task.shutdown_timeout;
        let (http_sender, control_receiver) = mpsc::channel(1_024);
        let (event_sender, mut event_receiver) = mpsc::channel(publication_queue_capacity);
        let actor_sender = http_sender.clone();
        let http_server = spawn_control_server(listener, http_sender);
        let mut actor_task = tokio::spawn(async move {
            actor_task
                .run_actor_loop(control_receiver, event_sender)
                .await
        });
        kairos_workspace::logging::record_gauge("kairos.process.ready", 1);
        info!(event = "process_starting", component = "market", socket = %socket_path.display(), event_socket = %event_socket_path.display(), snapshot_interval_ms = snapshot_interval.as_millis(), "market process starting");
        log_event(
            "info",
            "market process ready",
            json!({
                "socket": socket_path,
                "event_socket": event_socket_path,
                "snapshot_interval_ms": snapshot_interval.as_millis(),
            }),
        );
        info!(event = "process_ready", component = "market", socket = %socket_path.display(), "market control socket ready");
        let mut event_fanout = EventFanout::new(publication_queue_capacity);
        let event_publisher = event_publisher;
        let mut pending_reference_command = None;
        let mut reference_ticks = time::interval(reference_poll_interval);
        reference_ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        let actor_result = loop {
            tokio::select! {
                accepted = event_listener.accept() => {
                    let (stream, _) = accepted?;
                    event_fanout.add_client(stream);
                }
                Some(payload) = event_receiver.recv() => {
                    if let Some(publisher) = event_publisher.as_ref() {
                        publisher.publish(&payload)?;
                    }
                    event_fanout.publish(payload);
                }
                _ = reference_ticks.tick(), if reference_events.is_some() => {
                    if let Err(error) = forward_reference_events(
                        &mut reference_events,
                        &actor_sender,
                        &mut reference_event_sequence,
                        &mut pending_reference_command,
                    ) {
                        kairos_workspace::logging::record_counter("kairos.retry", 1);
                        log_event("warn", "reference event forwarding deferred", json!({"error": error}));
                    }
                }
                result = &mut actor_task => {
                    break result;
                }
            }
        };
        while let Ok(payload) = event_receiver.try_recv() {
            if let Some(publisher) = event_publisher.as_ref() {
                publisher.publish(&payload)?;
            }
            event_fanout.publish(payload);
        }
        event_fanout.shutdown(publication_shutdown_timeout).await;
        remove_socket(&socket_path)?;
        remove_socket(&event_socket_path)?;
        info!(
            event = "process_stopped",
            component = "market",
            "market process stopped"
        );
        http_server.abort();
        let _ = http_server.await;
        actor_result
            .map_err(|error| std::io::Error::other(error.to_string()))?
            .map_err(std::io::Error::other)?;
        Ok(())
    }
}

impl MarketActorTask {
    async fn run_actor_loop(
        mut self,
        mut control_receiver: Receiver<EngineCommand>,
        event_sender: Sender<Vec<u8>>,
    ) -> Result<(), String> {
        info!(
            event = "actor_task_starting",
            component = "market",
            "market actor task starting"
        );
        if let Err(error) = self.recover_reference_projection() {
            kairos_workspace::logging::record_counter("kairos.retry", 1);
            log_event(
                "warn",
                "initial Reference projection recovery deferred",
                json!({"error": error}),
            );
        }
        self.publish_snapshot()?;
        let mut event_publication = EventPublication::new(
            self.event_actor_id.clone(),
            self.event_identity.clone(),
            event_sender,
        );
        let mut snapshot_ticks = time::interval(self.snapshot_interval);
        snapshot_ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        let mut freshness_ticks = time::interval(self.freshness_check_interval);
        freshness_ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        let mut reference_recovery_ticks = time::interval(self.reference_recovery_interval);
        reference_recovery_ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        while !self.stop_requested {
            let sources_enabled = self.application.has_sources();
            tokio::select! {
                Some(command) = control_receiver.recv() => {
                    match command {
                        EngineCommand::Http(request) => {
                            let body = String::from_utf8_lossy(&request.body);
                            let mut response = self.handle_request(&request.method, &request.path, &body).await;
                            if matches!(
                                request.path.as_str(),
                                "/v1/subscribe"
                                    | "/v1/unsubscribe"
                                    | "/v1/subscriptions/release-owner"
                            )
                                && (200..300).contains(&response.status)
                            {
                                if let Some(activator) = self.source_activator.as_deref_mut() {
                                    if let Err(error) = self.application.activate_sources_for_subscriptions(activator).await {
                                        rollback_subscribe_intent(&mut self.application, &body);
                                        response = MarketHttpResponse {
                                            status: 422,
                                            payload: json!({"error":{"code":"market.source_unavailable","message":error.to_string(),"retryable":true}}),
                                        };
                                    }
                                }
                                if let Err(error) = self.application.sync_source_subscriptions().await {
                                    if response.status < 300 && request.path == "/v1/subscribe" {
                                        rollback_subscribe_intent(&mut self.application, &body);
                                        response = MarketHttpResponse {
                                            status: 422,
                                            payload: json!({"error":{"code":"market.subscription_unavailable","message":error.to_string(),"retryable":true}}),
                                        };
                                    }
                                    log_event(
                                        "warn",
                                        "market source reconciliation deferred",
                                        json!({"error": error.to_string()}),
                                    );
                                }
                                self.refresh_subscription_status(&mut response);
                            }
                            // Activation/reconciliation happens after the base request
                            // handler. Keep the idempotency cache aligned with the
                            // final response (including a 422 rollback).
                            self.refresh_command_result(&body, &response);
                            let _ = request.response.send(response);
                        }
                        EngineCommand::ReferenceChanged { sequence, gap } => {
                            if gap {
                                kairos_workspace::logging::record_counter("kairos.event.gap", 1);
                            }
                            self.reference.require_recovery(Some(sequence));
                        }
                    }
                }
                input = self.application.next_source_input(), if sources_enabled => {
                    let Some(input) = input else {
                        return Err("market source input channel closed".into());
                    };
                    self.application
                        .apply_source_input(input)
                        .await
                        .map_err(|error| error.to_string())?;
                    // Status/epoch and subscription acknowledgements can make
                    // new commands eligible. Reconcile on the wake-up that
                    // changed state instead of waiting for a maintenance tick.
                    if let Err(error) = self.application.sync_source_subscriptions().await {
                        log_event(
                            "warn",
                            "market source reconciliation deferred",
                            json!({"error": error.to_string()}),
                        );
                    }
                    let events = self.application.drain_events_limited(1_024);
                    self.history_recorder.record(&events).await?;
                    event_publication.publish(events)?;
                }
                _ = snapshot_ticks.tick() => {
                    self.publish_snapshot()?;
                }
                _ = freshness_ticks.tick() => {
                    let now_nanos = SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_nanos()
                        .min(u128::from(u64::MAX)) as u64;
                    let max_age_nanos = self
                        .freshness_max_age
                        .as_nanos()
                        .min(u128::from(u64::MAX)) as u64;
                    self.application.evaluate_freshness(now_nanos, max_age_nanos);
                }
                _ = reference_recovery_ticks.tick(), if self.reference.recovery_needed() => {
                    match self.recover_reference_projection() {
                        Ok(()) => {
                            if let Some(activator) = self.source_activator.as_deref_mut() {
                                self.application
                                    .activate_sources_for_subscriptions(activator)
                                    .await
                                    .map_err(|error| error.to_string())?;
                            }
                            if let Err(error) = self.application.sync_source_subscriptions().await {
                                log_event(
                                    "warn",
                                    "market source reconciliation after Reference recovery deferred",
                                    json!({"error": error.to_string()}),
                                );
                            }
                        }
                        Err(error) => {
                            kairos_workspace::logging::record_counter("kairos.retry", 1);
                            log_event("error", "reference projection recovery failed", json!({"error": error}));
                        }
                    }
                }
            }
            // Retry a bounded publication queue whenever any engine input or
            // maintenance wake-up occurs. Finite replay must not wait forever
            // merely because its final event first encountered a full queue.
            event_publication.flush()?;
            if self.application.sources_complete() && event_publication.is_empty() {
                self.stop_requested = true;
            }
        }
        self.application
            .shutdown_sources(self.shutdown_timeout)
            .await?;
        let events = self.application.drain_events_limited(1_024);
        self.history_recorder.record(&events).await?;
        event_publication.publish(events)?;
        event_publication.drain(self.shutdown_timeout).await?;
        self.history_recorder.shutdown().await?;
        self.publish_snapshot()?;
        info!(
            event = "actor_task_stopped",
            component = "market",
            "market actor task stopped"
        );
        Ok(())
    }

    fn publish_snapshot(&mut self) -> Result<(), String> {
        let snapshot = self.application.current_view();
        kairos_workspace::logging::record_gauge(
            "kairos.snapshot.generation",
            snapshot.generation.get(),
        );
        let now_nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let now_nanos = u64::try_from(now_nanos).unwrap_or(u64::MAX);
        let max_lag_ms = snapshot
            .freshness
            .values()
            .map(|freshness| {
                now_nanos.saturating_sub(freshness.last_received_time_unix_nanos.get()) / 1_000_000
            })
            .max()
            .unwrap_or_default();
        kairos_workspace::logging::record_gauge("kairos.event.lag", max_lag_ms);
        self.publisher.publish(&snapshot)
    }

    fn recover_reference_projection(&mut self) -> Result<(), String> {
        if !self.reference.recovery_needed() {
            return Ok(());
        }
        let markets = self.reference.recover()?;
        self.application
            .reconcile_reference(markets)
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    async fn handle_request(&mut self, method: &str, path: &str, body: &str) -> MarketHttpResponse {
        let started = Instant::now();
        log_event(
            "info",
            "market control request",
            json!({"method": method, "path": path}),
        );
        let command_key = if matches!(
            path,
            "/v1/subscribe" | "/v1/unsubscribe" | "/v1/subscriptions/release-owner"
        ) {
            serde_json::from_str::<Value>(body)
                .ok()
                .and_then(|value| {
                    value
                        .get("idempotency_key")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                })
                .filter(|value| !value.trim().is_empty())
        } else {
            None
        };
        if let Some(key) = command_key.as_deref() {
            if let Some(cached) = self.command_results.get(key).cloned() {
                if cached.request_body == body {
                    return MarketHttpResponse {
                        status: cached.status,
                        payload: cached.payload,
                    };
                }
                return MarketHttpResponse {
                    status: 409,
                    payload: json!({
                        "error": {
                            "code": "command.idempotency_conflict",
                            "message": "idempotency key was already used with a different request",
                            "retryable": false
                        }
                    }),
                };
            }
        }
        let (status, payload) = match path {
            HEALTH_PATH => (200, self.health()),
            SNAPSHOT_PATH => match serde_json::to_value(self.application.snapshot()) {
                Ok(value) => (200, value),
                Err(error) => (500, json!({"error": error.to_string()})),
            },
            "/v1/subscribe" => self.subscribe(body),
            "/v1/unsubscribe" => self.unsubscribe(body),
            "/v1/subscriptions/release-owner" => self.release_owner(body),
            "/v1/recover" => match self.application.recover_sources().await {
                Ok(()) => (202, json!({"status":"recovering"})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/replay/pause" => match self.application.set_replay_paused(true).await {
                Ok(()) => (202, json!({"status":"paused"})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/replay/resume" => match self.application.set_replay_paused(false).await {
                Ok(()) => (202, json!({"status":"running"})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            STOP_PATH => {
                self.stop_requested = true;
                (202, json!({"status":"stopping"}))
            }
            _ => (404, json!({"error":"unknown market control path"})),
        };
        if let Some(key) = command_key {
            self.command_results.insert(
                key,
                CachedCommandResult {
                    request_body: body.to_owned(),
                    status,
                    payload: payload.clone(),
                },
            );
            while self.command_results.len() > MAX_COMMAND_RESULTS {
                let Some(oldest_key) = self.command_results.keys().next().cloned() else {
                    break;
                };
                self.command_results.remove(&oldest_key);
            }
        }
        info!(event = "control_response", component = "market", method = %method, path = %path, status, duration_ms = started.elapsed().as_millis(), "market control response sent");
        MarketHttpResponse { status, payload }
    }

    fn refresh_command_result(&mut self, body: &str, response: &MarketHttpResponse) {
        let Some(key) = serde_json::from_str::<Value>(body)
            .ok()
            .and_then(|value| {
                value
                    .get("idempotency_key")
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            })
            .filter(|value| !value.trim().is_empty())
        else {
            return;
        };
        self.command_results.insert(
            key,
            CachedCommandResult {
                request_body: body.to_owned(),
                status: response.status,
                payload: response.payload.clone(),
            },
        );
        while self.command_results.len() > MAX_COMMAND_RESULTS {
            let Some(oldest_key) = self.command_results.keys().next().cloned() else {
                break;
            };
            self.command_results.remove(&oldest_key);
        }
    }

    fn refresh_subscription_status(&self, response: &mut MarketHttpResponse) {
        let Some(object) = response.payload.as_object_mut() else {
            return;
        };
        let Some(subscription_id) = object
            .get("subscription_id")
            .and_then(Value::as_str)
            .and_then(|value| SubscriptionId::new(value).ok())
        else {
            return;
        };
        if let Some(status) = self.application.subscription_status(&subscription_id) {
            object.insert("subscription_status".into(), json!(status));
        }
    }

    fn health(&self) -> Value {
        let snapshot = self.application.snapshot();
        json!({
            "pid": std::process::id(),
            "status": match snapshot.feed_status {
                // A server without a configured feed is still available for
                // control-plane validation and subscription commands.
                crate::FeedStatus::Disconnected => "ready",
                crate::FeedStatus::Ready => "ready",
                crate::FeedStatus::Reconnecting => "reconnecting",
                crate::FeedStatus::WarmingUp => "warming_up",
                crate::FeedStatus::Degraded => "degraded",
            },
            "actor_id": snapshot.actor_id,
            "generation": snapshot.generation,
            "event_sequence": snapshot.event_sequence,
            "subscription_count": snapshot.subscriptions.len(),
            "subscriptions": snapshot.subscriptions.iter().map(|subscription| json!({
                "id": subscription.id,
                "status": subscription.status,
                "member_status": subscription.member_status,
            })).collect::<Vec<_>>(),
            "source_count": snapshot.sources.len(),
            "feed_status": snapshot.feed_status,
        })
    }

    fn subscribe(&mut self, body: &str) -> (u16, Value) {
        let value: CommandEnvelope<SubscribePayload> = match serde_json::from_str(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid subscribe command: {error}"),"retryable":false}}),
                )
            }
        };
        if value.schema_version != 1
            || value.operation != "market.subscribe"
            || value.command_id.trim().is_empty()
            || value.idempotency_key.trim().is_empty()
        {
            return (
                422,
                json!({"error":{"code":"command.invalid_envelope","message":"unsupported market command schema or operation","retryable":false}}),
            );
        }
        let request = SubscribeRequest {
            request_id: value.command_id,
            strategy_id: value.strategy_id,
            launch_id: value.launch_id,
            instance_id: value.instance_id,
            subject: value.payload.subject,
            selectors: value.payload.selectors,
            exchange: value.payload.exchange,
            market_type: value.payload.market_type,
            asset_type: value.payload.asset_type,
            params: value.payload.params,
            dynamic: value.payload.dynamic,
        };
        if request.request_id.trim().is_empty()
            || request.strategy_id.trim().is_empty()
            || request.instance_id.trim().is_empty()
            || request.subject.trim().is_empty()
        {
            return (
                422,
                json!({"error":"request_id, strategy_id, instance_id and subject are required"}),
            );
        }
        let chain_mode = request
            .params
            .get("mode")
            .and_then(Value::as_str)
            .is_some_and(|mode| mode.eq_ignore_ascii_case("chain"));
        let source_symbol = request
            .subject
            .strip_prefix("market.")
            .unwrap_or(&request.subject)
            .to_owned();
        let exchange = request.exchange.clone().unwrap_or_else(|| "binance".into());
        let market_type = request.market_type.clone().unwrap_or_else(|| "spot".into());
        let subscription_id = match SubscriptionId::new(request.request_id.clone()) {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        if chain_mode {
            let underlying = match request.params.get("underlying").and_then(Value::as_str) {
                Some(value) if !value.trim().is_empty() => value.trim().to_owned(),
                _ => {
                    return (
                        422,
                        json!({"error":"chain subscription requires params.underlying"}),
                    )
                }
            };
            let Some(reference_markets) = self.reference.markets() else {
                return (422, json!({"error":"Reference snapshot is not ready"}));
            };
            if market_type != "options" {
                return (
                    422,
                    json!({"error":"chain subscription requires market_type=options"}),
                );
            }
            let markets = match resolve_option_markets(
                reference_markets,
                &exchange,
                request.asset_type.as_deref(),
                &underlying,
            ) {
                Ok(value) if !value.is_empty() => value,
                Ok(_) => {
                    return (
                        422,
                        json!({"error":format!("Reference has no active option markets for {underlying}")}),
                    )
                }
                Err(error) => return (422, json!({"error": error})),
            };
            let exchange_id = match kairos_domain_types::Exchange::new(exchange.clone()) {
                Ok(value) => value,
                Err(error) => return (422, json!({"error": error.to_string()})),
            };
            let query = crate::MarketSelectionQuery {
                exchange_id: Some(exchange_id),
                market_type: Some(market_type),
                asset_type: request.asset_type.clone(),
                underlying_instrument_id: markets[0].underlying_instrument_id.as_deref().map(
                    |value| {
                        kairos_domain_types::InstrumentId::new(value)
                            .expect("valid underlying instrument id")
                    },
                ),
                active_only: true,
                ..Default::default()
            };
            let owner_id = strategy_subscription_owner(
                request.launch_id.as_deref(),
                &request.instance_id,
                &request.strategy_id,
            );
            let result = self.application.subscribe_dynamic_with_selectors(
                subscription_id.clone(),
                owner_id,
                query,
                markets,
                request.selectors.clone(),
            );
            let command_id = request.request_id.clone();
            return match result {
                Err(error) => (
                    422,
                    json!({"status":"rejected","error":{"code":"market.subscription_rejected","message":error.to_string()}}),
                ),
                Ok(diff) => {
                    log_event(
                        "info",
                        "market dynamic subscription accepted",
                        json!({
                            "request_id": request.request_id,
                            "strategy_id": request.strategy_id,
                            "underlying": underlying,
                            "added": diff.added.len(),
                            "removed": diff.removed.len(),
                        }),
                    );
                    (
                        202,
                        json!({
                            "schema_version": 1,
                            "command_id": command_id,
                            "request_id": request.request_id,
                            "instance_id": request.instance_id,
                            "status": "accepted",
                            "subscription_status": self.application.subscription_status(&subscription_id),
                            "mode": "chain",
                            "subscription_id": subscription_id.0,
                            "added": diff.added,
                            "removed": diff.removed,
                            "changed": diff.changed,
                            "rejected": diff.rejected,
                        }),
                    )
                }
            };
        }
        if request.dynamic {
            return (
                422,
                json!({"error":"dynamic subscriptions require params.mode=chain"}),
            );
        }
        let descriptor_result = match self.reference.markets() {
            Some(reference_markets) if !reference_markets.is_empty() => resolve_market(
                reference_markets,
                &exchange,
                &market_type,
                request.asset_type.as_deref(),
                &source_symbol,
            ),
            _ => {
                let Some(market_id) = request.params.get("market_id").and_then(Value::as_str)
                else {
                    return (
                        422,
                        json!({"error":"market_id is required when Reference is unavailable"}),
                    );
                };
                let Some(instrument_id) =
                    request.params.get("instrument_id").and_then(Value::as_str)
                else {
                    return (
                        422,
                        json!({"error":"instrument_id is required when Reference is unavailable"}),
                    );
                };
                MarketDescriptor::new(
                    market_id,
                    instrument_id,
                    exchange,
                    market_type,
                    source_symbol,
                )
                .map(|mut descriptor| {
                    descriptor.asset_type = request.asset_type.clone();
                    descriptor
                })
            }
        };
        let descriptor = match descriptor_result {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        let owner_id = strategy_subscription_owner(
            request.launch_id.as_deref(),
            &request.instance_id,
            &request.strategy_id,
        );
        let result = self.application.subscribe_static_with_selectors(
            subscription_id,
            owner_id,
            descriptor,
            request.selectors.clone(),
        );
        let command_id = request.request_id.clone();
        let selectors = request.selectors.clone();
        match result {
            Err(error) => (
                422,
                json!({
                    "schema_version": 1,
                    "command_id": command_id,
                    "request_id": request.request_id,
                    "status": "rejected",
                    "error": {"code": "market.subscription_rejected", "message": error.to_string(), "retryable": false}
                }),
            ),
            Ok(()) => (
                202,
                json!({
                    "schema_version": 1,
                    "command_id": command_id,
                    "request_id": request.request_id,
                    "instance_id": request.instance_id,
                    "status": "accepted",
                    "subscription_status": self.application.subscription_status(&SubscriptionId::new(request.request_id.clone()).expect("validated subscription id")),
                    "result": {
                        "subscription_id": request.request_id.clone(),
                        "selectors": selectors.clone(),
                    },
                    "subscription_id": request.request_id,
                    "selectors": selectors,
                }),
            ),
        }
    }

    fn unsubscribe(&mut self, body: &str) -> (u16, Value) {
        let value: CommandEnvelope<UnsubscribePayload> = match serde_json::from_str(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid unsubscribe command: {error}"),"retryable":false}}),
                )
            }
        };
        if value.schema_version != 1
            || value.operation != "market.unsubscribe"
            || value.command_id.trim().is_empty()
            || value.idempotency_key.trim().is_empty()
        {
            return (
                422,
                json!({"error":{"code":"command.invalid_envelope","message":"unsupported market command schema or operation","retryable":false}}),
            );
        }
        let request_id = value.command_id;
        let owner_id = strategy_subscription_owner(
            value.launch_id.as_deref(),
            &value.instance_id,
            &value.strategy_id,
        );
        let subscription_id = value.payload.subscription_id;
        let id = match SubscriptionId::new(subscription_id) {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        match self.application.unsubscribe_owned(&id, &owner_id) {
            Ok(true) => (
                202,
                json!({"schema_version":1, "command_id":request_id, "request_id": request_id, "status":"accepted"}),
            ),
            Ok(false) => (
                404,
                json!({"schema_version":1, "command_id":request_id, "request_id": request_id, "status":"rejected", "error":{"code":"market.subscription_not_found","message":"subscription not found","retryable":false}}),
            ),
            Err(error) => (
                409,
                json!({"schema_version":1, "command_id":request_id, "request_id": request_id, "status":"rejected", "error":{"code":"market.subscription_owner_mismatch","message":error.to_string(),"retryable":false}}),
            ),
        }
    }

    fn release_owner(&mut self, body: &str) -> (u16, Value) {
        let value: CommandEnvelope<ReleaseOwnerPayload> = match serde_json::from_str(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid release-owner command: {error}"),"retryable":false}}),
                )
            }
        };
        if value.schema_version != 1
            || value.operation != "market.release_owner"
            || value.command_id.trim().is_empty()
            || value.idempotency_key.trim().is_empty()
            || value.strategy_id.trim().is_empty()
            || value.instance_id.trim().is_empty()
        {
            return (
                422,
                json!({"error":{"code":"command.invalid_envelope","message":"unsupported release-owner command schema or identity","retryable":false}}),
            );
        }
        let owner_id = strategy_subscription_owner(
            value.launch_id.as_deref(),
            &value.instance_id,
            &value.strategy_id,
        );
        let removed = self.application.release_subscription_owner(&owner_id);
        (
            202,
            json!({
                "schema_version": 1,
                "command_id": value.command_id,
                "request_id": value.command_id,
                "status": "accepted",
                "result": {
                    "owner_id": owner_id,
                    "removed_subscription_ids": removed,
                    "removed_count": removed.len(),
                }
            }),
        )
    }
}

fn strategy_subscription_owner(
    launch_id: Option<&str>,
    instance_id: &str,
    strategy_id: &str,
) -> String {
    serde_json::to_string(&json!([
        "strategy",
        launch_id.unwrap_or_default(),
        instance_id,
        strategy_id
    ]))
    .expect("strategy subscription owner is serializable")
}

fn log_event(level: &str, message: &str, fields: Value) {
    match level {
        "error" => error!(component = "market", event = "runtime", fields = %fields, "{message}"),
        "warn" => warn!(component = "market", event = "runtime", fields = %fields, "{message}"),
        _ => info!(component = "market", event = "runtime", fields = %fields, "{message}"),
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::{forward_reference_events, MarketProcess, MarketSnapshotPublisher};
    use crate::domain::observations::{
        Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, OpenInterest, OptionGreeks,
        Ticker24h,
    };
    use crate::services::control::EngineCommand;
    use crate::services::event_wire::{encode_event, encode_observation_event};
    use crate::{MarketApplication, MarketEvent, OrderBook, PriceLevel};
    use kairos_protocol::InstanceIdentity;
    use kairos_reference_contract::ReferenceMarket;
    use serde_json::json;
    use std::collections::VecDeque;
    use std::time::Duration;

    struct NullPublisher;

    impl MarketSnapshotPublisher for NullPublisher {
        fn publish(
            &mut self,
            _snapshot: &crate::domain::snapshot::MarketCurrentView,
        ) -> Result<(), String> {
            Ok(())
        }
    }

    #[test]
    fn bar_and_greeks_have_event_wire_messages() {
        let identity = InstanceIdentity::new("workspace", "launch", "instance");
        let bar = MarketObservation::Bar(Bar {
            market_id: kairos_domain_types::MarketId::new("market:btc").unwrap(),
            instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
            timeframe: "1m".into(),
            open: "1".parse().unwrap(),
            high: "2".parse().unwrap(),
            low: "0.5".parse().unwrap(),
            close: "1.5".parse().unwrap(),
            volume: None,
            observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(1),
            source_id: "binance".into(),
            derivation: "aggregated".into(),
        });
        let greeks = MarketObservation::OptionGreeks(OptionGreeks {
            market_id: kairos_domain_types::MarketId::new("market:btc-option").unwrap(),
            instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc-option").unwrap(),
            expiry_unix_nanos: None,
            strike: None,
            delta: Some("0.5".parse().unwrap()),
            gamma: None,
            vega: None,
            theta: None,
            implied_volatility: None,
            observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(2),
            source_id: "deribit".into(),
            derivation: "direct".into(),
        });
        assert_eq!(
            &encode_observation_event("actor", &identity, 1, &bar).unwrap()[4..8],
            b"MBA1"
        );
        assert_eq!(
            &encode_observation_event("actor", &identity, 2, &greeks).unwrap()[4..8],
            b"MGR1"
        );
    }

    #[test]
    fn orderbook_mutation_has_a_sequence_preserving_event_wire_message() {
        let identity = InstanceIdentity::new("workspace", "launch", "instance");
        let book = OrderBook::snapshot_with_source(
            "binance-spot",
            "market:binance:spot:BTCUSDT",
            "instrument:spot:BTC",
            42,
            100,
            vec![PriceLevel {
                price: "64000".parse().unwrap(),
                quantity: "1.25".parse().unwrap(),
            }],
            vec![PriceLevel {
                price: "64001".parse().unwrap(),
                quantity: "2.5".parse().unwrap(),
            }],
        )
        .unwrap();

        let encoded = encode_event("actor", &identity, 7, &MarketEvent::OrderBook(book)).unwrap();

        assert_eq!(&encoded[4..8], b"MOB1");
        let decoded =
            kairos_protocol::generated::kairos::market::v_1::root_as_order_book_message(&encoded)
                .unwrap();
        assert_eq!(decoded.header().sequence(), 7);
        assert_eq!(decoded.payload().sequence(), 42);
        assert!(decoded.payload().synchronized());
    }

    #[test]
    fn derivative_observations_have_distinct_event_wire_messages() {
        let identity = InstanceIdentity::new("workspace", "launch", "instance");
        let common = (
            kairos_domain_types::MarketId::new("market:btc").unwrap(),
            kairos_domain_types::InstrumentId::new("instrument:btc").unwrap(),
            "source".to_string(),
        );
        let ticker = MarketObservation::Ticker24h(Ticker24h {
            market_id: common.0.clone(),
            instrument_id: common.1.clone(),
            last_price: Some("1".parse().unwrap()),
            bid_price: None,
            bid_quantity: None,
            ask_price: None,
            ask_quantity: None,
            open_price: None,
            high_price: None,
            low_price: None,
            volume_base: None,
            volume_quote: None,
            price_change_abs: None,
            price_change_pct: None,
            vwap: None,
            mark_price: None,
            observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(1),
            source_id: common.2.clone(),
        });
        let mark = MarketObservation::MarkPrice(MarkPrice {
            market_id: common.0.clone(),
            instrument_id: common.1.clone(),
            mark_price: "1".parse().unwrap(),
            index_price: None,
            estimated_settlement_price: None,
            funding_rate: None,
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(2),
            source_id: common.2.clone(),
        });
        let index = MarketObservation::IndexPrice(IndexPrice {
            market_id: common.0.clone(),
            instrument_id: common.1.clone(),
            spot_index_price: Some("1".parse().unwrap()),
            contract_index_price: None,
            index_price: None,
            funding_rate: None,
            observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(3),
            source_id: common.2.clone(),
        });
        let funding = MarketObservation::FundingRate(FundingRate {
            market_id: common.0.clone(),
            instrument_id: common.1.clone(),
            funding_rate: "0.001".parse().unwrap(),
            funding_period_seconds: Some(28_800),
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(4),
            source_id: common.2.clone(),
        });
        let open_interest = MarketObservation::OpenInterest(OpenInterest {
            market_id: common.0,
            instrument_id: common.1,
            contracts: "10".parse().unwrap(),
            quote_value: None,
            change_24h: None,
            change_pct_24h: None,
            observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(5),
            source_id: common.2,
        });
        for (sequence, observation, identifier) in [
            (1, ticker, b"MT24"),
            (2, mark, b"MMP1"),
            (3, index, b"MIP1"),
            (4, funding, b"MFR1"),
            (5, open_interest, b"MOI1"),
        ] {
            assert_eq!(
                &encode_observation_event("actor", &identity, sequence, &observation).unwrap()
                    [4..8],
                identifier
            );
        }
    }

    #[tokio::test]
    async fn command_idempotency_replays_same_result_and_rejects_payload_reuse() {
        let root = tempfile::tempdir().unwrap();
        let mut process = MarketProcess::new(
            MarketApplication::new("test-market", 10).unwrap(),
            NullPublisher,
            root.path().join("market.sock"),
            root.path().join("market.events.sock"),
            Duration::from_millis(10),
        )
        .unwrap();
        let body = serde_json::to_string(&json!({
            "schema_version": 1,
            "command_id": "command-1",
            "idempotency_key": "idem-1",
            "operation": "market.subscribe",
            "strategy_id": "strategy-1",
            "instance_id": "instance-1",
            "payload": {
                "subject": "market.BTCUSDT",
                "selectors": ["quote"],
                "exchange": "binance",
                "market_type": "spot",
                "asset_type": "crypto",
                "params": {"market_id":"market:binance:spot:BTCUSDT","instrument_id":"instrument:binance:spot:BTCUSDT"},
                "dynamic": false
            }
        }))
        .unwrap();
        let first = process
            .actor_task
            .handle_request("POST", "/v1/subscribe", &body)
            .await;
        let second = process
            .actor_task
            .handle_request("POST", "/v1/subscribe", &body)
            .await;
        assert_eq!(first.status, second.status);
        assert_eq!(first.payload, second.payload);
        let conflict = body.replace("BTCUSDT", "ETHUSDT");
        let response = process
            .actor_task
            .handle_request("POST", "/v1/subscribe", &conflict)
            .await;
        assert_eq!(response.status, 409);
    }

    #[tokio::test]
    async fn owner_release_is_scoped_idempotent_and_enforced_by_unsubscribe() {
        let root = tempfile::tempdir().unwrap();
        let mut process = MarketProcess::new(
            MarketApplication::new("test-market", 10).unwrap(),
            NullPublisher,
            root.path().join("market.sock"),
            root.path().join("market.events.sock"),
            Duration::from_millis(10),
        )
        .unwrap();
        let subscribe = |command_id: &str, launch_id: &str, strategy_id: &str| {
            serde_json::to_string(&json!({
                "schema_version": 1,
                "command_id": command_id,
                "idempotency_key": command_id,
                "operation": "market.subscribe",
                "strategy_id": strategy_id,
                "launch_id": launch_id,
                "instance_id": "instance-1",
                "payload": {
                    "subject": "market.BTCUSDT",
                    "selectors": ["quote"],
                    "exchange": "binance",
                    "market_type": "spot",
                    "asset_type": "crypto",
                    "params": {"market_id":"market:binance:spot:BTCUSDT","instrument_id":"instrument:binance:spot:BTCUSDT"},
                    "dynamic": false
                }
            }))
            .unwrap()
        };
        for body in [
            subscribe("subscription-a", "launch-a", "same-name"),
            subscribe("subscription-b", "launch-b", "same-name"),
        ] {
            assert_eq!(
                process
                    .actor_task
                    .handle_request("POST", "/v1/subscribe", &body)
                    .await
                    .status,
                202
            );
        }
        let wrong_owner_unsubscribe = serde_json::to_string(&json!({
            "schema_version": 1,
            "command_id": "wrong-owner-unsubscribe",
            "idempotency_key": "wrong-owner-unsubscribe",
            "operation": "market.unsubscribe",
            "strategy_id": "same-name",
            "launch_id": "launch-b",
            "instance_id": "instance-1",
            "payload": {"subscription_id": "subscription-a"}
        }))
        .unwrap();
        assert_eq!(
            process
                .actor_task
                .handle_request("POST", "/v1/unsubscribe", &wrong_owner_unsubscribe)
                .await
                .status,
            409
        );

        let release = serde_json::to_string(&json!({
            "schema_version": 1,
            "command_id": "release-a",
            "idempotency_key": "release-a",
            "operation": "market.release_owner",
            "strategy_id": "same-name",
            "launch_id": "launch-a",
            "instance_id": "instance-1",
            "payload": {}
        }))
        .unwrap();
        let released = process
            .actor_task
            .handle_request("POST", "/v1/subscriptions/release-owner", &release)
            .await;
        assert_eq!(released.status, 202);
        assert_eq!(released.payload["result"]["removed_count"], 1);
        let subscriptions = process.actor_task.application.snapshot().subscriptions;
        assert_eq!(subscriptions.len(), 1);
        assert_eq!(subscriptions[0].id.0, "subscription-b");

        let repeated = release.replace("release-a", "release-a-again");
        let released = process
            .actor_task
            .handle_request("POST", "/v1/subscriptions/release-owner", &repeated)
            .await;
        assert_eq!(released.status, 202);
        assert_eq!(released.payload["result"]["removed_count"], 0);
    }

    #[tokio::test]
    async fn replay_pause_resume_controls_the_source_without_a_polling_path() {
        let root = tempfile::tempdir().unwrap();
        let mut application = MarketApplication::new("replay-market", 10).unwrap();
        crate::composition::attach_replay_source_with_policy(
            &mut application,
            [MarketObservation::Bar(crate::Bar {
                market_id: kairos_domain_types::MarketId::new("market:test:spot:TEST").unwrap(),
                instrument_id: kairos_domain_types::InstrumentId::new("instrument:test:spot:TEST")
                    .unwrap(),
                timeframe: "1m".into(),
                open: "1".parse().unwrap(),
                high: "1".parse().unwrap(),
                low: "1".parse().unwrap(),
                close: "1".parse().unwrap(),
                volume: None,
                observed_at_unix_nanos: kairos_domain_types::UnixNanos::new(1),
                source_id: "replay".into(),
                derivation: "acceptance".into(),
            })],
            None,
            None,
            root.path().join("checkpoint.json"),
            crate::composition::MarketReplayClock::Maximum,
            1,
            true,
        )
        .unwrap();
        let mut process = MarketProcess::new(
            application,
            NullPublisher,
            root.path().join("market.sock"),
            root.path().join("market.events.sock"),
            Duration::from_millis(10),
        )
        .unwrap();
        process
            .actor_task
            .application
            .drive_next_source_input()
            .await
            .unwrap();
        assert_eq!(
            process
                .actor_task
                .application
                .snapshot()
                .sources
                .values()
                .next()
                .unwrap()
                .status,
            crate::SourceStatus::Paused
        );

        let subscribe = json!({
            "schema_version":1,"command_id":"replay-sub","idempotency_key":"replay-sub",
            "operation":"market.subscribe","strategy_id":"acceptance","instance_id":"instance",
            "payload":{"subject":"market.TEST","selectors":["bar"],"exchange":"test",
            "market_type":"spot","asset_type":"crypto","params":{"market_id":"market:test:spot:TEST","instrument_id":"instrument:test:spot:TEST"},"dynamic":false}
        })
        .to_string();
        assert_eq!(
            process
                .actor_task
                .handle_request("POST", "/v1/subscribe", &subscribe)
                .await
                .status,
            202
        );
        process
            .actor_task
            .application
            .drive_next_source_input()
            .await
            .unwrap();
        assert_eq!(
            process
                .actor_task
                .application
                .snapshot()
                .event_sequence
                .get(),
            0
        );
        assert_eq!(
            process
                .actor_task
                .handle_request("POST", "/v1/replay/resume", "")
                .await
                .status,
            202
        );
        process
            .actor_task
            .application
            .drive_next_source_input()
            .await
            .unwrap();
        process
            .actor_task
            .application
            .drive_next_source_input()
            .await
            .unwrap();
        assert_eq!(
            process
                .actor_task
                .application
                .snapshot()
                .event_sequence
                .get(),
            1
        );
    }

    struct TestReferenceSource {
        events: VecDeque<super::ReferenceEvent>,
    }

    impl super::ReferenceChangeSource for TestReferenceSource {
        fn next_event(&mut self) -> Result<Option<super::ReferenceEvent>, String> {
            Ok(self.events.pop_front())
        }
    }

    #[tokio::test]
    async fn reference_command_queue_pressure_preserves_recovery_signal() {
        let (sender, mut receiver) = tokio::sync::mpsc::channel(1);
        sender
            .try_send(EngineCommand::ReferenceChanged {
                sequence: 1.into(),
                gap: false,
            })
            .unwrap();
        let mut source = Some(Box::new(TestReferenceSource {
            events: [super::ReferenceEvent { sequence: 2.into() }]
                .into_iter()
                .collect(),
        }) as Box<dyn super::ReferenceChangeSource>);
        let mut cursor = None;
        let mut pending = None;

        forward_reference_events(&mut source, &sender, &mut cursor, &mut pending).unwrap();

        assert_eq!(pending, Some((2.into(), true)));
        assert_eq!(cursor, Some(2.into()));
        let _ = receiver.recv().await;
        forward_reference_events(&mut source, &sender, &mut cursor, &mut pending).unwrap();
        assert!(pending.is_none());
        match receiver.recv().await {
            Some(EngineCommand::ReferenceChanged { sequence, gap }) => {
                assert_eq!((sequence, gap), (2.into(), true));
            }
            _ => panic!("reference recovery command was not preserved"),
        }
    }

    #[test]
    fn configured_missing_reference_database_keeps_projection_in_error() {
        let root = tempfile::tempdir().unwrap();
        let mut process = MarketProcess::new(
            MarketApplication::new("test-market", 10).unwrap(),
            NullPublisher,
            root.path().join("market.sock"),
            root.path().join("market.events.sock"),
            Duration::from_millis(10),
        )
        .unwrap()
        .with_reference_database(root.path().join("missing-reference.sqlite"));

        assert!(process.actor_task.recover_reference_projection().is_err());
        assert!(process.actor_task.reference.recovery_needed());
        assert!(process.actor_task.reference.markets().is_none());
    }

    #[test]
    fn reference_database_builds_market_projection_and_clears_recovery() {
        let root = tempfile::tempdir().unwrap();
        let database = root.path().join("reference.sqlite");
        let connection = rusqlite::Connection::open(&database).unwrap();
        connection.execute_batch("CREATE TABLE reference_meta(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, generation INTEGER NOT NULL, event_sequence INTEGER NOT NULL, committed_at_unix_nanos INTEGER NOT NULL); INSERT INTO reference_meta VALUES(1,1,1,1,0); CREATE TABLE reference_markets_current(market_id TEXT PRIMARY KEY, source_id TEXT, market_key TEXT, instrument_id TEXT, listing_id TEXT, exchange_id TEXT, market_type TEXT, asset_type TEXT, underlying_instrument_id TEXT, source_symbol TEXT, status TEXT, effective_to_unix_nanos INTEGER, payload TEXT); CREATE TABLE reference_market_data_accesses_current(access_id TEXT PRIMARY KEY, market_id TEXT, provider_id TEXT, product_family TEXT, provider_symbol TEXT, status TEXT, effective_to_unix_nanos INTEGER, payload TEXT); CREATE TABLE reference_lifecycle(sequence INTEGER PRIMARY KEY, payload TEXT);").unwrap();
        let market = ReferenceMarket {
            source_id: Some("binance-spot".into()),
            market_id: "market:binance:spot:BTCUSDT".into(),
            market_key: "BTCUSDT".into(),
            instrument_id: "instrument:spot:BTC".into(),
            listing_id: "listing:binance:spot:BTC:USDT".into(),
            exchange_id: "exchange:binance".into(),
            market_type: "spot".into(),
            asset_type: Some("crypto".into()),
            source_symbol: "BTCUSDT".into(),
            market_data_access_id: None,
            provider_symbol: None,
            status: "active".into(),
            base_asset_id: None,
            quote_asset_id: None,
            underlying_instrument_id: None,
            price_tick: None,
            quantity_tick: None,
            minimum_quantity: None,
            minimum_notional: None,
            price_precision: 0,
            quantity_precision: 0,
            contract_size: None,
            effective_from_unix_nanos: 0,
            effective_to_unix_nanos: None,
        };
        let payload = serde_json::to_string(&market).unwrap();
        let effective_to = market.effective_to_unix_nanos.map(|value| value as i64);
        connection
            .execute(
                "INSERT INTO reference_markets_current VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rusqlite::params![
                    &market.market_id,
                    &market.source_id,
                    &market.market_key,
                    &market.instrument_id,
                    &market.listing_id,
                    &market.exchange_id,
                    &market.market_type,
                    &market.asset_type,
                    &market.underlying_instrument_id,
                    &market.source_symbol,
                    &market.status,
                    effective_to,
                    payload
                ],
            )
            .unwrap();
        let mut process = MarketProcess::new(
            MarketApplication::new("test-market", 10).unwrap(),
            NullPublisher,
            root.path().join("market.sock"),
            root.path().join("market.events.sock"),
            Duration::from_millis(10),
        )
        .unwrap()
        .with_reference_database(database.clone());

        process.actor_task.recover_reference_projection().unwrap();

        assert!(!process.actor_task.reference.recovery_needed());
        assert_eq!(process.actor_task.reference.markets().unwrap().len(), 1);
        assert_eq!(
            process.actor_task.reference.markets().unwrap()[0].market_id,
            "market:binance:spot:BTCUSDT"
        );
        assert_eq!(
            process.actor_task.reference.event_sequence(),
            Some(1.into())
        );

        process
            .actor_task
            .reference
            .require_recovery(Some(2.into()));
        let error = process
            .actor_task
            .recover_reference_projection()
            .unwrap_err();
        assert!(error.contains("behind required sequence 2"));
        assert!(process.actor_task.reference.recovery_needed());

        connection
            .execute(
                "UPDATE reference_meta SET generation = 2, event_sequence = 2 WHERE id = 1",
                [],
            )
            .unwrap();
        process.actor_task.recover_reference_projection().unwrap();
        assert_eq!(
            process.actor_task.reference.event_sequence(),
            Some(2.into())
        );
        assert!(!process.actor_task.reference.recovery_needed());
    }
}

fn forward_reference_events(
    source: &mut Option<Box<dyn ReferenceChangeSource>>,
    sender: &Sender<EngineCommand>,
    cursor: &mut Option<Sequence>,
    pending: &mut Option<(Sequence, bool)>,
) -> Result<(), String> {
    let Some(source) = source.as_mut() else {
        return Ok(());
    };
    if let Some((sequence, gap)) = pending.take() {
        match sender.try_send(EngineCommand::ReferenceChanged { sequence, gap }) {
            Ok(()) => {}
            Err(TrySendError::Full(_)) => {
                *pending = Some((sequence, true));
                return Ok(());
            }
            Err(TrySendError::Closed(_)) => {
                return Err("reference engine command queue is closed".into())
            }
        }
    }
    let mut latest_sequence: Option<Sequence> = None;
    let mut gap = false;
    while let Some(change) = source.next_event()? {
        if cursor.is_some_and(|previous| change.sequence.get() > previous.get().saturating_add(1)) {
            gap = true;
        }
        *cursor = Some(
            cursor
                .as_ref()
                .copied()
                .unwrap_or_default()
                .max(change.sequence),
        );
        latest_sequence = Some(latest_sequence.unwrap_or_default().max(change.sequence));
    }
    if let Some(sequence) = latest_sequence {
        match sender.try_send(EngineCommand::ReferenceChanged { sequence, gap }) {
            Ok(()) => {}
            Err(TrySendError::Full(_)) => *pending = Some((sequence, true)),
            Err(TrySendError::Closed(_)) => {
                return Err("reference engine command queue is closed".into())
            }
        }
    }
    Ok(())
}

fn rollback_subscribe_intent(application: &mut MarketApplication, body: &str) {
    let Some(command_id) = serde_json::from_str::<Value>(body).ok().and_then(|value| {
        value
            .get("command_id")
            .and_then(Value::as_str)
            .map(str::to_owned)
    }) else {
        return;
    };
    if let Ok(subscription_id) = SubscriptionId::new(command_id) {
        application.unsubscribe(&subscription_id);
    }
}

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}
