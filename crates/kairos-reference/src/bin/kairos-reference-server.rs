use clap::Parser;
use kairos_reference::application::control;
use kairos_reference::application::MarketQuery;
use kairos_reference::application::{ReferenceApplication, ReferenceKind, ReferenceQuery};
use kairos_reference::composition::{
    build_application, default_endpoint, ensure_database_parent, ReferenceCompositionConfig,
    ReferenceSnapshotWriter,
};
use kairos_reference::domain::Asset;
use kairos_workspace::workspace::Workspace;
use serde_json::{json, Value};
use std::path::PathBuf;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    run()
}

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
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
    let provider = args.provider;
    let endpoint = args
        .endpoint
        .unwrap_or_else(|| default_endpoint(&provider).to_string());
    let api_key = args.api_key.unwrap_or_default();
    let binance_api_key = args.binance_api_key.unwrap_or_default();
    let secret = args.secret.unwrap_or_default();
    let database = args
        .database
        .unwrap_or(workspace.child(&["reference", "reference.sqlite"])?);
    if !database.starts_with(workspace.root()) {
        return Err("reference database must be inside workspace".into());
    }
    let channel = args.channel;
    let catalog_stream = args.catalog_stream;
    let markets_stream = args.markets_stream;
    let lifecycle_stream = args.lifecycle_stream;
    let changes_stream = args.changes_stream;
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
        aeron_dir: args.aeron_dir,
        channel,
        catalog_stream,
        markets_stream,
        lifecycle_stream,
        changes_stream,
    };
    let composition = build_application(&config, true)?;
    let mut application = composition.application;
    let mut snapshot_writer = composition.snapshot_writer;

    application.refresh()?;
    if let Some(writer) = snapshot_writer.as_mut() {
        writer.publish(application.catalog())?;
        let events = application.pending_events()?;
        writer.publish_change(application.catalog(), &events)?;
        application.acknowledge_published_events()?;
    }
    println!(
        "reference generation={} event_sequence={} events={}",
        application.catalog().generation,
        application.catalog().event_sequence,
        application.catalog().lifecycle_events.len()
    );
    if args.once {
        return Ok(());
    }

    let socket = args
        .socket
        .unwrap_or(workspace.process_socket("reference")?);
    if !socket.starts_with(workspace.root()) {
        return Err("reference socket must be inside workspace".into());
    }
    let health_file = args
        .health_file
        .or_else(|| workspace.health_file("reference").ok());
    let refresh_seconds = args.refresh_seconds;
    tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()?
        .block_on(run_process(
            application,
            snapshot_writer,
            socket,
            health_file,
            Duration::from_secs(refresh_seconds),
        ))
}

async fn run_process(
    mut application: ReferenceApplication,
    mut snapshot_writer: Option<ReferenceSnapshotWriter>,
    socket: PathBuf,
    health_file: Option<PathBuf>,
    refresh_interval: Duration,
) -> Result<(), Box<dyn std::error::Error>> {
    if let Some(parent) = socket.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let _ = std::fs::remove_file(&socket);
    let listener = UnixListener::bind(&socket)?;
    let mut interval = tokio::time::interval(refresh_interval);
    interval.tick().await;
    write_health(&health_file, &application, "ready").await?;
    let mut stopping = false;
    while !stopping {
        tokio::select! {
            accepted = listener.accept() => {
                let (stream, _) = accepted?;
                stopping = handle_client(&mut application, snapshot_writer.as_mut(), stream).await?;
            }
            _ = interval.tick() => {
                let status = match application.refresh() {
                    Ok(_) => {
                        if let Some(writer) = snapshot_writer.as_mut() {
                            writer.publish(application.catalog())?;
                            let events = application.pending_events()?;
                            writer.publish_change(application.catalog(), &events)?;
                            application.acknowledge_published_events()?;
                        }
                        "ready"
                    }
                    Err(_) => "degraded",
                };
                write_health(&health_file, &application, status).await?;
            }
        }
    }
    let _ = std::fs::remove_file(&socket);
    Ok(())
}

async fn handle_client(
    application: &mut ReferenceApplication,
    writer: Option<&mut ReferenceSnapshotWriter>,
    mut stream: UnixStream,
) -> Result<bool, Box<dyn std::error::Error>> {
    let mut buffer = vec![0; 16 * 1024];
    let size = stream.read(&mut buffer).await?;
    let request = String::from_utf8_lossy(&buffer[..size]);
    let target = request
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .unwrap_or("/");
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    let body = request
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .unwrap_or("");
    let params = query_params(query);
    let (status, body, stopping) = match path {
        control::HEALTH => (200, health_json(application, "ready"), false),
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
        control::QUERY | control::EVENTS => {
            let mut query = reference_query(&params);
            if path == control::EVENTS {
                query.kind = ReferenceKind::Event;
            }
            (200, serde_json::to_value(application.query(&query))?, false)
        }
        control::SHOW => {
            let identifier = params.get("identifier").ok_or("show requires identifier")?;
            match application.record(identifier) {
                Ok(record) => (200, serde_json::to_value(record)?, false),
                Err(error) => (404, json!({"error": error.to_string()}), false),
            }
        }
        control::REFRESH => match application.refresh() {
            Ok(result) => match publish_pending(writer, application) {
                Ok(events) => (
                    200,
                    json!({"generation": result.generation, "event_sequence": result.event_sequence, "events": events}),
                    false,
                ),
                Err(error) => (503, json!({"error": error.to_string()}), false),
            },
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
        control::ASSETS if request.starts_with("POST ") => {
            match serde_json::from_str::<Asset>(body) {
                Ok(asset) => match application.upsert_asset(asset) {
                    Ok(generation) => (200, json!({"generation": generation}), false),
                    Err(error) => (400, json!({"error": error.to_string()}), false),
                },
                Err(error) => (
                    400,
                    json!({"error": format!("invalid asset: {error}")}),
                    false,
                ),
            }
        }
        control::STOP => (202, json!({"status": "stopping"}), true),
        _ => (
            404,
            json!({"error": "unknown reference control path"}),
            false,
        ),
    };
    write_json(&mut stream, status, &body).await?;
    Ok(stopping)
}

fn publish(
    writer: Option<&mut ReferenceSnapshotWriter>,
    application: &ReferenceApplication,
    events: &[kairos_reference::domain::LifecycleEvent],
) -> kairos_reference::domain::ReferenceResult<()> {
    let writer = writer.ok_or_else(|| {
        kairos_reference::domain::ReferenceError::Publication(
            "reference publication is not configured".into(),
        )
    })?;
    writer.publish(application.catalog())?;
    writer.publish_change(application.catalog(), events)
}

fn publish_pending(
    writer: Option<&mut ReferenceSnapshotWriter>,
    application: &mut ReferenceApplication,
) -> kairos_reference::domain::ReferenceResult<usize> {
    let events = application.pending_events()?;
    let count = events.len();
    publish(writer, application, &events)?;
    application.acknowledge_published_events()?;
    Ok(count)
}

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

fn health_json(application: &ReferenceApplication, status: &str) -> Value {
    json!({
        "status": status,
        "actor_id": application.actor_id(),
        "source_id": application.source_id(),
        "generation": application.catalog().generation,
        "event_sequence": application.catalog().event_sequence,
        "market_count": application.catalog().markets.len(),
    })
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

async fn write_json(
    stream: &mut UnixStream,
    status: u16,
    value: &Value,
) -> Result<(), std::io::Error> {
    let body = serde_json::to_vec(value).map_err(std::io::Error::other)?;
    let reason = match status {
        200 => "OK",
        202 => "Accepted",
        404 => "Not Found",
        503 => "Service Unavailable",
        _ => "Error",
    };
    let header = format!("HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
    stream.write_all(header.as_bytes()).await?;
    stream.write_all(&body).await
}

#[derive(Debug, Parser)]
#[command(
    name = "kairos-reference",
    about = "Refresh and publish Reference data"
)]
struct Args {
    #[arg(long, default_value = "binance-spot")]
    provider: String,
    #[arg(long)]
    endpoint: Option<String>,
    #[arg(long)]
    database: Option<PathBuf>,
    #[arg(long, env = "MASSIVE_API_KEY")]
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
    #[arg(long, default_value = "aeron:udp?endpoint=localhost:40123")]
    channel: String,
    #[arg(long, default_value_t = 1201)]
    catalog_stream: i32,
    #[arg(long, default_value_t = 1202)]
    markets_stream: i32,
    #[arg(long, default_value_t = 1203)]
    lifecycle_stream: i32,
    #[arg(long, default_value_t = 1204)]
    changes_stream: i32,
    #[arg(long)]
    aeron_dir: Option<String>,
    #[arg(long, default_value_t = 300, value_parser = clap::value_parser!(u64).range(1..))]
    refresh_seconds: u64,
    #[arg(long, default_value_t = false)]
    once: bool,
}
