//! Market process runtime facade and strategy command boundary.

use crate::application::{
    resolve_market, resolve_option_markets, MarketApplication, ReconcileMarketUniverse,
};
use crate::services::control::wire::{
    command_id, idempotency_key, parse_release_owner_command, parse_subscribe_command,
    parse_unsubscribe_command, strategy_subscription_owner, CommandEnvelope, ReleaseOwnerPayload,
    SubscribePayload, UnsubscribePayload,
};
use crate::services::control::{
    spawn_server as spawn_control_server, EngineCommand, MarketHttpResponse,
};
use crate::services::publication::{EventFanout, EventPublication};
use crate::services::source::SourceActivator;
use crate::SubscriptionId;
use kairos_protocol::InstanceIdentity;
use kairos_workspace::runtime::{HEALTH_PATH, STOP_PATH};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::net::UnixListener;
use tokio::sync::mpsc;
use tokio::sync::mpsc::{Receiver, Sender};
use tokio::time::{self, MissedTickBehavior};
use tracing::{error, info, warn};

const MAX_COMMAND_RESULTS: usize = 4_096;

use super::lifecycle::{now_unix_nanos, remove_socket, MarketProcessSettings};
use super::publication::{MarketChangePublisher, MarketHistoryRecorder};

#[derive(Debug)]
struct SubscribeRequest {
    request_id: String,
    strategy_id: String,
    launch_id: Option<String>,
    instance_id: String,
    subject: String,
    selectors: Vec<crate::ObservationSelector>,
    exchange: Option<String>,
    market_type: Option<String>,
    asset_type: Option<String>,
    params: BTreeMap<String, Value>,
    dynamic: bool,
}

pub struct MarketProcess {
    actor_task: MarketActorTask,
    socket_path: PathBuf,
    event_socket_path: Option<PathBuf>,
    market_universe_updates: Option<Receiver<ReconcileMarketUniverse>>,
    publication_queue_capacity: usize,
    lifecycle_guard: Option<Box<dyn Send>>,
    event_publisher: Option<kairos_transport::AeronBytePublisher>,
}

/// The sole asynchronous task which serializes every MarketActor mutation.
/// This is task/mailbox mechanics around the Actor, not a second business
/// runtime or state owner.
struct MarketActorTask {
    application: MarketApplication,
    publisher: Box<dyn MarketChangePublisher>,
    event_actor_id: String,
    event_identity: InstanceIdentity,
    publication_interval: Duration,
    freshness_check_interval: Duration,
    freshness_max_age: Duration,
    shutdown_timeout: Duration,
    stop_requested: bool,
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
    pub fn new<P: MarketChangePublisher + 'static>(
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

    pub fn new_with_identity<P: MarketChangePublisher + 'static>(
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
                publication_interval: interval,
                freshness_check_interval: interval,
                freshness_max_age: Duration::from_secs(5),
                reference_recovery_interval: interval,
                shutdown_timeout: Duration::from_secs(5),
                publication_queue_capacity: 256,
            },
        )
    }

    pub(crate) fn new_configured<P: MarketChangePublisher + 'static>(
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

    pub(crate) fn new_configured_with_activator<P: MarketChangePublisher + 'static>(
        application: MarketApplication,
        publisher: P,
        socket_path: impl Into<PathBuf>,
        event_socket_path: impl Into<PathBuf>,
        identity: InstanceIdentity,
        settings: MarketProcessSettings,
        source_activator: Option<Box<dyn SourceActivator>>,
    ) -> Result<Self, String> {
        for (name, value) in [
            ("publication interval", settings.publication_interval),
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
        let event_actor_id = application.current_view().actor_id;
        Ok(Self {
            actor_task: MarketActorTask {
                application,
                publisher: Box::new(publisher),
                event_actor_id: event_actor_id.to_string(),
                event_identity: identity,
                publication_interval: settings.publication_interval,
                freshness_check_interval: settings.freshness_check_interval,
                freshness_max_age: settings.freshness_max_age,
                shutdown_timeout: settings.shutdown_timeout,
                stop_requested: false,
                command_results: BTreeMap::new(),
                source_activator,
                history_recorder: MarketHistoryRecorder::Noop,
            },
            socket_path: socket_path.into(),
            event_socket_path: Some(event_socket_path.into()),
            market_universe_updates: None,
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

    pub(crate) fn without_event_socket(mut self) -> Self {
        self.event_socket_path = None;
        self
    }

    pub fn with_market_universe_updates(
        mut self,
        updates: Receiver<ReconcileMarketUniverse>,
    ) -> Self {
        self.market_universe_updates = Some(updates);
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
            mut market_universe_updates,
            publication_queue_capacity,
            lifecycle_guard,
            event_publisher,
        } = self;
        let _lifecycle_guard = lifecycle_guard;
        remove_socket(&socket_path)?;
        if let Some(path) = event_socket_path.as_deref() {
            remove_socket(path)?;
        }
        if let Some(parent) = socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        if let Some(parent) = event_socket_path.as_deref().and_then(Path::parent) {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&socket_path)?;
        let event_listener = match event_socket_path.as_deref() {
            Some(path) => Some(UnixListener::bind(path)?),
            None => None,
        };
        let publication_interval = actor_task.publication_interval;
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
        info!(event = "process_starting", component = "market", socket = %socket_path.display(), event_socket = ?event_socket_path, publication_interval_ms = publication_interval.as_millis(), "market process starting");
        log_event(
            "info",
            "market process ready",
            json!({
                "socket": socket_path,
                "event_socket": event_socket_path,
                "publication_interval_ms": publication_interval.as_millis(),
            }),
        );
        info!(event = "process_ready", component = "market", socket = %socket_path.display(), "market control socket ready");
        let mut event_fanout = EventFanout::new(publication_queue_capacity);
        let event_publisher = event_publisher;
        let actor_result = loop {
            tokio::select! {
                accepted = async {
                    match event_listener.as_ref() {
                        Some(listener) => listener.accept().await.map(Some),
                        None => std::future::pending().await,
                    }
                } => {
                    let Some((stream, _)) = accepted? else { continue };
                    event_fanout.add_client(stream);
                }
                Some(payload) = event_receiver.recv() => {
                    if let Some(publisher) = event_publisher.as_ref() {
                        publisher.publish(&payload)?;
                    }
                    event_fanout.publish(payload);
                }
                update = async {
                    match market_universe_updates.as_mut() {
                        Some(updates) => updates.recv().await,
                        None => std::future::pending().await,
                    }
                } => {
                    match update {
                        Some(update) => actor_sender
                            .send(EngineCommand::ReconcileMarketUniverse(update))
                            .await
                            .map_err(|_| "market engine command queue is closed")?,
                        None => market_universe_updates = None,
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
        if let Some(path) = event_socket_path.as_deref() {
            remove_socket(path)?;
        }
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
        let mut event_publication = EventPublication::new(
            self.event_actor_id.clone(),
            self.event_identity.clone(),
            event_sender,
        );
        let mut freshness_ticks = time::interval(self.freshness_check_interval);
        freshness_ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
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
                                    | "/v1/subscriptions"
                                    | "/v1/unsubscribe"
                                    | "/v1/subscriptions/release-owner"
                            ) || request.path.starts_with("/v1/subscriptions/")
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
                                    if response.status < 300
                                        && matches!(request.path.as_str(), "/v1/subscribe" | "/v1/subscriptions")
                                    {
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
                        EngineCommand::ReconcileMarketUniverse(update) => {
                            self.application
                                .reconcile_market_universe(update)
                                .map_err(|error| error.to_string())?;
                            if let Some(activator) = self.source_activator.as_deref_mut() {
                                self.application
                                    .activate_sources_for_subscriptions(activator)
                                    .await
                                    .map_err(|error| error.to_string())?;
                            }
                            if let Err(error) = self.application.sync_source_subscriptions().await {
                                log_event(
                                    "warn",
                                    "market source reconciliation after market-universe update deferred",
                                    json!({"error": error.to_string()}),
                                );
                            }
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
            }
            self.publish_changes(&mut event_publication).await?;
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
        self.publish_changes(&mut event_publication).await?;
        event_publication.drain(self.shutdown_timeout).await?;
        self.history_recorder.shutdown().await?;
        info!(
            event = "actor_task_stopped",
            component = "market",
            "market actor task stopped"
        );
        Ok(())
    }

    async fn publish_changes(
        &mut self,
        event_publication: &mut EventPublication,
    ) -> Result<(), String> {
        let changes = self.application.drain_changes_limited(1_024);
        let events = changes
            .iter()
            .filter_map(|change| {
                change
                    .event
                    .clone()
                    .map(|event| (change.sequence.get(), event))
            })
            .collect::<Vec<_>>();
        self.history_recorder.record(&events).await?;
        event_publication.publish(events)?;
        for change in changes {
            if change.view.is_some() {
                self.publisher.publish(&change)?;
            }
        }
        Ok(())
    }

    async fn handle_request(&mut self, method: &str, path: &str, body: &str) -> MarketHttpResponse {
        let started = Instant::now();
        log_event(
            "info",
            "market control request",
            json!({"method": method, "path": path}),
        );
        if method == "GET" && path != HEALTH_PATH {
            return MarketHttpResponse {
                status: 405,
                payload: json!({"error":"Market business queries are available only through typed mmap views"}),
            };
        }
        if path == HEALTH_PATH && method != "GET" {
            return MarketHttpResponse {
                status: 405,
                payload: json!({"error":"/v1/health only accepts GET"}),
            };
        }
        let command_key = if matches!(
            path,
            "/v1/subscribe"
                | "/v1/subscriptions"
                | "/v1/unsubscribe"
                | "/v1/subscriptions/release-owner"
        ) {
            idempotency_key(body)
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
            "/v1/subscribe" => self.subscribe(body),
            "/v1/subscriptions" => {
                let (status, payload) = self.subscribe(body);
                if status == 202 {
                    let subscription_id = payload
                        .get("subscription_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    let owner_id = self
                        .application
                        .current_view()
                        .subscriptions
                        .into_iter()
                        .find(|item| item.id.0 == subscription_id)
                        .map(|item| item.owner_id)
                        .unwrap_or_default();
                    (
                        201,
                        json!({
                            "subscription_id": subscription_id,
                            "owner_id": owner_id,
                            "status": payload.get("subscription_status").cloned().unwrap_or(json!("pending")),
                            "members": [],
                            "updated_at_unix_nanos": now_unix_nanos(),
                        }),
                    )
                } else {
                    (status, payload)
                }
            }
            path if path.starts_with("/v1/subscriptions/") && method == "DELETE" => {
                let (status, payload) = self.unsubscribe(body);
                if status == 202 {
                    (204, Value::Null)
                } else {
                    (status, payload)
                }
            }
            "/v1/unsubscribe" => self.unsubscribe(body),
            "/v1/subscriptions/release-owner" => self.release_owner(body),
            "/v1/recover" | "/v1/recovery" => match self.application.recover_sources().await {
                Ok(()) => (
                    202,
                    json!({
                        "command_id": command_id(body).map(Value::String).unwrap_or(Value::Null),
                        "status": "accepted",
                        "operation": "market.recovery"
                    }),
                ),
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
        let Some(key) = idempotency_key(body) else {
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
        let snapshot = self.application.current_view();
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
            "dependencies": { "feed_status": snapshot.feed_status },
        })
    }

    fn subscribe(&mut self, body: &str) -> (u16, Value) {
        let value: CommandEnvelope<SubscribePayload> = match parse_subscribe_command(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid subscribe command: {error}"),"retryable":false}}),
                )
            }
        };
        if !matches!(value.schema_version, 1 | 2)
            || value.operation != "market.subscribe"
            || value.command_id.trim().is_empty()
            || value.idempotency_key.trim().is_empty()
        {
            return (
                422,
                json!({"error":{"code":"command.invalid_envelope","message":"unsupported market command schema or operation","retryable":false}}),
            );
        }
        let selectors = match value
            .payload
            .selectors
            .iter()
            .map(|value| crate::ObservationSelector::parse(value))
            .collect::<Result<Vec<_>, _>>()
        {
            Ok(selectors) => selectors,
            Err(error) => return (422, json!({"error": error})),
        };
        let request = SubscribeRequest {
            request_id: value.command_id,
            strategy_id: value.strategy_id,
            launch_id: value.launch_id,
            instance_id: value.instance_id,
            subject: value.payload.subject,
            selectors,
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
            let market_universe = self.application.market_universe();
            if market_universe.is_empty() {
                return (422, json!({"error":"market universe is not ready"}));
            }
            if market_type != "options" {
                return (
                    422,
                    json!({"error":"chain subscription requires market_type=options"}),
                );
            }
            let markets = match resolve_option_markets(
                &market_universe,
                &exchange,
                request.asset_type.as_deref(),
                &underlying,
            ) {
                Ok(value) if !value.is_empty() => value,
                Ok(_) => {
                    return (
                        422,
                        json!({"error":format!("market universe has no active option markets for {underlying}")}),
                    )
                }
                Err(error) => return (422, json!({"error": error})),
            };
            let exchange_id = match kairos_primitives::Exchange::new(exchange.clone()) {
                Ok(value) => value,
                Err(error) => return (422, json!({"error": error.to_string()})),
            };
            let asset_type = match request
                .asset_type
                .as_deref()
                .map(str::parse::<kairos_primitives::AssetClass>)
                .transpose()
            {
                Ok(value) => value,
                Err(error) => return (422, json!({"error": error.to_string()})),
            };
            let query = crate::MarketSelectionQuery {
                exchange_id: Some(exchange_id),
                provider_product: Some(
                    kairos_primitives::ProviderProductCode::new(market_type)
                        .expect("validated Reference market type"),
                ),
                asset_type,
                underlying_instrument_id: markets[0].underlying_instrument_id.clone(),
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
        let market_universe = self.application.market_universe();
        let descriptor_result = match market_universe.as_slice() {
            [_first, ..] => resolve_market(
                &market_universe,
                &exchange,
                &market_type,
                request.asset_type.as_deref(),
                &source_symbol,
            ),
            _ if self.application.has_replay_source() => {
                let market_id = request
                    .params
                    .get("market_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "replay subscription requires params.market_id".to_string());
                let instrument_id = request
                    .params
                    .get("instrument_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "replay subscription requires params.instrument_id".to_string());
                market_id.and_then(|market_id| {
                    instrument_id.and_then(|instrument_id| {
                        let instrument_kind = match market_type.as_str() {
                            "equity" => kairos_primitives::InstrumentKind::Equity,
                            "spot" => kairos_primitives::InstrumentKind::Spot,
                            "perpetual" | "swap" => kairos_primitives::InstrumentKind::Perpetual,
                            "future" | "futures" => kairos_primitives::InstrumentKind::Future,
                            "option" | "options" => kairos_primitives::InstrumentKind::Option,
                            "index" => kairos_primitives::InstrumentKind::Index,
                            _ => {
                                return Err(format!("unsupported replay market type {market_type}"))
                            }
                        };
                        let route = crate::MarketDataRoute::new(
                            format!("replay:{market_id}"),
                            "replay",
                            market_type.clone(),
                            source_symbol.clone(),
                        )?;
                        let mut descriptor = crate::ResolvedMarket::new(
                            market_id,
                            instrument_id,
                            instrument_kind,
                            exchange.clone(),
                            route,
                        )?;
                        descriptor.asset_type = request
                            .asset_type
                            .as_deref()
                            .map(str::parse::<kairos_primitives::AssetClass>)
                            .transpose()
                            .map_err(|error| error.to_string())?;
                        descriptor.with_source("replay")
                    })
                })
            }
            _ => {
                Err("market universe is not ready; explicit market-data access is required".into())
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
        let value: CommandEnvelope<UnsubscribePayload> = match parse_unsubscribe_command(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid unsubscribe command: {error}"),"retryable":false}}),
                )
            }
        };
        if !matches!(value.schema_version, 1 | 2)
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
        let value: CommandEnvelope<ReleaseOwnerPayload> = match parse_release_owner_command(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid release-owner command: {error}"),"retryable":false}}),
                )
            }
        };
        if !matches!(value.schema_version, 1 | 2)
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
            200,
            json!({
                "command_id": value.command_id,
                "status": "completed",
                "removed_subscription_ids": removed,
            }),
        )
    }
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
    use super::{MarketChangePublisher, MarketProcess};
    use crate::domain::observation::{
        Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, OpenInterest, OptionGreeks,
        Ticker24h,
    };
    use crate::services::publication::encode_event;
    use crate::{MarketApplication, MarketEvent, OrderBook, PriceLevel};
    use kairos_protocol::InstanceIdentity;
    use serde_json::json;
    use std::time::Duration;

    struct NullPublisher;

    impl MarketChangePublisher for NullPublisher {
        fn publish(&mut self, _change: &crate::domain::events::MarketChange) -> Result<(), String> {
            Ok(())
        }
    }

    #[test]
    fn bar_and_greeks_have_event_wire_messages() {
        let identity = InstanceIdentity::new("workspace", "launch", "instance");
        let bar = MarketObservation::Bar(Bar {
            market_id: kairos_primitives::MarketId::new("market:btc").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("instrument:btc").unwrap(),
            timeframe: "1m".into(),
            open: "1".parse().unwrap(),
            high: "2".parse().unwrap(),
            low: "0.5".parse().unwrap(),
            close: "1.5".parse().unwrap(),
            volume: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(1),
            source_id: "binance".into(),
            derivation: "aggregated".into(),
        });
        let greeks = MarketObservation::OptionGreeks(OptionGreeks {
            market_id: kairos_primitives::MarketId::new("market:btc-option").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("instrument:btc-option").unwrap(),
            expiry_unix_nanos: None,
            strike: None,
            delta: Some("0.5".parse().unwrap()),
            gamma: None,
            vega: None,
            theta: None,
            implied_volatility: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(2),
            source_id: "deribit".into(),
            derivation: "direct".into(),
        });
        assert_eq!(
            &encode_event("actor", &identity, 1, &MarketEvent::Observation(bar)).unwrap()[4..8],
            b"MBV2"
        );
        assert_eq!(
            &encode_event("actor", &identity, 2, &MarketEvent::Observation(greeks)).unwrap()[4..8],
            b"MGU2"
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

        let encoded =
            encode_event("actor", &identity, 7, &MarketEvent::OrderBookSnapshot(book)).unwrap();

        assert_eq!(&encoded[4..8], b"MOS2");
        let decoded = kairos_market_contract::event::decode_event(&encoded).unwrap();
        let kairos_market_contract::event::MarketEvent::OrderBookSnapshotReceived(decoded) =
            decoded
        else {
            panic!("wrong v2 event root")
        };
        assert_eq!(decoded.metadata().sequence(), 7);
        assert_eq!(decoded.snapshot().sequence(), 42);
    }

    #[test]
    fn derivative_observations_have_distinct_event_wire_messages() {
        let identity = InstanceIdentity::new("workspace", "launch", "instance");
        let common = (
            kairos_primitives::MarketId::new("market:btc").unwrap(),
            kairos_primitives::InstrumentId::new("instrument:btc").unwrap(),
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
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(1),
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
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(2),
            source_id: common.2.clone(),
        });
        let index = MarketObservation::IndexPrice(IndexPrice {
            market_id: common.0.clone(),
            instrument_id: common.1.clone(),
            spot_index_price: Some("1".parse().unwrap()),
            contract_index_price: None,
            index_price: None,
            funding_rate: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(3),
            source_id: common.2.clone(),
        });
        let funding = MarketObservation::FundingRate(FundingRate {
            market_id: common.0.clone(),
            instrument_id: common.1.clone(),
            funding_rate: "0.001".parse().unwrap(),
            funding_period_seconds: Some(28_800),
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(4),
            source_id: common.2.clone(),
        });
        let open_interest = MarketObservation::OpenInterest(OpenInterest {
            market_id: common.0,
            instrument_id: common.1,
            contracts: "10".parse().unwrap(),
            quote_value: None,
            change_24h: None,
            change_pct_24h: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(5),
            source_id: common.2,
        });
        for (sequence, observation, identifier) in [
            (1, ticker, b"MTU2"),
            (2, mark, b"MMP2"),
            (3, index, b"MIP2"),
            (4, funding, b"MFR2"),
            (5, open_interest, b"MOI2"),
        ] {
            assert_eq!(
                &encode_event(
                    "actor",
                    &identity,
                    sequence,
                    &MarketEvent::Observation(observation)
                )
                .unwrap()[4..8],
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
        let mut application = MarketApplication::new("test-market", 10).unwrap();
        crate::composition::attach_replay_source_with_policy(
            &mut application,
            std::iter::empty::<MarketObservation>(),
            None,
            None,
            root.path().join("owner-checkpoint.json"),
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
        assert_eq!(released.status, 200);
        assert_eq!(
            released.payload["removed_subscription_ids"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        let subscriptions = process.actor_task.application.current_view().subscriptions;
        assert_eq!(subscriptions.len(), 1);
        assert_eq!(subscriptions[0].id.0, "subscription-b");

        let repeated = release.replace("release-a", "release-a-again");
        let released = process
            .actor_task
            .handle_request("POST", "/v1/subscriptions/release-owner", &repeated)
            .await;
        assert_eq!(released.status, 200);
        assert_eq!(
            released.payload["removed_subscription_ids"]
                .as_array()
                .unwrap()
                .len(),
            0
        );
    }

    #[tokio::test]
    async fn replay_pause_resume_controls_the_source_without_a_polling_path() {
        let root = tempfile::tempdir().unwrap();
        let mut application = MarketApplication::new("replay-market", 10).unwrap();
        crate::composition::attach_replay_source_with_policy(
            &mut application,
            [MarketObservation::Bar(crate::Bar {
                market_id: kairos_primitives::MarketId::new("market:test:spot:TEST").unwrap(),
                instrument_id: kairos_primitives::InstrumentId::new("instrument:test:spot:TEST")
                    .unwrap(),
                timeframe: "1m".into(),
                open: "1".parse().unwrap(),
                high: "1".parse().unwrap(),
                low: "1".parse().unwrap(),
                close: "1".parse().unwrap(),
                volume: None,
                observed_at_unix_nanos: kairos_primitives::UnixNanos::new(1),
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
                .current_view()
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
        assert_eq!(process.actor_task.application.event_sequence(), 0);
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
        assert_eq!(process.actor_task.application.event_sequence(), 1);
    }
}

fn rollback_subscribe_intent(application: &mut MarketApplication, body: &str) {
    let Some(command_id) = command_id(body) else {
        return;
    };
    if let Ok(subscription_id) = SubscriptionId::new(command_id) {
        application.unsubscribe(&subscription_id);
    }
}
