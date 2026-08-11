//! Market process runtime and strategy command boundary.

use crate::application::MarketRuntime;
use crate::domain::observations::MarketObservation;
use crate::domain::snapshot::MarketSnapshot;
use crate::services::reference::{resolve_market, resolve_option_markets};
use crate::{MarketDescriptor, SubscriptionId};
use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::{Method, StatusCode},
    response::{IntoResponse, Response},
    routing::any,
    Json, Router,
};
use flatbuffers::FlatBufferBuilder;
use kairos_domain_types::Sequence;
use kairos_protocol::generated::kairos::common::v_1::{
    Decimal64, MessageHeader, MessageHeaderArgs,
};
use kairos_protocol::generated::kairos::market::v_1::{
    finish_bar_message_buffer, finish_funding_rate_message_buffer, finish_greeks_message_buffer,
    finish_index_price_message_buffer, finish_instrument_status_message_buffer,
    finish_mark_price_message_buffer, finish_open_interest_message_buffer,
    finish_quote_message_buffer, finish_rate_message_buffer, finish_ticker_24h_message_buffer,
    finish_trade_message_buffer, Bar as FbBar, BarArgs as FbBarArgs, BarMessage, BarMessageArgs,
    FundingRate as FbFundingRate, FundingRateArgs as FbFundingRateArgs, FundingRateMessage,
    FundingRateMessageArgs, Greeks as FbGreeks, GreeksArgs as FbGreeksArgs, GreeksMessage,
    GreeksMessageArgs, IndexPrice as FbIndexPrice, IndexPriceArgs as FbIndexPriceArgs,
    IndexPriceMessage, IndexPriceMessageArgs, InstrumentStatus as FbInstrumentStatus,
    InstrumentStatusArgs as FbInstrumentStatusArgs, InstrumentStatusMessage,
    InstrumentStatusMessageArgs, MarkPrice as FbMarkPrice, MarkPriceArgs as FbMarkPriceArgs,
    MarkPriceMessage, MarkPriceMessageArgs, OpenInterest as FbOpenInterest,
    OpenInterestArgs as FbOpenInterestArgs, OpenInterestMessage, OpenInterestMessageArgs,
    Quote as FbQuote, QuoteArgs as FbQuoteArgs, QuoteMessage, QuoteMessageArgs, Rate as FbRate,
    RateArgs as FbRateArgs, RateMessage, RateMessageArgs, Ticker24h as FbTicker24h,
    Ticker24hArgs as FbTicker24hArgs, Ticker24hMessage, Ticker24hMessageArgs, Trade as FbTrade,
    TradeArgs as FbTradeArgs, TradeMessage, TradeMessageArgs,
};
use kairos_protocol::InstanceIdentity;
use kairos_workspace::runtime::{HEALTH_PATH, SNAPSHOT_PATH, STOP_PATH};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::AsyncWriteExt;
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::mpsc::error::TrySendError;
use tokio::sync::mpsc::{Receiver, Sender};
use tokio::sync::{mpsc, oneshot};
use tokio::time::{self, MissedTickBehavior};
use tracing::{error, info, warn, Instrument};

const MAX_PENDING_ENCODED_EVENTS: usize = 8_192;
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
    instance_id: String,
    payload: T,
}

#[derive(Debug, Deserialize)]
struct SubscribeRequest {
    request_id: String,
    strategy_id: String,
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

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceEvent {
    pub sequence: Sequence,
}

pub trait ReferenceChangeSource {
    fn next_event(&mut self) -> Result<Option<ReferenceEvent>, String>;
}

pub struct MarketProcess {
    engine: MarketEngine,
    socket_path: PathBuf,
    event_socket_path: PathBuf,
    reference_events: Option<Box<dyn ReferenceChangeSource>>,
    reference_event_sequence: Option<Sequence>,
}

struct MarketHttpRequest {
    method: Method,
    path: String,
    body: Vec<u8>,
    response: oneshot::Sender<MarketHttpResponse>,
}

struct MarketHttpResponse {
    status: StatusCode,
    payload: Value,
}

/// Application-owned publication capability. Concrete storage and wire
/// encoding are selected by composition and never cross this boundary.
pub trait MarketSnapshotPublisher: Send {
    fn publish(&mut self, snapshot: &MarketSnapshot) -> Result<(), String>;
}

enum EngineCommand {
    Http(MarketHttpRequest),
    ReferenceChanged { sequence: Sequence, gap: bool },
}

struct MarketEngine {
    application: MarketRuntime,
    publisher: Box<dyn MarketSnapshotPublisher>,
    event_actor_id: String,
    event_identity: InstanceIdentity,
    interval: Duration,
    feed_enabled: bool,
    stop_requested: bool,
    reference_snapshot_path: Option<PathBuf>,
    reference_markets: Option<Vec<MarketDescriptor>>,
    reference_event_sequence: Option<Sequence>,
    reference_recovery_needed: bool,
    command_results: BTreeMap<String, CachedCommandResult>,
}

#[derive(Clone)]
struct CachedCommandResult {
    request_body: String,
    status: u16,
    payload: Value,
}

impl MarketProcess {
    pub fn new<P: MarketSnapshotPublisher + 'static>(
        application: MarketRuntime,
        publisher: P,
        socket_path: impl Into<PathBuf>,
        event_socket_path: impl Into<PathBuf>,
        interval: Duration,
        feed_enabled: bool,
    ) -> Result<Self, String> {
        Self::new_with_identity(
            application,
            publisher,
            socket_path,
            event_socket_path,
            interval,
            feed_enabled,
            InstanceIdentity::default(),
        )
    }

    pub fn new_with_identity<P: MarketSnapshotPublisher + 'static>(
        application: MarketRuntime,
        publisher: P,
        socket_path: impl Into<PathBuf>,
        event_socket_path: impl Into<PathBuf>,
        interval: Duration,
        feed_enabled: bool,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        if interval.is_zero() {
            return Err("market process interval must be positive".into());
        }
        let event_actor_id = application.snapshot().actor_id;
        Ok(Self {
            engine: MarketEngine {
                application,
                publisher: Box::new(publisher),
                event_actor_id: event_actor_id.to_string(),
                event_identity: identity,
                interval,
                feed_enabled,
                stop_requested: false,
                reference_snapshot_path: None,
                reference_markets: None,
                reference_event_sequence: None,
                reference_recovery_needed: false,
                command_results: BTreeMap::new(),
            },
            socket_path: socket_path.into(),
            event_socket_path: event_socket_path.into(),
            reference_events: None,
            reference_event_sequence: None,
        })
    }

    pub fn with_reference_snapshot(mut self, path: impl Into<PathBuf>) -> Self {
        self.engine.reference_snapshot_path = Some(path.into());
        self.engine.reference_recovery_needed = true;
        self
    }

    pub fn with_reference_events<S: ReferenceChangeSource + 'static>(mut self, source: S) -> Self {
        self.reference_events = Some(Box::new(source));
        self.engine.reference_recovery_needed = true;
        self
    }

    pub async fn run(self) -> Result<(), Box<dyn std::error::Error>> {
        let MarketProcess {
            engine,
            socket_path,
            event_socket_path,
            mut reference_events,
            mut reference_event_sequence,
        } = self;
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
        let feed_enabled = engine.feed_enabled;
        let interval = engine.interval;
        let (http_sender, control_receiver) = mpsc::channel(1_024);
        let (event_sender, mut event_receiver) = mpsc::channel(4_096);
        let engine_sender = http_sender.clone();
        let router = Router::new()
            .route(HEALTH_PATH, any(market_http_handler))
            .route(SNAPSHOT_PATH, any(market_http_handler))
            .route(STOP_PATH, any(market_http_handler))
            .route("/v1/subscribe", any(market_http_handler))
            .route("/v1/unsubscribe", any(market_http_handler))
            .route("/v1/recover", any(market_http_handler))
            .with_state(http_sender);
        let http_server = tokio::spawn(async move {
            axum::serve(listener, router)
                .await
                .map_err(std::io::Error::other)
        });
        let mut engine =
            tokio::spawn(async move { engine.run_engine(control_receiver, event_sender).await });
        kairos_workspace::logging::record_gauge("kairos.process.ready", 1);
        info!(event = "process_starting", component = "market", socket = %socket_path.display(), event_socket = %event_socket_path.display(), feed_enabled, interval_ms = interval.as_millis(), "market process starting");
        log_event(
            "info",
            "market process ready",
            json!({
                "socket": socket_path,
                "event_socket": event_socket_path,
                "feed_enabled": feed_enabled,
                "poll_interval_ms": interval.as_millis(),
            }),
        );
        info!(event = "process_ready", component = "market", socket = %socket_path.display(), "market control socket ready");
        let mut event_clients: Vec<Sender<Vec<u8>>> = Vec::new();
        let mut pending_reference_command = None;
        let mut reference_ticks = time::interval(interval);
        reference_ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        let engine_result = loop {
            tokio::select! {
                accepted = event_listener.accept() => {
                    let (stream, _) = accepted?;
                    let (sender, receiver) = mpsc::channel(256);
                    tokio::spawn(event_client_writer(stream, receiver));
                    event_clients.push(sender);
                }
                Some(payload) = event_receiver.recv() => {
                    // Client writes happen in independent tasks. A slow
                    // client is removed when its bounded queue is full and
                    // cannot delay control, ingest, or other clients.
                    event_clients.retain(|client| match client.try_send(payload.clone()) {
                        Ok(()) => true,
                        Err(TrySendError::Full(_)) | Err(TrySendError::Closed(_)) => {
                            kairos_workspace::logging::record_counter(
                                "kairos.queue.rejected",
                                1,
                            );
                            false
                        }
                    });
                }
                _ = reference_ticks.tick(), if reference_events.is_some() => {
                    if let Err(error) = forward_reference_events(
                        &mut reference_events,
                        &engine_sender,
                        &mut reference_event_sequence,
                        &mut pending_reference_command,
                    ) {
                        kairos_workspace::logging::record_counter("kairos.retry", 1);
                        log_event("warn", "reference event forwarding deferred", json!({"error": error}));
                    }
                }
                result = &mut engine => {
                    break result;
                }
            }
        };
        remove_socket(&socket_path)?;
        remove_socket(&event_socket_path)?;
        info!(
            event = "process_stopped",
            component = "market",
            "market process stopped"
        );
        http_server.abort();
        let _ = http_server.await;
        engine_result
            .map_err(|error| std::io::Error::other(error.to_string()))?
            .map_err(std::io::Error::other)?;
        Ok(())
    }
}

impl MarketEngine {
    async fn run_engine(
        mut self,
        mut control_receiver: Receiver<EngineCommand>,
        event_sender: Sender<Vec<u8>>,
    ) -> Result<(), String> {
        info!(
            event = "engine_starting",
            component = "market",
            "market application engine starting"
        );
        self.recover_reference_projection()?;
        self.publish_snapshot()?;
        let mut pending_encoded_events = VecDeque::new();
        let mut ticks = time::interval(self.interval);
        ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        while !self.stop_requested {
            tokio::select! {
                Some(command) = control_receiver.recv() => {
                    match command {
                        EngineCommand::Http(request) => {
                            let body = String::from_utf8_lossy(&request.body);
                            let response = self.handle_request(&request.method, &request.path, &body);
                            let _ = request.response.send(response);
                        }
                        EngineCommand::ReferenceChanged { sequence, gap } => {
                            if gap {
                                kairos_workspace::logging::record_counter("kairos.event.gap", 1);
                                self.reference_recovery_needed = true;
                            }
                            self.reference_event_sequence = Some(
                                self.reference_event_sequence
                                    .unwrap_or_default()
                                    .max(sequence),
                            );
                            self.reference_recovery_needed = true;
                        }
                    }
                }
                _ = ticks.tick() => {
                    let tick_started = Instant::now();
                    if let Err(error) = self.recover_reference_projection() {
                        log_event("error", "reference projection recovery failed", json!({"error": error}));
                    }
                    if self.feed_enabled {
                        if let Err(error) = self.application.reconcile_feed() {
                            kairos_workspace::logging::record_counter("kairos.retry", 1);
                            log_event(
                                "warn",
                                "market feed reconciliation deferred",
                                json!({"error": error.to_string()}),
                            );
                        }
                    }
                    if self.feed_enabled && !self.application.snapshot().subscriptions.is_empty() {
                        let poll_started = Instant::now();
                        let result = self.application.poll_feed().map_err(|error| error.to_string());
                        match result {
                            Ok(observation_count) => log_event(
                                "info",
                                "market feed poll completed",
                                json!({
                                    "observations": observation_count,
                                    "duration_ms": poll_started.elapsed().as_millis(),
                                    "feed_status": self.application.snapshot().feed_status,
                                }),
                            ),
                            Err(error) => {
                                log_event("error", "market feed poll failed", json!({"error": error}));
                            }
                        }
                    }
                    self.publish_pending_events(&mut pending_encoded_events, &event_sender)?;
                    self.publish_snapshot()?;
                    if self.application.feed_complete() && pending_encoded_events.is_empty() {
                        self.stop_requested = true;
                    }
                    let elapsed = tick_started.elapsed();
                    if elapsed > self.interval {
                        log_event("warn", "market tick exceeded interval", json!({
                            "duration_ms": elapsed.as_millis(),
                            "interval_ms": self.interval.as_millis(),
                        }));
                    }
                }
            }
        }
        info!(
            event = "engine_stopped",
            component = "market",
            "market application engine stopped"
        );
        Ok(())
    }

    fn publish_snapshot(&mut self) -> Result<(), String> {
        let snapshot = self.application.snapshot();
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
        if !self.reference_recovery_needed {
            return Ok(());
        }
        let path = self
            .reference_snapshot_path
            .as_ref()
            .ok_or_else(|| "Reference snapshot is not configured".to_string())?;
        // A live provider can serve explicit static subscriptions without a
        // Reference process. Treat the absent optional projection as an empty
        // reference view; dynamic subscriptions still require a snapshot or
        // a later Reference change to populate it.
        if !path.exists() {
            self.reference_markets = Some(Vec::new());
            self.reference_recovery_needed = false;
            return Ok(());
        }
        let snapshot = kairos_reference_contract::ReferenceMmapMarketsReader::open(
            path,
            "reference",
            "reference.lifecycle",
        )
        .map_err(|error| error.to_string())?
        .read()
        .map_err(|error| error.to_string())?;
        let markets = snapshot
            .markets
            .into_iter()
            .filter(|market| matches!(market.status.as_str(), "active" | "trading"))
            .map(|market| {
                let mut descriptor = MarketDescriptor::new(
                    market.market_id,
                    market.instrument_id,
                    market.exchange_id,
                    market.market_type,
                    market.source_symbol,
                )?;
                descriptor.asset_type = market.asset_type;
                descriptor.underlying_instrument_id = market.underlying_instrument_id;
                Ok(descriptor)
            })
            .collect::<Result<Vec<_>, String>>()?;
        self.reference_markets = Some(markets.clone());
        self.application
            .reconcile_reference(markets)
            .map_err(|error| error.to_string())?;
        self.reference_recovery_needed = false;
        Ok(())
    }

    fn publish_pending_events(
        &mut self,
        pending_encoded_events: &mut VecDeque<Vec<u8>>,
        event_sender: &Sender<Vec<u8>>,
    ) -> Result<(), String> {
        if pending_encoded_events.len() >= MAX_PENDING_ENCODED_EVENTS {
            return Err(format!(
                "market encoded event backlog exceeded limit: {MAX_PENDING_ENCODED_EVENTS}"
            ));
        }
        for (sequence, observation) in self.application.drain_events_limited(1_024) {
            pending_encoded_events.push_back(encode_event(
                &self.event_actor_id,
                &self.event_identity,
                sequence,
                &observation,
            )?);
        }
        while let Some(payload) = pending_encoded_events.pop_front() {
            match event_sender.try_send(payload) {
                Ok(()) => {}
                Err(TrySendError::Full(payload)) => {
                    pending_encoded_events.push_front(payload);
                    break;
                }
                Err(TrySendError::Closed(_)) => {
                    return Err("market event endpoint is closed".to_string());
                }
            }
        }
        Ok(())
    }

    fn handle_request(&mut self, method: &Method, path: &str, body: &str) -> MarketHttpResponse {
        let started = Instant::now();
        log_event(
            "info",
            "market control request",
            json!({"method": method.as_str(), "path": path}),
        );
        let command_key = if matches!(path, "/v1/subscribe" | "/v1/unsubscribe") {
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
                        status: StatusCode::from_u16(cached.status)
                            .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
                        payload: cached.payload,
                    };
                }
                return MarketHttpResponse {
                    status: StatusCode::CONFLICT,
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
            "/v1/recover" => match self.application.recover_feed() {
                Ok(()) => (202, json!({"status":"recovering"})),
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
        MarketHttpResponse {
            status: StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            payload,
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
            let Some(reference_markets) = self.reference_markets.as_deref() else {
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
            let result = self.application.subscribe_dynamic_with_selectors(
                subscription_id.clone(),
                request.strategy_id.clone(),
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
        let descriptor_result = match self.reference_markets.as_deref() {
            Some(reference_markets) if !reference_markets.is_empty() => resolve_market(
                reference_markets,
                &exchange,
                &market_type,
                request.asset_type.as_deref(),
                &source_symbol,
            ),
            // Explicit static subscriptions are valid for public feeds even
            // when the optional Reference projection is not running. Keep
            // Reference validation when a catalog is available; this
            // fallback is intentionally not used for dynamic subscriptions.
            _ => {
                let market_id = format!("market:{exchange}:{market_type}:{source_symbol}");
                let instrument_id = format!("instrument:{exchange}:{market_type}:{source_symbol}");
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
        let result = self.application.subscribe_static_with_selectors(
            subscription_id,
            request.strategy_id,
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
        let subscription_id = value.payload.subscription_id;
        let id = match SubscriptionId::new(subscription_id) {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        if self.application.unsubscribe(&id) {
            (
                202,
                json!({"schema_version":1, "command_id":request_id, "request_id": request_id, "status":"accepted"}),
            )
        } else {
            (
                404,
                json!({"schema_version":1, "command_id":request_id, "request_id": request_id, "status":"rejected", "error":{"code":"market.subscription_not_found","message":"subscription not found","retryable":false}}),
            )
        }
    }
}

macro_rules! finish_market_event {
    ($builder:expr, $header:expr, $payload:expr, $message:ident, $args:ident, $finish:ident) => {{
        let root = $message::create(
            $builder,
            &$args {
                header: Some($header),
                payload: Some($payload),
            },
        );
        $finish($builder, root);
        Ok($builder.finished_data().to_vec())
    }};
}

fn encode_event(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    observation: &MarketObservation,
) -> Result<Vec<u8>, String> {
    let stream_id = "market.events";
    match observation {
        MarketObservation::Quote(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let instrument_id = builder.create_string(&value.instrument_id);
            let market_id = builder.create_string(&value.market_id);
            let source_id = builder.create_string(&value.source_id);
            let bid_price = value.bid_price.map(decimal64_price);
            let bid_quantity = value.bid_quantity.map(decimal64_quantity);
            let ask_price = value.ask_price.map(decimal64_price);
            let ask_quantity = value.ask_quantity.map(decimal64_quantity);
            let quote = FbQuote::create(
                &mut builder,
                &FbQuoteArgs {
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    bid_price: bid_price.as_ref(),
                    bid_quantity: bid_quantity.as_ref(),
                    ask_price: ask_price.as_ref(),
                    ask_quantity: ask_quantity.as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                    ..Default::default()
                },
            );
            let root = QuoteMessage::create(
                &mut builder,
                &QuoteMessageArgs {
                    header: Some(header),
                    payload: Some(quote),
                },
            );
            finish_quote_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::Trade(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let instrument_id = builder.create_string(&value.instrument_id);
            let market_id = builder.create_string(&value.market_id);
            let source_id = builder.create_string(&value.source_id);
            let price = decimal64_price(value.price);
            let quantity = decimal64_quantity(value.quantity);
            let cost = value.cost.map(decimal64_money);
            let trade_id = value.trade_id.as_ref().map(|id| builder.create_string(id));
            let trade = FbTrade::create(
                &mut builder,
                &FbTradeArgs {
                    trade_id,
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    price: Some(&price),
                    quantity: Some(&quantity),
                    cost: cost.as_ref(),
                    aggressor_side: aggressor_side(value.aggressor_side.as_deref()),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            let root = TradeMessage::create(
                &mut builder,
                &TradeMessageArgs {
                    header: Some(header),
                    payload: Some(trade),
                },
            );
            finish_trade_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::Bar(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let timeframe = builder.create_string(&value.timeframe);
            let source_id = builder.create_string(&value.source_id);
            let derivation = builder.create_string(&value.derivation);
            let bar_kind = builder.create_string(if value.derivation.starts_with("trade:") {
                "trade_bar"
            } else if value.derivation.starts_with("quote:") {
                "quote_bar"
            } else {
                "bar"
            });
            let open = decimal64_price(value.open);
            let high = decimal64_price(value.high);
            let low = decimal64_price(value.low);
            let close = decimal64_price(value.close);
            let volume = value.volume.map(decimal64_quantity);
            let bar = FbBar::create(
                &mut builder,
                &FbBarArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    timeframe: Some(timeframe),
                    open: Some(&open),
                    high: Some(&high),
                    low: Some(&low),
                    close: Some(&close),
                    volume: volume.as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                    derivation: Some(derivation),
                    bar_kind: Some(bar_kind),
                },
            );
            let root = BarMessage::create(
                &mut builder,
                &BarMessageArgs {
                    header: Some(header),
                    payload: Some(bar),
                },
            );
            finish_bar_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::TradeBar(value) => {
            let mut bar = value.bar.clone();
            bar.derivation = if bar.derivation.is_empty() {
                "trade".into()
            } else {
                format!("trade:{}", bar.derivation)
            };
            encode_event(actor_id, identity, sequence, &MarketObservation::Bar(bar))
        }
        MarketObservation::QuoteBar(value) => {
            let mut bar = value.bar.clone();
            bar.derivation = if bar.derivation.is_empty() {
                "quote".into()
            } else {
                format!("quote:{}", bar.derivation)
            };
            encode_event(actor_id, identity, sequence, &MarketObservation::Bar(bar))
        }
        MarketObservation::OptionGreeks(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let derivation = builder.create_string(&value.derivation);
            let strike = value.strike.map(decimal64_price);
            let delta = value.delta.map(decimal64_rate);
            let gamma = value.gamma.map(decimal64_rate);
            let vega = value.vega.map(decimal64_rate);
            let theta = value.theta.map(decimal64_rate);
            let implied_volatility = value.implied_volatility.map(decimal64_rate);
            let greeks = FbGreeks::create(
                &mut builder,
                &FbGreeksArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    expiry_unix_nanos: value.expiry_unix_nanos.map_or(0, Into::into),
                    strike: strike.as_ref(),
                    delta: delta.as_ref(),
                    gamma: gamma.as_ref(),
                    vega: vega.as_ref(),
                    theta: theta.as_ref(),
                    implied_volatility: implied_volatility.as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                    derivation: Some(derivation),
                },
            );
            let root = GreeksMessage::create(
                &mut builder,
                &GreeksMessageArgs {
                    header: Some(header),
                    payload: Some(greeks),
                },
            );
            finish_greeks_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::Rate(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let rate_id = builder.create_string(&value.rate_id);
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let basis = builder.create_string(&value.basis);
            let source_id = builder.create_string(&value.source_id);
            let rate_value = decimal64_rate(value.value);
            let mark_price = value.mark_price.map(decimal64_price);
            let rate = FbRate::create(
                &mut builder,
                &FbRateArgs {
                    rate_id: Some(rate_id),
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    basis: Some(basis),
                    value: Some(&rate_value),
                    mark_price: mark_price.as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            let root = RateMessage::create(
                &mut builder,
                &RateMessageArgs {
                    header: Some(header),
                    payload: Some(rate),
                },
            );
            finish_rate_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::Ticker24h(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let payload = FbTicker24h::create(
                &mut builder,
                &FbTicker24hArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    last_price: value.last_price.map(decimal64_price).as_ref(),
                    bid_price: value.bid_price.map(decimal64_price).as_ref(),
                    bid_quantity: value.bid_quantity.map(decimal64_quantity).as_ref(),
                    ask_price: value.ask_price.map(decimal64_price).as_ref(),
                    ask_quantity: value.ask_quantity.map(decimal64_quantity).as_ref(),
                    open_price: value.open_price.map(decimal64_price).as_ref(),
                    high_price: value.high_price.map(decimal64_price).as_ref(),
                    low_price: value.low_price.map(decimal64_price).as_ref(),
                    volume_base: value.volume_base.map(decimal64_quantity).as_ref(),
                    volume_quote: value.volume_quote.map(decimal64_money).as_ref(),
                    price_change_abs: value.price_change_abs.map(decimal64_money).as_ref(),
                    price_change_pct: value.price_change_pct.map(decimal64_rate).as_ref(),
                    vwap: value.vwap.map(decimal64_price).as_ref(),
                    mark_price: value.mark_price.map(decimal64_price).as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                Ticker24hMessage,
                Ticker24hMessageArgs,
                finish_ticker_24h_message_buffer
            )
        }
        MarketObservation::MarkPrice(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let mark_price = decimal64_price(value.mark_price);
            let index_price = value.index_price.map(decimal64_price);
            let settlement = value.estimated_settlement_price.map(decimal64_price);
            let funding = value.funding_rate.map(decimal64_rate);
            let payload = FbMarkPrice::create(
                &mut builder,
                &FbMarkPriceArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    mark_price: Some(&mark_price),
                    index_price: index_price.as_ref(),
                    estimated_settlement_price: settlement.as_ref(),
                    funding_rate: funding.as_ref(),
                    next_funding_time_unix_nanos: value
                        .next_funding_time_unix_nanos
                        .map_or(0, Into::into),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                MarkPriceMessage,
                MarkPriceMessageArgs,
                finish_mark_price_message_buffer
            )
        }
        MarketObservation::IndexPrice(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let payload = FbIndexPrice::create(
                &mut builder,
                &FbIndexPriceArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    spot_index_price: value.spot_index_price.map(decimal64_price).as_ref(),
                    contract_index_price: value.contract_index_price.map(decimal64_price).as_ref(),
                    index_price: value.index_price.map(decimal64_price).as_ref(),
                    funding_rate: value.funding_rate.map(decimal64_rate).as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                IndexPriceMessage,
                IndexPriceMessageArgs,
                finish_index_price_message_buffer
            )
        }
        MarketObservation::FundingRate(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let rate = decimal64_rate(value.funding_rate);
            let payload = FbFundingRate::create(
                &mut builder,
                &FbFundingRateArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    funding_rate: Some(&rate),
                    funding_period_seconds: value.funding_period_seconds.unwrap_or_default(),
                    next_funding_time_unix_nanos: value
                        .next_funding_time_unix_nanos
                        .map_or(0, Into::into),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                FundingRateMessage,
                FundingRateMessageArgs,
                finish_funding_rate_message_buffer
            )
        }
        MarketObservation::OpenInterest(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let contracts = decimal64_quantity(value.contracts);
            let payload = FbOpenInterest::create(
                &mut builder,
                &FbOpenInterestArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    contracts: Some(&contracts),
                    quote_value: value.quote_value.map(decimal64_money).as_ref(),
                    change_24h: value.change_24h.map(decimal64_money).as_ref(),
                    change_pct_24h: value.change_pct_24h.map(decimal64_rate).as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                OpenInterestMessage,
                OpenInterestMessageArgs,
                finish_open_interest_message_buffer
            )
        }
        MarketObservation::InstrumentStatus(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let status = builder.create_string(value.status.as_str());
            let reason = value
                .reason
                .as_ref()
                .map(|value| builder.create_string(value));
            let source_id = builder.create_string(&value.source_id);
            let payload = FbInstrumentStatus::create(
                &mut builder,
                &FbInstrumentStatusArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    status: Some(status),
                    reason,
                    effective_at_unix_nanos: value.effective_at_unix_nanos.map_or(0, Into::into),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                InstrumentStatusMessage,
                InstrumentStatusMessageArgs,
                finish_instrument_status_message_buffer
            )
        }
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
    use super::{
        encode_event, forward_reference_events, EngineCommand, MarketProcess,
        MarketSnapshotPublisher,
    };
    use crate::composition::MmapMarketSnapshotPublisher;
    use crate::domain::freshness::FeedStatus;
    use crate::domain::market::MarketDescriptor;
    use crate::domain::observations::{
        Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, OpenInterest, OptionGreeks,
        Ticker24h,
    };
    use crate::services::composite::{CompositeMarketFeed, MarketFeedFactory, MarketRoute};
    use crate::services::feed::{MarketFeed, MarketOrderBookUpdate};
    use crate::{MarketApplication, MarketRuntime, SubscriptionId};
    use axum::http::Method;
    use kairos_protocol::InstanceIdentity;
    use serde_json::json;
    use std::collections::BTreeMap;
    use std::collections::VecDeque;
    use std::time::Duration;

    struct NullPublisher;

    impl MarketSnapshotPublisher for NullPublisher {
        fn publish(
            &mut self,
            _snapshot: &crate::domain::snapshot::MarketSnapshot,
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
            &encode_event("actor", &identity, 1, &bar).unwrap()[4..8],
            b"MBA1"
        );
        assert_eq!(
            &encode_event("actor", &identity, 2, &greeks).unwrap()[4..8],
            b"MGR1"
        );
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
                &encode_event("actor", &identity, sequence, &observation).unwrap()[4..8],
                identifier
            );
        }
    }

    #[test]
    fn command_idempotency_replays_same_result_and_rejects_payload_reuse() {
        let root = tempfile::tempdir().unwrap();
        let application = MarketApplication::new("test-market", 10).unwrap();
        let mut process = MarketProcess::new(
            MarketRuntime::new(application),
            NullPublisher,
            root.path().join("market.sock"),
            root.path().join("market.events.sock"),
            Duration::from_millis(10),
            false,
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
                "params": {},
                "dynamic": false
            }
        }))
        .unwrap();
        let first = process
            .engine
            .handle_request(&Method::POST, "/v1/subscribe", &body);
        let second = process
            .engine
            .handle_request(&Method::POST, "/v1/subscribe", &body);
        assert_eq!(first.status, second.status);
        assert_eq!(first.payload, second.payload);
        let conflict = body.replace("BTCUSDT", "ETHUSDT");
        let response = process
            .engine
            .handle_request(&Method::POST, "/v1/subscribe", &conflict);
        assert_eq!(response.status, axum::http::StatusCode::CONFLICT);
    }

    struct FakeFeed {
        next: u64,
    }

    impl MarketFeed for FakeFeed {
        fn subscribe(&mut self, _market: &MarketDescriptor) -> Result<SubscriptionId, String> {
            let id = SubscriptionId::new(format!("fake:{}", self.next))?;
            self.next += 1;
            Ok(id)
        }

        fn unsubscribe(&mut self, _subscription: &SubscriptionId) -> Result<(), String> {
            Ok(())
        }

        fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
            Ok(Vec::new())
        }

        fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
            Ok(Vec::new())
        }

        fn status(&self) -> FeedStatus {
            FeedStatus::Ready
        }
    }

    #[test]
    fn command_endpoint_accepts_all_required_product_routes_in_one_process() {
        let routes = [
            ("massive", "equity", "equity"),
            ("massive", "options", "equity"),
            ("binance", "spot", "crypto"),
            ("binance", "usd-m-futures", "crypto"),
            ("binance", "coin-m-futures", "crypto"),
            ("binance", "options", "crypto"),
            ("binance", "equity", "equity"),
            ("okx", "spot", "crypto"),
            ("okx", "spot", "equity"),
            ("okx", "swap", "crypto"),
            ("okx", "futures", "crypto"),
            ("okx", "options", "crypto"),
        ];
        let mut factories: BTreeMap<MarketRoute, MarketFeedFactory> = BTreeMap::new();
        for (exchange, market_type, asset_type) in routes {
            factories.insert(
                MarketRoute::with_asset_type(exchange, market_type, asset_type),
                Box::new(|| Ok(Box::new(FakeFeed { next: 1 }) as Box<dyn MarketFeed>)),
            );
        }
        let application = MarketApplication::new("test-market", 100).unwrap();
        let application = MarketRuntime::with_feed(
            application,
            Box::new(CompositeMarketFeed::new(factories).unwrap()),
        );
        let directory = tempfile::tempdir().unwrap();
        let publisher = MmapMarketSnapshotPublisher::create(
            directory.path().join("market.snapshot"),
            4096,
            "test-market",
            "market.events",
        )
        .unwrap();
        let mut process = MarketProcess::new(
            application,
            publisher,
            directory.path().join("market.sock"),
            directory.path().join("market-events.sock"),
            Duration::from_secs(1),
            true,
        )
        .unwrap();
        process.engine.reference_markets = Some(
            routes
                .iter()
                .enumerate()
                .map(|(index, (exchange, market_type, asset_type))| {
                    MarketDescriptor::new_with_asset_type(
                        format!("market-{index}"),
                        format!("instrument-{index}"),
                        *exchange,
                        *market_type,
                        *asset_type,
                        format!("SYMBOL{index}"),
                    )
                    .unwrap()
                })
                .collect(),
        );

        for (index, (exchange, market_type, asset_type)) in routes.into_iter().enumerate() {
            let body = json!({
                "schema_version": 1,
                "command_id": format!("command-{index}"),
                "idempotency_key": format!("command-{index}"),
                "operation": "market.subscribe",
                "strategy_id": "all-products",
                "instance_id": "instance-1",
                "payload": {
                    "subject": format!("market.SYMBOL{index}"),
                    "selectors": ["quote"],
                    "exchange": exchange,
                    "market_type": market_type,
                    "asset_type": asset_type,
                    "identity": null,
                    "dynamic": false,
                }
            })
            .to_string();
            let (status, _) = process.engine.subscribe(&body);
            assert_eq!(status, 202, "route {exchange}/{market_type}/{asset_type}");
        }
        assert_eq!(
            process.engine.application.snapshot().subscriptions.len(),
            routes.len()
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
}

fn event_header<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    actor_id: &str,
    identity: &InstanceIdentity,
    stream_id: &str,
    sequence: u64,
    event_time: u64,
) -> flatbuffers::WIPOffset<MessageHeader<'a>> {
    let message_id = builder.create_string(&format!("market:{sequence}"));
    let stream = builder.create_string(stream_id);
    let actor = builder.create_string(actor_id);
    let workspace_id = non_empty_string(builder, &identity.workspace_id);
    let launch_id = non_empty_string(builder, &identity.launch_id);
    let instance_id = non_empty_string(builder, &identity.instance_id);
    MessageHeader::create(
        builder,
        &MessageHeaderArgs {
            message_id: Some(message_id),
            stream_id: Some(stream),
            producer_id: Some(actor),
            workspace_id,
            launch_id,
            instance_id,
            sequence,
            event_time_unix_nanos: event_time,
            publish_time_unix_nanos: event_time,
        },
    )
}

fn decimal64_price(value: kairos_domain_types::Price) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}

fn decimal64_quantity(value: kairos_domain_types::Quantity) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}

fn decimal64_money(value: kairos_domain_types::Money) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}

fn decimal64_rate(value: kairos_domain_types::Rate) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}

fn aggressor_side(value: Option<&str>) -> kairos_protocol::generated::kairos::common::v_1::Side {
    use kairos_protocol::generated::kairos::common::v_1::Side;
    match value.map(|value| value.to_ascii_lowercase()).as_deref() {
        Some("buy") => Side::BUY,
        Some("sell") => Side::SELL,
        _ => Side::UNSPECIFIED,
    }
}

fn non_empty_string<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}

async fn market_http_handler(
    State(sender): State<Sender<EngineCommand>>,
    request: Request,
) -> Response {
    let started = Instant::now();
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
    let span = tracing::info_span!(
        "market.control_request",
        component = "market",
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
    let response = market_http_handler_inner(sender, request)
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
    tracing::info!(parent: &span, event = "control_request_completed", component = "market", duration_ms, result = if response.status().is_success() { "accepted" } else { "rejected" }, "market control request completed");
    response
}

async fn market_http_handler_inner(sender: Sender<EngineCommand>, request: Request) -> Response {
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
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
        .try_send(EngineCommand::Http(MarketHttpRequest {
            method,
            path,
            body,
            response: response_sender,
        }))
        .is_err()
    {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"market control queue is full or process is stopping"})),
        )
            .into_response();
    }
    match response_receiver.await {
        Ok(response) => (response.status, Json(response.payload)).into_response(),
        Err(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"market process did not respond"})),
        )
            .into_response(),
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

async fn event_client_writer(mut stream: UnixStream, mut receiver: Receiver<Vec<u8>>) {
    while let Some(payload) = receiver.recv().await {
        let frame = (payload.len() as u32).to_be_bytes();
        if stream.write_all(&frame).await.is_err() || stream.write_all(&payload).await.is_err() {
            break;
        }
    }
}

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}
