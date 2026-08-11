use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json, Router,
};
use clap::Parser;
use kairos_protocol::InstanceIdentity;
use kairos_reference::application::control;
use kairos_reference::application::{ReferenceApplication, ReferenceReadModel};
use kairos_reference::composition::{
    build_application, ensure_database_parent, ReferenceCompositionConfig, ReferenceEventWriter,
    ReferenceEventWriterConfig, ReferenceMmapSnapshotConfig, ReferenceMmapSnapshotWriter,
};
use kairos_reference::domain::{Asset, Instrument, Listing};
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
use tracing::Instrument as TracingInstrument;

// Provider refreshes and SQL persistence run on the caller's Tokio runtime.
// The separate bounded Aeron publication worker below isolates the transport
// client without turning provider acquisition back into blocking work.
#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    kairos_workspace::logging::init("reference");
    let result = run().await;
    if let Err(error) = &result {
        tracing::error!(event = "process_failed", component = "reference", error = %error, "reference server failed");
    }
    kairos_workspace::logging::shutdown();
    if let Err(error) = result {
        eprintln!("kairos-reference-server: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    tracing::info!(
        event = "process_start",
        component = "reference",
        "starting reference server"
    );
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
    let database = workspace.child(&["reference", "reference.sqlite"])?;
    if args.reference_changes_stream <= 0 {
        return Err("reference event stream id must be positive".into());
    }
    let aeron_channel = args.aeron_channel;
    let aeron_dir = args.aeron_dir.clone();
    let reference_changes_stream = args.reference_changes_stream;
    tracing::info!(
        event = "reference_transport_config",
        component = "reference",
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
        database,
        aeron_dir: aeron_dir.clone(),
        aeron_channel: aeron_channel.clone(),
        reference_changes_stream,
    };
    let composition = build_application(&config, true).await?;
    let mut application = composition.application;
    let mut event_writer = composition.event_writer;

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
    let mut mmap_writer = ReferenceMmapSnapshotWriter::create(ReferenceMmapSnapshotConfig {
        catalog_path: snapshot_root.join("catalog.snapshot"),
        entities_path: snapshot_root.join("entities.snapshot"),
        assets_path: snapshot_root.join("assets.snapshot"),
        instruments_path: snapshot_root.join("instruments.snapshot"),
        listings_path: snapshot_root.join("listings.snapshot"),
        markets_path: snapshot_root.join("markets.snapshot"),
        financial_products_path: snapshot_root.join("financial-products.snapshot"),
        execution_accesses_path: snapshot_root.join("execution-accesses.snapshot"),
        slot_size: snapshot_slot_size,
        actor_id: "reference".into(),
        event_stream_id: "reference.lifecycle".into(),
        identity: InstanceIdentity::new(workspace.id(), "reference", "global"),
    })?;

    if args.run_mode == "once" {
        application.refresh().await?;
        mmap_writer.publish(application.catalog())?;
        if let Some(writer) = event_writer.as_mut() {
            loop {
                let events = application.pending_events(EVENT_BATCH_LIMIT).await?;
                if events.is_empty() {
                    break;
                }
                // Aeron is a best-effort notification stream. A successful
                // publish also includes the normal no-subscriber drop case;
                // the snapshot is the recovery source for late consumers.
                writer.publish(application.catalog(), &events)?;
                let event_ids = events
                    .iter()
                    .map(|event| event.event_id.clone())
                    .collect::<Vec<_>>();
                application.acknowledge_published_events(&event_ids).await?;
            }
        }
        tracing::info!(
            event = "initial_refresh_complete",
            component = "reference",
            generation = application.catalog().generation.get(),
            event_sequence = application.catalog().event_sequence.get(),
            events = application.catalog().lifecycle_events.len(),
            "reference catalog initialized"
        );
        println!(
            "reference generation={} event_sequence={} events={}",
            application.catalog().generation.get(),
            application.catalog().event_sequence.get(),
            application.catalog().lifecycle_events.len()
        );
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
        true,
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
    initial_refresh: bool,
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
    let initial_read_model = application.read_model().await;
    let read_model = Arc::new(RwLock::new(initial_read_model));
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
    kairos_workspace::logging::record_gauge("kairos.process.ready", 1);
    tracing::info!(event = "process_ready", component = "reference", socket = %socket.display(), "reference control socket ready");
    mmap_writer.publish(application.catalog())?;
    let initial_status = reference_status(&application);
    write_health(&health_file, &application, initial_status).await?;

    // The control plane is available before the first provider refresh. A
    // full Massive universe can require several paginated requests, and a
    // provider rate limit must not make the process appear dead.
    if initial_refresh {
        let refresh_started = Instant::now();
        let status = match refresh_cycle(
            &mut application,
            event_publisher.as_ref(),
            &mut mmap_writer,
        )
        .await
        {
            Ok(()) => reference_status(&application),
            Err(error) => {
                let provider_health = application.provider_health();
                let degraded_providers = provider_health
                    .iter()
                    .filter(|health| health.status != "ready" && health.status != "unknown")
                    .map(|health| health.source_id.as_str())
                    .collect::<Vec<_>>();
                tracing::warn!(
                    event = "initial_refresh_failed",
                    component = "reference",
                    source = %application.source_id(),
                    duration_ms = refresh_started.elapsed().as_millis() as u64,
                    provider_count = provider_health.len(),
                    degraded_provider_count = degraded_providers.len(),
                    stale_provider_count = provider_health.iter().filter(|health| health.stale).count(),
                    degraded_providers = ?degraded_providers,
                    fallback = "last_persisted_catalog",
                    error = %error,
                    "reference initial refresh failed; serving last persisted catalog"
                );
                "degraded"
            }
        };
        *read_model.write().await = application.read_model().await;
        *health_status.write().await = status.to_owned();
        write_health(&health_file, &application, status).await?;
    }
    let mut interval = tokio::time::interval_at(
        tokio::time::Instant::now() + refresh_interval,
        refresh_interval,
    );
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut publication_interval = tokio::time::interval(Duration::from_millis(50));
    publication_interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut publication_retry_after = tokio::time::Instant::now();
    let mut stopping = false;
    while !stopping {
        tokio::select! {
            Some(request) = receiver.recv() => {
                control_queue_depth.fetch_sub(1, Ordering::Relaxed);
                let response = handle_request(&mut application, event_publisher.as_ref(), &mut mmap_writer, &request.target, &String::from_utf8_lossy(&request.body)).await;
                if let Ok((should_stop, status, payload)) = &response {
                    stopping = *should_stop;
                    let _ = request.response.send(Ok((*should_stop, *status, payload.clone())));
                    let path = request.target.split_once('?').map_or(request.target.as_str(), |(path, _)| path);
                    if matches!(path, control::REFRESH | control::PUBLISH | control::ASSETS | control::INSTRUMENTS | control::LISTINGS | control::SOURCE_PAUSE | control::SOURCE_RESUME | control::OPTIONS_COVERAGE_ADD | control::OPTIONS_COVERAGE_REMOVE) {
                        let status = if *status < 400 { reference_status(&application) } else { "degraded" };
                        *health_status.write().await = status.to_owned();
                        let _ = write_health(&health_file, &application, status).await;
                    }
                } else {
                    let _ = request.response.send(response.map_err(|error| error.to_string()));
                }
                *read_model.write().await = application.read_model().await;
            }
            _ = interval.tick() => {
                let refresh_started = Instant::now();
                let status = match refresh_cycle(&mut application, event_publisher.as_ref(), &mut mmap_writer).await {
                    Ok(()) => reference_status(&application),
                    Err(error) => {
                        let provider_health = application.provider_health();
                        let degraded_providers = provider_health
                            .iter()
                            .filter(|health| health.status != "ready" && health.status != "unknown")
                            .map(|health| health.source_id.as_str())
                            .collect::<Vec<_>>();
                        tracing::warn!(
                            event = "refresh_failed",
                            component = "reference",
                            source = %application.source_id(),
                            duration_ms = refresh_started.elapsed().as_millis() as u64,
                            provider_count = provider_health.len(),
                            degraded_provider_count = degraded_providers.len(),
                            stale_provider_count = provider_health.iter().filter(|health| health.stale).count(),
                            degraded_providers = ?degraded_providers,
                            fallback = if provider_health.iter().any(|health| health.stale) { "last_known_good" } else { "none" },
                            error = %error,
                            "reference refresh failed"
                        );
                        "degraded"
                    }
                };
                *read_model.write().await = application.read_model().await;
                *health_status.write().await = status.to_owned();
                write_health(&health_file, &application, status).await?;
            }
            _ = publication_interval.tick(), if event_publisher.is_some() => {
                if tokio::time::Instant::now() >= publication_retry_after {
                    if let Err(error) = publish_pending_batch(event_publisher.as_ref(), &mut application).await {
                        publication_retry_after = tokio::time::Instant::now() + Duration::from_secs(1);
                        tracing::debug!(
                            event = "reference_pending_publication_deferred",
                            component = "reference",
                            error = %error,
                            "reference pending publication remains durable for a later retry"
                        );
                    }
                }
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
    generation: kairos_domain_types::Generation,
    event_sequence: kairos_domain_types::Sequence,
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
                    let catalog = kairos_reference::domain::ReferenceCatalog {
                        generation: request.generation,
                        event_sequence: request.event_sequence,
                        ..Default::default()
                    };
                    let result = writer
                        .publish(&catalog, &request.events)
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
        let receiver = self.enqueue(catalog, events)?;
        receiver
            .recv()
            .map_err(|error| {
                kairos_reference::domain::ReferenceError::Publication(error.to_string())
            })?
            .map_err(kairos_reference::domain::ReferenceError::Publication)
    }

    fn enqueue(
        &self,
        catalog: &kairos_reference::domain::ReferenceCatalog,
        events: &[kairos_reference::domain::LifecycleEvent],
    ) -> kairos_reference::domain::ReferenceResult<std_mpsc::Receiver<Result<(), String>>> {
        let (response, receiver) = std_mpsc::sync_channel(1);
        match self.requests.try_send(PublishRequest {
            generation: catalog.generation,
            event_sequence: catalog.event_sequence,
            events: events.to_vec(),
            response,
        }) {
            Ok(()) => Ok(receiver),
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
    let started = Instant::now();
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
    let span = tracing::info_span!(
        "reference.control_request",
        component = "reference",
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
    let response = reference_http_handler_inner(state, request)
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
    tracing::info!(parent: &span, event = "control_request_completed", component = "reference", duration_ms, result = if response.status().is_success() { "accepted" } else { "rejected" }, "reference control request completed");
    response
}

async fn reference_http_handler_inner(state: ReferenceServerState, request: Request) -> Response {
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

async fn handle_request(
    application: &mut ReferenceApplication,
    writer: Option<&EventPublisherRuntime>,
    mmap_writer: &mut ReferenceMmapSnapshotWriter,
    target: &str,
    body: &str,
) -> Result<(bool, u16, Value), Box<dyn std::error::Error>> {
    let started = Instant::now();
    tracing::info!(event = "control_request", component = "reference", path = %target, "reference control request received");
    let path = target.split_once('?').map_or(target, |(path, _)| path);
    let (status, body, stopping) = match path {
        control::HEALTH => (200, health_json(application, "ready"), false),
        control::PROVIDERS => (
            200,
            json!({
                "mode": application.source_id(),
                "source_id": application.source_id(),
                "providers": application.provider_health(),
            }),
            false,
        ),
        control::EVENTS => {
            let sequence_from = query_u64(target, "sequence_from")?;
            let sequence_to = query_u64(target, "sequence_to")?;
            let limit = query_u64(target, "limit")?.unwrap_or(256).clamp(1, 4096) as usize;
            match application
                .lifecycle_events_page(sequence_from, sequence_to, limit)
                .await
            {
                Ok(events) => (
                    200,
                    json!({
                        "generation": application.catalog().generation.get(),
                        "event_sequence": application.catalog().event_sequence.get(),
                        "events": events,
                    }),
                    false,
                ),
                Err(error) => (503, json!({"error": error.to_string()}), false),
            }
        }
        control::REFRESH => match match query_value(target, "source") {
            Some(source_id) => application.refresh_source(source_id).await,
            None => application.refresh().await,
        } {
            Ok(result) => {
                let publication = publish_pending(writer, application).await;
                let snapshot = publish_snapshot(mmap_writer, application);
                match snapshot {
                    Ok(()) => {
                        let (events, publication_pending, publication_error) = match publication {
                            Ok(events) => (events, false, None),
                            Err(error) => {
                                tracing::debug!(
                                    event = "reference_publication_deferred",
                                    component = "reference",
                                    error = %error,
                                    "reference snapshot committed while durable events await a subscriber"
                                );
                                (0, true, Some(error.to_string()))
                            }
                        };
                        (
                            200,
                            json!({
                                "generation": result.generation,
                                "event_sequence": result.event_sequence,
                                "events": events,
                                "publication_pending": publication_pending,
                                "publication_error": publication_error,
                            }),
                            false,
                        )
                    }
                    Err(error) => (503, json!({"error": error.to_string()}), false),
                }
            }
            Err(error) => (503, json!({"error": error.to_string()}), false),
        },
        control::PUBLISH => match publish_pending(writer, application).await {
            Ok(events) => (
                200,
                json!({"generation": application.catalog().generation.get(), "events": events}),
                false,
            ),
            Err(error) => (503, json!({"error": error.to_string()}), false),
        },
        control::SOURCE_PAUSE | control::SOURCE_RESUME => {
            let Some(source_id) = query_value(target, "source") else {
                return Err("source is required".into());
            };
            let paused = path == control::SOURCE_PAUSE;
            match application.set_source_paused(source_id, paused).await {
                Ok(()) => (
                    200,
                    json!({"source_id": source_id, "status": if paused { "paused" } else { "resumed" }}),
                    false,
                ),
                Err(error) => (400, json!({"error": error.to_string()}), false),
            }
        }
        control::OPTIONS_COVERAGE => (
            200,
            json!({"source_id": "massive-options", "underlyings": application.option_underlyings()}),
            false,
        ),
        control::OPTIONS_COVERAGE_ADD | control::OPTIONS_COVERAGE_REMOVE => {
            let Some(underlying) = query_value(target, "underlying") else {
                return Err("underlying is required".into());
            };
            let enabled = path == control::OPTIONS_COVERAGE_ADD;
            match application.set_option_underlying(underlying, enabled).await {
                Ok(result) => (
                    200,
                    json!({
                        "source_id": "massive-options",
                        "underlying": underlying,
                        "enabled": enabled,
                        "underlyings": application.option_underlyings(),
                        "generation": result.generation,
                        "event_sequence": result.event_sequence,
                        "changed": result.changed,
                    }),
                    false,
                ),
                Err(error) => (400, json!({"error": error.to_string()}), false),
            }
        }
        control::ASSETS => match serde_json::from_str::<Asset>(body) {
            Ok(asset) => match application.upsert_asset(asset).await {
                Ok(generation) => match publish_snapshot(mmap_writer, application) {
                    Ok(()) => match publish_pending(writer, application).await {
                        Ok(events) => (
                            200,
                            json!({"generation": generation, "events": events}),
                            false,
                        ),
                        Err(error) => (503, json!({"error": error.to_string()}), false),
                    },
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
        control::INSTRUMENTS => match serde_json::from_str::<Instrument>(body) {
            Ok(instrument) => match application.upsert_instrument(instrument).await {
                Ok(generation) => match publish_snapshot(mmap_writer, application) {
                    Ok(()) => match publish_pending(writer, application).await {
                        Ok(events) => (
                            200,
                            json!({"generation": generation, "events": events}),
                            false,
                        ),
                        Err(error) => (503, json!({"error": error.to_string()}), false),
                    },
                    Err(error) => (503, json!({"error": error.to_string()}), false),
                },
                Err(error) => (400, json!({"error": error.to_string()}), false),
            },
            Err(error) => (
                400,
                json!({"error": format!("invalid instrument: {error}")}),
                false,
            ),
        },
        control::LISTINGS => match serde_json::from_str::<Listing>(body) {
            Ok(listing) => match application.upsert_listing(listing).await {
                Ok(generation) => match publish_snapshot(mmap_writer, application) {
                    Ok(()) => match publish_pending(writer, application).await {
                        Ok(events) => (
                            200,
                            json!({"generation": generation, "events": events}),
                            false,
                        ),
                        Err(error) => (503, json!({"error": error.to_string()}), false),
                    },
                    Err(error) => (503, json!({"error": error.to_string()}), false),
                },
                Err(error) => (400, json!({"error": error.to_string()}), false),
            },
            Err(error) => (
                400,
                json!({"error": format!("invalid listing: {error}")}),
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

fn query_u64(target: &str, name: &str) -> Result<Option<u64>, Box<dyn std::error::Error>> {
    let Some(value) = query_value(target, name) else {
        return Ok(None);
    };
    value
        .parse::<u64>()
        .map(Some)
        .map_err(|error| format!("invalid {name}: {error}").into())
}

fn query_value<'a>(target: &'a str, name: &str) -> Option<&'a str> {
    let Some((_, query)) = target.split_once('?') else {
        return None;
    };
    query.split('&').find_map(|pair| {
        let (key, value) = pair.split_once('=')?;
        (key == name).then_some(value)
    })
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
        generation = application.catalog().generation.get(),
        event_sequence = application.catalog().event_sequence.get(),
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

async fn publish_pending(
    writer: Option<&EventPublisherRuntime>,
    application: &mut ReferenceApplication,
) -> kairos_reference::domain::ReferenceResult<usize> {
    let count = publish_pending_batch(writer, application).await?;
    tracing::info!(
        event = "reference_pending_events_published",
        component = "reference",
        event_count = count,
        "reference pending events published"
    );
    Ok(count)
}

async fn publish_pending_batch(
    writer: Option<&EventPublisherRuntime>,
    application: &mut ReferenceApplication,
) -> kairos_reference::domain::ReferenceResult<usize> {
    let events = application.pending_events(EVENT_BATCH_LIMIT).await?;
    if events.is_empty() {
        return Ok(0);
    }
    publish(writer, application, &events)?;
    let event_ids = events
        .iter()
        .map(|event| event.event_id.clone())
        .collect::<Vec<_>>();
    application.acknowledge_published_events(&event_ids).await?;
    Ok(events.len())
}

fn publish_snapshot(
    mmap_writer: &mut ReferenceMmapSnapshotWriter,
    application: &ReferenceApplication,
) -> kairos_reference::domain::ReferenceResult<()> {
    kairos_workspace::logging::record_gauge(
        "kairos.snapshot.generation",
        application.catalog().generation.get(),
    );
    mmap_writer
        .publish(application.catalog())
        .map_err(|error| kairos_reference::domain::ReferenceError::Publication(error.to_string()))
}

async fn refresh_cycle(
    application: &mut ReferenceApplication,
    publisher: Option<&EventPublisherRuntime>,
    mmap_writer: &mut ReferenceMmapSnapshotWriter,
) -> kairos_reference::domain::ReferenceResult<()> {
    application.refresh().await?;
    mmap_writer
        .publish(application.catalog())
        .map_err(|error| {
            kairos_reference::domain::ReferenceError::Publication(error.to_string())
        })?;
    if let Err(error) = publish_pending(publisher, application).await {
        tracing::debug!(
            event = "reference_publication_deferred",
            component = "reference",
            error = %error,
            "reference snapshot committed while durable events await a subscriber"
        );
    }
    Ok(())
}

const EVENT_BATCH_LIMIT: usize = 1024;
const CONTROL_QUEUE_CAPACITY: usize = 64;
const EVENT_PUBLISH_QUEUE_CAPACITY: usize = 8;

fn health_json(application: &ReferenceApplication, status: &str) -> Value {
    json!({
        "status": status,
        "pid": std::process::id(),
        "actor_id": application.actor_id(),
        "source_id": application.source_id(),
        "generation": application.catalog().generation.get(),
        "event_sequence": application.catalog().event_sequence.get(),
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
        "generation": model.catalog().generation.get(),
        "event_sequence": model.catalog().event_sequence.get(),
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
    use super::{parse_refresh_interval, query_u64, Args};
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
    fn lifecycle_query_reads_stable_sequence_bounds() {
        let target = "/v1/events?sequence_from=41&sequence_to=50&limit=9";
        assert_eq!(query_u64(target, "sequence_from").unwrap(), Some(41));
        assert_eq!(query_u64(target, "sequence_to").unwrap(), Some(50));
        assert_eq!(query_u64(target, "limit").unwrap(), Some(9));
        assert_eq!(query_u64(target, "missing").unwrap(), None);
        assert!(query_u64("/v1/events?limit=invalid", "limit").is_err());
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
            "1201",
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
