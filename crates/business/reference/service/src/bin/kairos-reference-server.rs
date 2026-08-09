use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json, Router,
};
use clap::Parser;
use kairos_integration::credentials::load_workspace_credential;
use kairos_protocol::InstanceIdentity;
use kairos_reference::application::control;
use kairos_reference::application::MarketQuery;
use kairos_reference::application::{
    ReferenceApplication, ReferenceKind, ReferenceQuery, ReferenceReadModel,
};
use kairos_reference::composition::{
    build_application, default_endpoint, ensure_database_parent, ReferenceCompositionConfig,
    ReferenceEventWriter, ReferenceEventWriterConfig, ReferenceMmapSnapshotWriter,
};
use kairos_reference::domain::Asset;
use kairos_workspace::workspace::Workspace;
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::{self as std_mpsc, SyncSender, TrySendError};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::net::UnixListener;
use tokio::sync::mpsc::Sender;
use tokio::sync::{mpsc, oneshot, RwLock};

// Reference providers currently use blocking HTTP clients. Keep the control
// server asynchronous, but give provider refreshes a blocking section on a
// multi-thread Tokio runtime so reqwest::blocking never runs inside a
// current-thread runtime context.
#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    kairos_workspace::logging::init("reference");
    if let Err(error) = run().await {
        tracing::error!(event = "process_failed", component = "reference", error = %error, "reference server failed");
        eprintln!("kairos-reference-server: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    tracing::info!(event = "process_start", component = "reference", provider = %args.provider, "starting reference server");
    let workspace = Workspace::open(args.workspace)?;
    if let Some(path) = &args.socket {
        if !path.starts_with(workspace.root()) {
            return Err("reference socket must be inside workspace".into());
        }
    }
    if let Some(path) = &args.health_file {
        if !path.starts_with(workspace.root()) {
            return Err("reference health file must be inside workspace".into());
        }
    }
    let _process_lock = workspace.process_lock("reference").map_err(|error| {
        if error.kind() == std::io::ErrorKind::AlreadyExists {
            "reference is already running for this workspace".to_string()
        } else {
            format!("acquire reference workspace lock: {error}")
        }
    })?;
    let provider = args.provider;
    let endpoint = args
        .endpoint
        .unwrap_or_else(|| default_endpoint(&provider).to_string());
    let credential = args
        .credential_id
        .as_deref()
        .map(|credential_id| {
            let credential_provider = if provider.starts_with("massive") {
                "massive"
            } else if provider.starts_with("binance") {
                "binance"
            } else if provider.starts_with("okx") {
                "okx"
            } else {
                provider.as_str()
            };
            load_workspace_credential(
                &workspace.root().join("credentials"),
                credential_provider,
                Some(credential_id),
            )
            .map_err(|error| format!("load reference credential {credential_id}: {error}"))?
            .ok_or_else(|| format!("reference credential not found: {credential_id}"))
        })
        .transpose()?;
    let api_key = args
        .api_key
        .or_else(|| credential.as_ref().map(|value| value.api_key.clone()))
        .unwrap_or_default();
    let binance_api_key = args
        .binance_api_key
        .or_else(|| credential.as_ref().map(|value| value.api_key.clone()))
        .unwrap_or_default();
    let secret = args
        .secret
        .or_else(|| credential.as_ref().map(|value| value.secret.clone()))
        .unwrap_or_default();
    let database = args
        .database
        .unwrap_or(workspace.child(&["reference", "reference.sqlite"])?);
    if !database.starts_with(workspace.root()) {
        return Err("reference database must be inside workspace".into());
    }
    if args.reference_changes_stream <= 0 {
        return Err("reference event stream id must be positive".into());
    }
    let aeron_channel = args.aeron_channel;
    let aeron_dir = args.aeron_dir.clone();
    let reference_changes_stream = args.reference_changes_stream;
    tracing::info!(
        event = "reference_transport_config",
        component = "reference",
        provider = %provider,
        aeron_channel = %aeron_channel,
        reference_changes_stream,
        "reference transport configured"
    );
    if let Some(parent) = database.parent() {
        std::fs::create_dir_all(parent)?;
    }
    ensure_database_parent(&database)?;
    let config = ReferenceCompositionConfig {
        workspace: Some(workspace.root().to_path_buf()),
        provider,
        endpoint,
        database,
        api_key,
        binance_api_key,
        secret,
        aeron_dir: aeron_dir.clone(),
        aeron_channel: aeron_channel.clone(),
        reference_changes_stream,
    };
    let composition = build_application(&config, true)?;
    let mut application = composition.application;
    let mut event_writer = composition.event_writer;

    tokio::task::block_in_place(|| application.refresh())?;

    let snapshot_root = workspace.child(&["snapshots", "reference"])?;
    std::fs::create_dir_all(&snapshot_root)?;
    // Remove the pre-view-model lifecycle history snapshot. Lifecycle history
    // is now an event-plane concern; leaving this stale file would make it
    // look like a supported current-state resource.
    let _ = std::fs::remove_file(snapshot_root.join("lifecycle.snapshot"));
    let snapshot_slot_size = args
        .snapshot_slot_size_mib
        .checked_mul(1024 * 1024)
        .and_then(|value| usize::try_from(value).ok())
        .ok_or("snapshot slot size is too large")?;
    let mut mmap_writer = ReferenceMmapSnapshotWriter::create(
        snapshot_root.join("catalog.snapshot"),
        snapshot_root.join("entities.snapshot"),
        snapshot_root.join("assets.snapshot"),
        snapshot_root.join("instruments.snapshot"),
        snapshot_root.join("listings.snapshot"),
        snapshot_root.join("markets.snapshot"),
        snapshot_root.join("financial-products.snapshot"),
        snapshot_root.join("execution-accesses.snapshot"),
        snapshot_slot_size,
        InstanceIdentity::new(workspace.id(), "reference", "global"),
    )?;

    mmap_writer.publish(application.catalog())?;
    if let Some(writer) = event_writer.as_mut() {
        let events = application.pending_events(EVENT_BATCH_LIMIT)?;
        writer.publish(application.catalog(), &events)?;
        let event_ids = events
            .iter()
            .map(|event| event.event_id.clone())
            .collect::<Vec<_>>();
        application.acknowledge_published_events(&event_ids)?;
    }
    tracing::info!(
        event = "initial_refresh_complete",
        component = "reference",
        generation = application.catalog().generation,
        event_sequence = application.catalog().event_sequence,
        events = application.catalog().lifecycle_events.len(),
        "reference catalog initialized"
    );
    println!(
        "reference generation={} event_sequence={} events={}",
        application.catalog().generation,
        application.catalog().event_sequence,
        application.catalog().lifecycle_events.len()
    );
    if args.run_mode == "once" {
        return Ok(());
    }

    let socket = if let Some(socket) = args.socket {
        if !socket.starts_with(workspace.root()) {
            return Err("reference socket must be inside workspace".into());
        }
        socket
    } else {
        workspace.process_socket("reference")?
    };
    let health_file = args
        .health_file
        .or_else(|| workspace.health_file("reference").ok());
    let event_writer_config = event_writer.take().map(|_| ReferenceEventWriterConfig {
        aeron_dir,
        aeron_channel,
        reference_changes_stream,
    });
    run_process(
        application,
        event_writer_config,
        mmap_writer,
        socket,
        health_file,
        args.refresh_interval,
    )
    .await
}

async fn run_process(
    mut application: ReferenceApplication,
    event_writer_config: Option<ReferenceEventWriterConfig>,
    mut mmap_writer: ReferenceMmapSnapshotWriter,
    socket: PathBuf,
    health_file: Option<PathBuf>,
    refresh_interval: Duration,
) -> Result<(), Box<dyn std::error::Error>> {
    let event_publisher = event_writer_config.map(EventPublisherRuntime::new);
    tracing::info!(event = "process_starting", component = "reference", socket = %socket.display(), refresh_interval_secs = refresh_interval.as_secs(), "reference process starting");
    if let Some(parent) = socket.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    // The workspace process lock is already held by the caller. Only now is
    // it safe to remove a stale socket from a previous crashed process.
    let _ = std::fs::remove_file(&socket);
    let listener = UnixListener::bind(&socket)?;
    let (sender, mut receiver) = mpsc::channel(CONTROL_QUEUE_CAPACITY);
    let control_queue_depth = Arc::new(AtomicUsize::new(0));
    let read_model = Arc::new(RwLock::new(application.read_model()));
    let health_status = Arc::new(RwLock::new(String::from("ready")));
    let router = Router::new()
        .fallback(reference_http_handler)
        .with_state(ReferenceServerState {
            sender,
            read_model: Arc::clone(&read_model),
            health_status: Arc::clone(&health_status),
            control_queue_depth: Arc::clone(&control_queue_depth),
        });
    let server = tokio::spawn(async move { axum::serve(listener, router).await });
    tracing::info!(event = "process_ready", component = "reference", socket = %socket.display(), "reference control socket ready");
    let mut interval = tokio::time::interval(refresh_interval);
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    interval.tick().await;
    let initial_status = reference_status(&application);
    write_health(&health_file, &application, initial_status).await?;
    mmap_writer.publish(application.catalog())?;
    let mut stopping = false;
    while !stopping {
        tokio::select! {
            Some(request) = receiver.recv() => {
                control_queue_depth.fetch_sub(1, Ordering::Relaxed);
                let response = tokio::task::block_in_place(|| handle_request(&mut application, event_publisher.as_ref(), &mut mmap_writer, &request.target, &String::from_utf8_lossy(&request.body)));
                if let Ok((should_stop, status, payload)) = &response {
                    stopping = *should_stop;
                    let _ = request.response.send(Ok((*should_stop, *status, payload.clone())));
                } else {
                    let _ = request.response.send(response.map_err(|error| error.to_string()));
                }
                *read_model.write().await = application.read_model();
            }
            _ = interval.tick() => {
                let status = match tokio::task::block_in_place(|| {
                    refresh_cycle(&mut application, event_publisher.as_ref(), &mut mmap_writer)
                }) {
                    Ok(()) => reference_status(&application),
                    Err(error) => {
                        tracing::warn!(event = "refresh_failed", component = "reference", error = %error, "reference refresh failed");
                        "degraded"
                    }
                };
                *read_model.write().await = application.read_model();
                *health_status.write().await = status.to_owned();
                write_health(&health_file, &application, status).await?;
            }
        }
    }
    let _ = std::fs::remove_file(&socket);
    server.abort();
    let _ = server.await;
    tracing::info!(
        event = "process_stopped",
        component = "reference",
        "reference process stopped"
    );
    Ok(())
}

struct ReferenceHttpRequest {
    target: String,
    body: Vec<u8>,
    response: oneshot::Sender<Result<(bool, u16, Value), String>>,
}

struct PublishRequest {
    catalog: kairos_reference::domain::ReferenceCatalog,
    events: Vec<kairos_reference::domain::LifecycleEvent>,
    response: SyncSender<Result<(), String>>,
}

struct EventPublisherRuntime {
    requests: SyncSender<PublishRequest>,
}

impl EventPublisherRuntime {
    fn new(config: ReferenceEventWriterConfig) -> Self {
        let (requests, receiver) =
            std_mpsc::sync_channel::<PublishRequest>(EVENT_PUBLISH_QUEUE_CAPACITY);
        std::thread::Builder::new()
            .name("reference-event-publisher".into())
            .spawn(move || {
                let mut writer = ReferenceEventWriter::connect(&config)
                    .expect("connect reference event publisher worker");
                while let Ok(request) = receiver.recv() {
                    let result = writer
                        .publish(&request.catalog, &request.events)
                        .map_err(|error| error.to_string());
                    let _ = request.response.send(result);
                }
            })
            .expect("start reference event publisher worker");
        Self { requests }
    }

    fn publish(
        &self,
        catalog: &kairos_reference::domain::ReferenceCatalog,
        events: &[kairos_reference::domain::LifecycleEvent],
    ) -> kairos_reference::domain::ReferenceResult<()> {
        let (response, receiver) = std_mpsc::sync_channel(1);
        match self.requests.try_send(PublishRequest {
            catalog: catalog.clone(),
            events: events.to_vec(),
            response,
        }) {
            Ok(()) => receiver
                .recv()
                .map_err(|error| {
                    kairos_reference::domain::ReferenceError::Publication(error.to_string())
                })?
                .map_err(kairos_reference::domain::ReferenceError::Publication),
            Err(TrySendError::Full(_)) => {
                Err(kairos_reference::domain::ReferenceError::Publication(
                    "reference event publisher queue is full".into(),
                ))
            }
            Err(TrySendError::Disconnected(_)) => {
                Err(kairos_reference::domain::ReferenceError::Publication(
                    "reference event publisher is unavailable".into(),
                ))
            }
        }
    }
}

#[derive(Clone)]
struct ReferenceServerState {
    sender: Sender<ReferenceHttpRequest>,
    read_model: Arc<RwLock<ReferenceReadModel>>,
    health_status: Arc<RwLock<String>>,
    control_queue_depth: Arc<AtomicUsize>,
}

async fn reference_http_handler(
    State(state): State<ReferenceServerState>,
    request: Request,
) -> Response {
    let target = request
        .uri()
        .path_and_query()
        .map(|value| value.as_str().to_owned())
        .unwrap_or_else(|| request.uri().path().to_owned());
    let path = target
        .split_once('?')
        .map_or(target.as_str(), |(path, _)| path);
    if path == control::HEALTH {
        let model = state.read_model.read().await;
        let status = state.health_status.read().await;
        return Json(health_json_read_model(
            &model,
            &status,
            state.control_queue_depth.load(Ordering::Relaxed),
        ))
        .into_response();
    }
    if path == control::SNAPSHOT {
        let model = state.read_model.read().await;
        return Json(json!({
            "actor_id": model.actor_id(),
            "generation": model.catalog().generation,
            "event_sequence": model.catalog().event_sequence,
            "catalog": model.catalog(),
        }))
        .into_response();
    }
    if path == control::MARKETS || path == control::RESOLVE_MARKET {
        let params = target.split_once('?').map_or("", |(_, query)| query);
        let query = market_query(&query_params(params));
        let model = state.read_model.read().await;
        if path == control::MARKETS {
            return Json(json!({
                "generation": model.generation(),
                "markets": model
                    .catalog()
                    .markets
                    .values()
                    .filter(|market| query.matches(market))
                    .collect::<Vec<_>>(),
            }))
            .into_response();
        }
        return match model
            .catalog()
            .markets
            .values()
            .filter(|market| query.matches(market))
            .collect::<Vec<_>>()
            .as_slice()
        {
            [market] => Json(
                serde_json::to_value(market)
                    .unwrap_or_else(|error| json!({"error": error.to_string()})),
            )
            .into_response(),
            [] => (
                StatusCode::NOT_FOUND,
                Json(json!({"error":"market not found"})),
            )
                .into_response(),
            _ => (
                StatusCode::CONFLICT,
                Json(json!({"error":"market query is ambiguous"})),
            )
                .into_response(),
        };
    }
    if path == control::QUERY || path == control::SHOW {
        let params = target.split_once('?').map_or("", |(_, query)| query);
        let params = query_params(params);
        let model = state.read_model.read().await;
        if path == control::SHOW {
            let Some(identifier) = params.get("identifier") else {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"error":"show requires identifier"})),
                )
                    .into_response();
            };
            return match model.record(identifier) {
                Ok(record) => Json(
                    serde_json::to_value(record)
                        .unwrap_or_else(|error| json!({"error": error.to_string()})),
                )
                .into_response(),
                Err(error) => (
                    StatusCode::NOT_FOUND,
                    Json(json!({"error": error.to_string()})),
                )
                    .into_response(),
            };
        }
        let mut query = reference_query(&params);
        if path == control::EVENTS {
            query.kind = ReferenceKind::Event;
        }
        return Json(model.query(&query)).into_response();
    }
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
    match state.sender.try_send(ReferenceHttpRequest {
        target,
        body,
        response: response_sender,
    }) {
        Ok(()) => {
            state.control_queue_depth.fetch_add(1, Ordering::Relaxed);
        }
        Err(tokio::sync::mpsc::error::TrySendError::Full(_)) => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"error":"reference control queue is full"})),
            )
                .into_response()
        }
        Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"error":"reference process is stopping"})),
            )
                .into_response()
        }
    }
    match response_receiver.await {
        Ok(Ok((_, status, payload))) => (
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
            Json(json!({"error":"reference process did not respond"})),
        )
            .into_response(),
    }
}

fn handle_request(
    application: &mut ReferenceApplication,
    writer: Option<&EventPublisherRuntime>,
    mmap_writer: &mut ReferenceMmapSnapshotWriter,
    target: &str,
    body: &str,
) -> Result<(bool, u16, Value), Box<dyn std::error::Error>> {
    let started = Instant::now();
    tracing::info!(event = "control_request", component = "reference", path = %target, "reference control request received");
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    let params = query_params(query);
    let (status, body, stopping) = match path {
        control::HEALTH => (200, health_json(application, "ready"), false),
        control::PROVIDERS => (
            200,
            json!({
                "mode": application.source_id(),
                "source_id": application.source_id(),
            }),
            false,
        ),
        control::SNAPSHOT => (
            200,
            json!({
                "actor_id": application.actor_id(),
                "generation": application.catalog().generation,
                "event_sequence": application.catalog().event_sequence,
                "catalog": application.catalog(),
            }),
            false,
        ),
        control::MARKETS => {
            let query = MarketQuery {
                market_id: params.get("market_id").cloned(),
                venue_id: params.get("venue_id").cloned(),
                market_type: params.get("market_type").cloned(),
                asset_type: params.get("asset_type").cloned(),
                source_symbol: params.get("symbol").cloned(),
                active_only: params
                    .get("active_only")
                    .is_some_and(|value| value == "true"),
                as_of_unix_nanos: None,
                status: params.get("status").cloned(),
            };
            (200, json!({"markets": application.markets(&query)}), false)
        }
        control::RESOLVE_MARKET => {
            let query = MarketQuery {
                market_id: params.get("market_id").cloned(),
                venue_id: params.get("venue_id").cloned(),
                market_type: params.get("market_type").cloned(),
                asset_type: params.get("asset_type").cloned(),
                source_symbol: params.get("symbol").cloned(),
                active_only: params
                    .get("active_only")
                    .is_some_and(|value| value == "true"),
                as_of_unix_nanos: None,
                status: params.get("status").cloned(),
            };
            match application.resolve_market(&query) {
                Ok(market) => (200, serde_json::to_value(market)?, false),
                Err(error) => (404, json!({"error": error.to_string()}), false),
            }
        }
        control::QUERY => {
            let query = reference_query(&params);
            (200, serde_json::to_value(application.query(&query))?, false)
        }
        control::EVENTS => {
            let mut query = reference_query(&params);
            query.kind = ReferenceKind::Event;
            (
                200,
                serde_json::to_value(application.query_lifecycle_events(&query)?)?,
                false,
            )
        }
        control::SHOW => {
            let identifier = params.get("identifier").ok_or("show requires identifier")?;
            match application.record(identifier) {
                Ok(record) => (200, serde_json::to_value(record)?, false),
                Err(error) => (404, json!({"error": error.to_string()}), false),
            }
        }
        control::REFRESH => match application.refresh() {
            Ok(result) => {
                let publication = publish_pending(writer, application);
                let snapshot = publish_snapshot(mmap_writer, application);
                match publication.and_then(|events| snapshot.map(|_| events)) {
                    Ok(events) => (
                        200,
                        json!({"generation": result.generation, "event_sequence": result.event_sequence, "events": events}),
                        false,
                    ),
                    Err(error) => (503, json!({"error": error.to_string()}), false),
                }
            }
            Err(error) => (503, json!({"error": error.to_string()}), false),
        },
        control::PUBLISH => match publish_pending(writer, application) {
            Ok(events) => (
                200,
                json!({"generation": application.catalog().generation, "events": events}),
                false,
            ),
            Err(error) => (503, json!({"error": error.to_string()}), false),
        },
        control::ASSETS => match serde_json::from_str::<Asset>(body) {
            Ok(asset) => match application.upsert_asset(asset) {
                Ok(generation) => match publish_snapshot(mmap_writer, application) {
                    Ok(()) => (200, json!({"generation": generation}), false),
                    Err(error) => (503, json!({"error": error.to_string()}), false),
                },
                Err(error) => (400, json!({"error": error.to_string()}), false),
            },
            Err(error) => (
                400,
                json!({"error": format!("invalid asset: {error}")}),
                false,
            ),
        },
        control::STOP => (202, json!({"status": "stopping"}), true),
        _ => (
            404,
            json!({"error": "unknown reference control path"}),
            false,
        ),
    };
    tracing::info!(event = "control_response", component = "reference", path = %path, status, duration_ms = started.elapsed().as_millis(), "reference control response sent");
    Ok((stopping, status, body))
}

fn publish(
    writer: Option<&EventPublisherRuntime>,
    application: &ReferenceApplication,
    events: &[kairos_reference::domain::LifecycleEvent],
) -> kairos_reference::domain::ReferenceResult<()> {
    publish_events(writer, application.catalog(), events)?;
    tracing::info!(
        event = "reference_changes_published",
        component = "reference",
        generation = application.catalog().generation,
        event_sequence = application.catalog().event_sequence,
        change_count = events.len(),
        "reference changes published"
    );
    Ok(())
}

fn publish_events(
    writer: Option<&EventPublisherRuntime>,
    catalog: &kairos_reference::domain::ReferenceCatalog,
    events: &[kairos_reference::domain::LifecycleEvent],
) -> kairos_reference::domain::ReferenceResult<()> {
    let writer = writer.ok_or_else(|| {
        kairos_reference::domain::ReferenceError::Publication(
            "reference publication is not configured".into(),
        )
    })?;
    writer.publish(catalog, events)
}

fn publish_pending(
    writer: Option<&EventPublisherRuntime>,
    application: &mut ReferenceApplication,
) -> kairos_reference::domain::ReferenceResult<usize> {
    let events = application.pending_events(EVENT_BATCH_LIMIT)?;
    let count = events.len();
    publish(writer, application, &events)?;
    let event_ids = events
        .iter()
        .map(|event| event.event_id.clone())
        .collect::<Vec<_>>();
    application.acknowledge_published_events(&event_ids)?;
    tracing::info!(
        event = "reference_pending_events_published",
        component = "reference",
        event_count = count,
        "reference pending events published"
    );
    Ok(count)
}

fn publish_snapshot(
    mmap_writer: &mut ReferenceMmapSnapshotWriter,
    application: &ReferenceApplication,
) -> kairos_reference::domain::ReferenceResult<()> {
    mmap_writer
        .publish(application.catalog())
        .map_err(|error| kairos_reference::domain::ReferenceError::Publication(error.to_string()))
}

fn refresh_cycle(
    application: &mut ReferenceApplication,
    publisher: Option<&EventPublisherRuntime>,
    mmap_writer: &mut ReferenceMmapSnapshotWriter,
) -> kairos_reference::domain::ReferenceResult<()> {
    application.refresh()?;
    let publication = if publisher.is_some() {
        let events = application.pending_events(EVENT_BATCH_LIMIT)?;
        publish_events(publisher, application.catalog(), &events)?;
        let event_ids = events
            .iter()
            .map(|event| event.event_id.clone())
            .collect::<Vec<_>>();
        application.acknowledge_published_events(&event_ids)
    } else {
        Ok(())
    };
    let snapshot = mmap_writer
        .publish(application.catalog())
        .map_err(|error| kairos_reference::domain::ReferenceError::Publication(error.to_string()));
    publication.and(snapshot)
}

const EVENT_BATCH_LIMIT: usize = 256;
const CONTROL_QUEUE_CAPACITY: usize = 64;
const EVENT_PUBLISH_QUEUE_CAPACITY: usize = 8;

fn query_params(query: &str) -> std::collections::BTreeMap<String, String> {
    query
        .split('&')
        .filter_map(|part| part.split_once('='))
        .map(|(key, value)| (percent_decode(key), percent_decode(value)))
        .collect()
}

fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut result = String::with_capacity(value.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            if let Ok(decoded) = u8::from_str_radix(&value[index + 1..index + 3], 16) {
                result.push(decoded as char);
                index += 3;
                continue;
            }
        }
        result.push(bytes[index] as char);
        index += 1;
    }
    result
}

fn reference_query(params: &std::collections::BTreeMap<String, String>) -> ReferenceQuery {
    let kind = match params.get("kind").map(String::as_str) {
        Some("entity") => ReferenceKind::Entity,
        Some("asset") => ReferenceKind::Asset,
        Some("instrument") => ReferenceKind::Instrument,
        Some("listing") => ReferenceKind::Listing,
        Some("market") => ReferenceKind::Market,
        Some("financial-product") | Some("financial_product") => ReferenceKind::FinancialProduct,
        Some("execution-access") | Some("execution_access") | Some("access") => {
            ReferenceKind::ExecutionAccess
        }
        Some("event") => ReferenceKind::Event,
        _ => ReferenceKind::All,
    };
    ReferenceQuery {
        text: params.get("text").cloned(),
        kind,
        venue_id: params.get("venue_id").cloned(),
        market_type: params.get("market_type").cloned(),
        asset_type: params.get("asset_type").cloned(),
        underlying_instrument_id: params
            .get("underlying_instrument_id")
            .or_else(|| params.get("underlying"))
            .cloned(),
        status: params.get("status").cloned(),
        active_only: params
            .get("active_only")
            .is_some_and(|value| value == "true"),
        as_of_unix_nanos: params
            .get("as_of_unix_nanos")
            .and_then(|value| value.parse().ok()),
        sequence_from: params
            .get("sequence_from")
            .and_then(|value| value.parse().ok()),
        sequence_to: params
            .get("sequence_to")
            .and_then(|value| value.parse().ok()),
        event_time_from_unix_nanos: params
            .get("event_time_from_unix_nanos")
            .and_then(|value| value.parse().ok()),
        event_time_to_unix_nanos: params
            .get("event_time_to_unix_nanos")
            .and_then(|value| value.parse().ok()),
        limit: params.get("limit").and_then(|value| value.parse().ok()),
    }
}

fn market_query(params: &std::collections::BTreeMap<String, String>) -> MarketQuery {
    MarketQuery {
        market_id: params.get("market_id").cloned(),
        venue_id: params.get("venue_id").cloned(),
        market_type: params.get("market_type").cloned(),
        asset_type: params.get("asset_type").cloned(),
        source_symbol: params.get("symbol").cloned(),
        active_only: params
            .get("active_only")
            .is_some_and(|value| value == "true"),
        as_of_unix_nanos: None,
        status: params.get("status").cloned(),
    }
}

fn health_json(application: &ReferenceApplication, status: &str) -> Value {
    json!({
        "status": status,
        "pid": std::process::id(),
        "actor_id": application.actor_id(),
        "source_id": application.source_id(),
        "generation": application.catalog().generation,
        "event_sequence": application.catalog().event_sequence,
        "market_count": application.catalog().markets.len(),
        "providers": application.provider_health(),
    })
}

fn health_json_read_model(
    model: &ReferenceReadModel,
    status: &str,
    control_queue_depth: usize,
) -> Value {
    json!({
        "status": status,
        "pid": std::process::id(),
        "actor_id": model.actor_id(),
        "source_id": model.source_id(),
        "generation": model.catalog().generation,
        "event_sequence": model.catalog().event_sequence,
        "market_count": model.catalog().markets.len(),
        "providers": model.provider_health(),
        "outbox_depth": model.outbox_depth(),
        "control_queue_depth": control_queue_depth,
    })
}

fn reference_status(application: &ReferenceApplication) -> &'static str {
    if application
        .provider_health()
        .iter()
        .any(|provider| provider.stale)
    {
        "degraded"
    } else {
        "ready"
    }
}

async fn write_health(
    path: &Option<PathBuf>,
    application: &ReferenceApplication,
    status: &str,
) -> Result<(), std::io::Error> {
    let Some(path) = path else {
        return Ok(());
    };
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    tokio::fs::write(
        path,
        serde_json::to_vec(&health_json(application, status)).map_err(std::io::Error::other)?,
    )
    .await
}

fn parse_refresh_interval(value: &str) -> Result<Duration, String> {
    let value = value.trim();
    let (number, multiplier) = if let Some(value) = value.strip_suffix('s') {
        (value, 1u64)
    } else if let Some(value) = value.strip_suffix('m') {
        (value, 60)
    } else if let Some(value) = value.strip_suffix('h') {
        (value, 60 * 60)
    } else {
        (value, 1)
    };
    let seconds = number
        .parse::<u64>()
        .ok()
        .and_then(|value| value.checked_mul(multiplier))
        .filter(|value| *value > 0)
        .ok_or_else(|| {
            "refresh interval must be a positive duration such as 30s, 5m, or 1h".to_string()
        })?;
    Ok(Duration::from_secs(seconds))
}

#[derive(Debug, Parser)]
#[command(
    name = "kairos-reference",
    about = "Refresh and publish Reference data"
)]
struct Args {
    #[arg(long, default_value = "default", value_parser = kairos_reference::composition::parse_provider)]
    provider: String,
    #[arg(long)]
    endpoint: Option<String>,
    #[arg(long)]
    credential_id: Option<String>,
    #[arg(long)]
    database: Option<PathBuf>,
    #[arg(long)]
    api_key: Option<String>,
    #[arg(long, env = "BINANCE_API_KEY")]
    binance_api_key: Option<String>,
    #[arg(long, env = "BINANCE_API_SECRET")]
    secret: Option<String>,
    #[arg(long)]
    workspace: PathBuf,
    #[arg(long)]
    socket: Option<PathBuf>,
    #[arg(long = "health-file")]
    health_file: Option<PathBuf>,
    #[arg(
        long = "aeron-channel",
        default_value = kairos_transport::DEFAULT_CHANNEL
    )]
    aeron_channel: String,
    #[arg(
        long = "reference-changes-stream",
        default_value_t = kairos_transport::stream_ids::REFERENCE_CHANGES,
        value_parser = clap::value_parser!(i32).range(1..)
    )]
    reference_changes_stream: i32,
    #[arg(long)]
    aeron_dir: Option<String>,
    #[arg(
        long = "refresh-interval",
        default_value = "5m",
        value_parser = parse_refresh_interval
    )]
    refresh_interval: Duration,
    #[arg(long = "run-mode", default_value = "daemon", value_parser = ["daemon", "once"])]
    run_mode: String,
    #[arg(
        long = "snapshot-slot-size-mib",
        default_value_t = 64,
        value_parser = clap::value_parser!(u64).range(1..=4096)
    )]
    snapshot_slot_size_mib: u64,
}

#[cfg(test)]
mod tests {
    use super::{parse_refresh_interval, Args};
    use clap::Parser;
    use std::time::Duration;

    #[test]
    fn refresh_interval_accepts_human_units_and_legacy_seconds() {
        assert_eq!(
            parse_refresh_interval("15m").unwrap(),
            Duration::from_secs(900)
        );
        assert_eq!(
            parse_refresh_interval("1h").unwrap(),
            Duration::from_secs(3600)
        );
        assert_eq!(
            parse_refresh_interval("30").unwrap(),
            Duration::from_secs(30)
        );
        assert!(parse_refresh_interval("0s").is_err());
    }

    #[test]
    fn canonical_transport_options_parse_and_old_name_is_rejected() {
        let canonical = Args::try_parse_from([
            "kairos-reference",
            "--workspace",
            "/tmp/workspace",
            "--aeron-channel",
            "aeron:ipc",
            "--reference-changes-stream",
            "1301",
            "--snapshot-slot-size-mib",
            "32",
        ])
        .unwrap();
        assert_eq!(canonical.aeron_channel, "aeron:ipc");
        assert_eq!(
            canonical.reference_changes_stream,
            kairos_transport::stream_ids::REFERENCE_CHANGES
        );
        assert_eq!(canonical.snapshot_slot_size_mib, 32);

        assert!(Args::try_parse_from([
            "kairos-reference",
            "--workspace",
            "/tmp/workspace",
            "--channel",
            "aeron:ipc",
        ])
        .is_err());
    }
}
