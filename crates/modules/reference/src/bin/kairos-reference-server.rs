use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json, Router,
};
use clap::Parser;
use kairos_conflux::{
    Conflux, ConfluxConfig, ConfluxEvent, ConfluxHandle, ConfluxSystem, ShutdownMode,
};
use kairos_reference::application::control;
use kairos_reference::composition::{
    build_application, ensure_database_parent, ReferenceCompositionConfig,
};
use kairos_reference::ReferenceApplication;
use kairos_reference_contract::{
    AeronEndpoint, ReferenceControlError, ReferenceEventPublisher, ReferenceOptionCoverageRequest,
    ReferenceRestRequest, ReferenceRestResponse, ReferenceSourceControlRequest,
};
use kairos_workspace::workspace::Workspace;
use serde::Serialize;
use serde_json::json;
use tokio::net::UnixListener;
use tokio::task::LocalSet;
use tracing::Instrument as _;

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    kairos_workspace::logging::init("reference");
    let result = LocalSet::new().run_until(run()).await;
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
    let workspace = Workspace::open(args.workspace)?;
    validate_workspace_path(workspace.root(), args.socket.as_deref(), "socket")?;
    validate_workspace_path(workspace.root(), args.health_file.as_deref(), "health file")?;
    let _process_lock = workspace.process_lock("reference").map_err(|error| {
        if error.kind() == std::io::ErrorKind::AlreadyExists {
            "reference is already running for this workspace".to_string()
        } else {
            format!("acquire reference workspace lock: {error}")
        }
    })?;

    let database = workspace.child(&["state", "reference", "reference.sqlite"])?;
    ensure_database_parent(&database)?;
    let config = ReferenceCompositionConfig {
        workspace: Some(workspace.root().to_path_buf()),
        database,
        aeron_dir: args.aeron_dir.clone(),
        aeron_channel: args.aeron_channel.clone(),
        reference_changes_stream: args.reference_changes_stream,
    };

    if args.run_mode == "once" {
        return run_once(&config).await;
    }

    let socket = args
        .socket
        .unwrap_or(workspace.process_socket("reference")?);
    let health_file = args
        .health_file
        .or_else(|| workspace.health_file("reference").ok());
    let composition = build_application(&config, false).await?;
    let (mut application, mut system, _) = composition.into_conflux();
    application.configure_conflux(args.refresh_interval, true);

    let event_endpoint = AeronEndpoint::from_parts(
        config.aeron_dir.as_deref(),
        config.aeron_channel.clone(),
        config.reference_changes_stream,
    )?;
    let publisher = ReferenceEventPublisher::connect(&event_endpoint)?;
    system
        .reference_event_publishers
        .ensure_with("reference-changes".to_owned(), 1, || publisher)?;

    run_process(application, system, socket, health_file).await
}

async fn run_once(config: &ReferenceCompositionConfig) -> Result<(), Box<dyn std::error::Error>> {
    let mut composition = build_application(config, true).await?;
    composition.activate_sources().await?;
    let (mut application, mut system, event_writer) = composition.into_conflux();
    let mut writer = event_writer.ok_or("reference publication is not configured")?;
    let refresh = application
        .refresh_with_connections(&mut system.connections())
        .await?;
    loop {
        let publications = application.pending_publications(1_024).await?;
        if publications.is_empty() {
            break;
        }
        writer.publish(&publications)?;
        let event_ids = publications
            .iter()
            .map(|event| event.event_id().to_owned())
            .collect::<Vec<_>>();
        application.acknowledge_publications(&event_ids).await?;
    }
    println!(
        "reference generation={} event_sequence={} events={}",
        application.generation().get(),
        application.event_sequence().get(),
        refresh.events.len()
    );
    Ok(())
}

async fn run_process(
    application: ReferenceApplication,
    system: ConfluxSystem,
    socket: PathBuf,
    health_file: Option<PathBuf>,
) -> Result<(), Box<dyn std::error::Error>> {
    remove_socket(&socket)?;
    if let Some(parent) = socket.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let listener = UnixListener::bind(&socket)?;
    let (conflux, handle) = Conflux::new(
        application,
        system,
        ConfluxConfig {
            ingress_capacity: 256,
            ..ConfluxConfig::default()
        },
    )?;
    let process = tokio::task::spawn_local(conflux.run());
    let router = Router::new()
        .fallback(reference_http_handler)
        .with_state(ReferenceHost {
            handle: handle.clone(),
        });
    let server = tokio::spawn(async move { axum::serve(listener, router).await });

    let startup = handle
        .handle(ConfluxEvent::Rest(ReferenceRestRequest::Health))
        .await
        .map_err(|_| "Reference Conflux startup health failed")?;
    let status = match startup {
        Some(ReferenceRestResponse::Health(Ok(health))) => health.status,
        _ => return Err("Reference Actor omitted its startup health response".into()),
    };
    write_health(health_file.as_deref(), &status).await?;
    kairos_workspace::logging::record_gauge("kairos.process.ready", 1);

    let outcome = process.await.map_err(|error| error.to_string())??;
    server.abort();
    let _ = server.await;
    remove_socket(&socket)?;
    write_health(health_file.as_deref(), "stopped").await?;
    tracing::info!(
        event = "process_stopped",
        component = "reference",
        phase = ?outcome.phase,
        discarded_inputs = outcome.discarded_inputs,
        "Reference Conflux process stopped"
    );
    Ok(())
}

#[derive(Clone)]
struct ReferenceHost {
    handle: ConfluxHandle<ReferenceApplication>,
}

async fn reference_http_handler(State(host): State<ReferenceHost>, request: Request) -> Response {
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
    );
    kairos_workspace::logging::set_remote_parent(&span, request.headers());
    let response = reference_http_handler_inner(host, request)
        .instrument(span.clone())
        .await;
    span.record("status", response.status().as_u16());
    span.record("duration_ms", started.elapsed().as_secs_f64() * 1_000.0);
    response
}

async fn reference_http_handler_inner(host: ReferenceHost, request: Request) -> Response {
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
        Ok(body) => body,
        Err(_) => return json_error(StatusCode::PAYLOAD_TOO_LARGE, "request body too large"),
    };
    let request = match decode_request(&method, &target, &body) {
        Ok(request) => request,
        Err(response) => return response,
    };
    match request {
        HostRequest::Stop => {
            host.handle.shutdown(ShutdownMode::Drain);
            (StatusCode::ACCEPTED, Json(json!({"status":"stopping"}))).into_response()
        }
        HostRequest::Rest(request) => match host.handle.handle(ConfluxEvent::Rest(request)).await {
            Ok(Some(response)) => encode_response(response),
            Ok(None) => json_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Reference Actor omitted its REST response",
            ),
            Err(_) => json_error(
                StatusCode::SERVICE_UNAVAILABLE,
                "reference process is stopping",
            ),
        },
    }
}

enum HostRequest {
    Rest(ReferenceRestRequest),
    Stop,
}

fn decode_request(method: &str, target: &str, body: &[u8]) -> Result<HostRequest, Response> {
    let path = target.split_once('?').map_or(target, |(path, _)| path);
    if path == control::STOP {
        return if method == "POST" {
            Ok(HostRequest::Stop)
        } else {
            Err(json_error(
                StatusCode::METHOD_NOT_ALLOWED,
                "stop accepts only POST",
            ))
        };
    }
    if path == control::HEALTH {
        return if method == "GET" {
            Ok(HostRequest::Rest(ReferenceRestRequest::Health))
        } else {
            Err(json_error(
                StatusCode::METHOD_NOT_ALLOWED,
                "health accepts only GET",
            ))
        };
    }
    if method != "POST" {
        return Err(json_error(
            StatusCode::METHOD_NOT_ALLOWED,
            "Reference business queries use the contract-owned SQLite client",
        ));
    }
    let request = match path {
        control::REFRESH => ReferenceRestRequest::Refresh {
            source_id: query_value(target, "source").map(str::to_owned),
        },
        control::PUBLISH => ReferenceRestRequest::Publish,
        control::SOURCE_PAUSE => ReferenceRestRequest::PauseSource(ReferenceSourceControlRequest {
            source_id: required_query(target, "source")?,
        }),
        control::SOURCE_RESUME => {
            ReferenceRestRequest::ResumeSource(ReferenceSourceControlRequest {
                source_id: required_query(target, "source")?,
            })
        }
        control::OPTIONS_COVERAGE_ADD => {
            ReferenceRestRequest::AddOptionCoverage(ReferenceOptionCoverageRequest {
                underlying: required_query(target, "underlying")?,
            })
        }
        control::OPTIONS_COVERAGE_REMOVE => {
            ReferenceRestRequest::RemoveOptionCoverage(ReferenceOptionCoverageRequest {
                underlying: required_query(target, "underlying")?,
            })
        }
        control::ASSETS => ReferenceRestRequest::UpsertAsset(decode(body)?),
        control::INSTRUMENTS => ReferenceRestRequest::UpsertInstrument(decode(body)?),
        control::LISTINGS => ReferenceRestRequest::UpsertListing(decode(body)?),
        _ => {
            return Err(json_error(
                StatusCode::NOT_FOUND,
                "unknown Reference control path",
            ))
        }
    };
    Ok(HostRequest::Rest(request))
}

fn decode<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T, Response> {
    serde_json::from_slice(body).map_err(|error| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"error":"invalid Reference request", "details":error.to_string()})),
        )
            .into_response()
    })
}

fn encode_response(response: ReferenceRestResponse) -> Response {
    match response {
        ReferenceRestResponse::Health(result) => result_response(result),
        ReferenceRestResponse::Refresh(result) => result_response(result),
        ReferenceRestResponse::Publish(result) => result_response(result),
        ReferenceRestResponse::PauseSource(result) => result_response(result),
        ReferenceRestResponse::ResumeSource(result) => result_response(result),
        ReferenceRestResponse::AddOptionCoverage(result) => result_response(result),
        ReferenceRestResponse::RemoveOptionCoverage(result) => result_response(result),
        ReferenceRestResponse::UpsertAsset(result) => result_response(result),
        ReferenceRestResponse::UpsertInstrument(result) => result_response(result),
        ReferenceRestResponse::UpsertListing(result) => result_response(result),
    }
}

fn result_response<T: Serialize>(result: Result<T, ReferenceControlError>) -> Response {
    match result {
        Ok(value) => (StatusCode::OK, Json(json!(value))).into_response(),
        Err(error) => {
            let status = if error.retryable {
                StatusCode::SERVICE_UNAVAILABLE
            } else {
                StatusCode::BAD_REQUEST
            };
            (status, Json(json!({"error": error}))).into_response()
        }
    }
}

fn query_value<'a>(target: &'a str, name: &str) -> Option<&'a str> {
    let (_, query) = target.split_once('?')?;
    query.split('&').find_map(|pair| {
        let (key, value) = pair.split_once('=')?;
        (key == name).then_some(value)
    })
}

fn required_query(target: &str, name: &str) -> Result<String, Response> {
    query_value(target, name).map(str::to_owned).ok_or_else(|| {
        json_error(
            StatusCode::UNPROCESSABLE_ENTITY,
            &format!("{name} is required"),
        )
    })
}

fn json_error(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"error":message}))).into_response()
}

fn validate_workspace_path(
    workspace: &Path,
    path: Option<&Path>,
    label: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    if path.is_some_and(|path| !path.starts_with(workspace)) {
        return Err(format!("reference {label} must be inside workspace").into());
    }
    Ok(())
}

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

async fn write_health(path: Option<&Path>, status: &str) -> Result<(), std::io::Error> {
    let Some(path) = path else {
        return Ok(());
    };
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    tokio::fs::write(
        path,
        serde_json::to_vec(&json!({"status":status, "pid":std::process::id()}))
            .map_err(std::io::Error::other)?,
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
    #[arg(long = "aeron-channel", default_value = kairos_transport::DEFAULT_CHANNEL)]
    aeron_channel: String,
    #[arg(
        long = "reference-changes-stream",
        default_value_t = kairos_transport::stream_ids::REFERENCE_CHANGES,
        value_parser = clap::value_parser!(i32).range(1..)
    )]
    reference_changes_stream: i32,
    #[arg(long)]
    aeron_dir: Option<String>,
    #[arg(long = "refresh-interval", default_value = "5m", value_parser = parse_refresh_interval)]
    refresh_interval: Duration,
    #[arg(long = "run-mode", default_value = "daemon", value_parser = ["daemon", "once"])]
    run_mode: String,
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
            "1201",
        ])
        .unwrap();
        assert_eq!(canonical.aeron_channel, "aeron:ipc");
        assert_eq!(
            canonical.reference_changes_stream,
            kairos_transport::stream_ids::REFERENCE_CHANGES
        );
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
