use std::path::{Path, PathBuf};

use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json, Router,
};
use kairos_conflux::{
    Conflux, ConfluxConfig, ConfluxEvent, ConfluxHandle, ConfluxSystem, ShutdownMode,
};
use kairos_market_contract::{MarketDataSourcesQuery, MarketRestRequest, MarketRestResponse};
use serde::de::DeserializeOwned;
use serde_json::json;
use tokio::net::UnixListener;

use crate::MarketApplication;

pub struct MarketHost {
    application: MarketApplication,
    system: ConfluxSystem,
    socket: PathBuf,
    health: Option<PathBuf>,
    _process_lock: kairos_workspace::workspace::WorkspaceProcessLock,
    _watcher: Option<super::reference::ReferenceWatcherGuard>,
}

impl MarketHost {
    pub(crate) fn new(
        application: MarketApplication,
        system: ConfluxSystem,
        socket: PathBuf,
        health: Option<PathBuf>,
        process_lock: kairos_workspace::workspace::WorkspaceProcessLock,
        watcher: Option<super::reference::ReferenceWatcherGuard>,
    ) -> Self {
        Self {
            application,
            system,
            socket,
            health,
            _process_lock: process_lock,
            _watcher: watcher,
        }
    }

    pub async fn run(self) -> Result<(), Box<dyn std::error::Error>> {
        remove_socket(&self.socket)?;
        if let Some(parent) = self.socket.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket)?;
        let (conflux, handle) = Conflux::new(
            self.application,
            self.system,
            ConfluxConfig {
                ingress_capacity: 1_024,
                ..ConfluxConfig::default()
            },
        )?;
        let actor = tokio::task::spawn_local(conflux.run());
        let router = Router::new()
            .fallback(market_http_handler)
            .with_state(handle.clone());
        let server = tokio::spawn(async move { axum::serve(listener, router).await });
        let startup = handle
            .handle(ConfluxEvent::Rest(MarketRestRequest::Health))
            .await
            .map_err(|_| "Market Conflux startup health failed")?;
        let status = match startup {
            Some(MarketRestResponse::Health(Ok(value))) => value.status,
            _ => return Err("Market Actor omitted startup health".into()),
        };
        write_health(self.health.as_deref(), &status).await?;
        let outcome = actor.await.map_err(|error| error.to_string())??;
        server.abort();
        let _ = server.await;
        remove_socket(&self.socket)?;
        write_health(self.health.as_deref(), "stopped").await?;
        tracing::info!(event = "process_stopped", component = "market", phase = ?outcome.phase, discarded_inputs = outcome.discarded_inputs, "Market Conflux process stopped");
        Ok(())
    }
}

async fn market_http_handler(
    State(handle): State<ConfluxHandle<MarketApplication>>,
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
        Ok(body) => body,
        Err(_) => return error(StatusCode::PAYLOAD_TOO_LARGE, "request body too large"),
    };
    let request = match decode_request(&method, &target, &body) {
        Ok(request) => request,
        Err(response) => return response,
    };
    let Some(request) = request else {
        handle.shutdown(ShutdownMode::Drain);
        return (StatusCode::ACCEPTED, Json(json!({"status":"stopping"}))).into_response();
    };
    match handle.handle(ConfluxEvent::Rest(request)).await {
        Ok(Some(response)) => encode_response(response),
        Ok(None) => error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Market Actor omitted REST response",
        ),
        Err(_) => error(
            StatusCode::SERVICE_UNAVAILABLE,
            "market process is stopping",
        ),
    }
}

fn decode_request(
    method: &str,
    target: &str,
    body: &[u8],
) -> Result<Option<MarketRestRequest>, Response> {
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    match (method, path) {
        ("POST", "/v1/stop") => Ok(None),
        ("GET", "/v1/health") => Ok(Some(MarketRestRequest::Health)),
        ("GET", "/v1/data-sources") => Ok(Some(MarketRestRequest::DataSources(
            data_sources_query(query),
        ))),
        ("POST", "/v1/subscribe") | ("POST", "/v1/subscriptions") => {
            Ok(Some(MarketRestRequest::Subscribe(decode(body)?)))
        }
        ("POST", "/v1/unsubscribe") | ("DELETE", _) if path.starts_with("/v1/subscriptions/") => {
            Ok(Some(MarketRestRequest::Unsubscribe(decode(body)?)))
        }
        ("POST", "/v1/subscriptions/release-owner") => {
            Ok(Some(MarketRestRequest::ReleaseOwner(decode(body)?)))
        }
        ("POST", "/v1/recover") | ("POST", "/v1/recovery") => Ok(Some(MarketRestRequest::Recover)),
        ("POST", "/v1/replay/pause") => Ok(Some(MarketRestRequest::PauseReplay)),
        ("POST", "/v1/replay/resume") => Ok(Some(MarketRestRequest::ResumeReplay)),
        _ => Err(error(StatusCode::NOT_FOUND, "unknown Market endpoint")),
    }
}

fn data_sources_query(query: &str) -> MarketDataSourcesQuery {
    let value = |key: &str| {
        query.split('&').find_map(|part| {
            part.split_once('=')
                .filter(|(name, _)| *name == key)
                .map(|(_, value)| value.to_owned())
        })
    };
    MarketDataSourcesQuery {
        market_id: value("market_id"),
        instrument_id: value("instrument_id"),
        exchange: value("exchange"),
        market_type: value("market_type"),
        asset_type: value("asset_type"),
    }
}

fn decode<T: DeserializeOwned>(body: &[u8]) -> Result<T, Response> {
    serde_json::from_slice(body).map_err(|value| error(StatusCode::BAD_REQUEST, &value.to_string()))
}

fn encode_response(response: MarketRestResponse) -> Response {
    match response {
        MarketRestResponse::Health(Ok(value)) => {
            (StatusCode::OK, Json(json!(value))).into_response()
        }
        MarketRestResponse::DataSources(Ok(value)) => {
            (StatusCode::OK, Json(json!(value))).into_response()
        }
        MarketRestResponse::Subscribe(Ok(value)) => {
            (StatusCode::CREATED, Json(json!(value))).into_response()
        }
        MarketRestResponse::Unsubscribe(Ok(value))
        | MarketRestResponse::Recover(Ok(value))
        | MarketRestResponse::PauseReplay(Ok(value))
        | MarketRestResponse::ResumeReplay(Ok(value)) => {
            (StatusCode::ACCEPTED, Json(json!(value))).into_response()
        }
        MarketRestResponse::ReleaseOwner(Ok(value)) => {
            (StatusCode::OK, Json(json!(value))).into_response()
        }
        MarketRestResponse::Health(Err(value))
        | MarketRestResponse::DataSources(Err(value))
        | MarketRestResponse::Subscribe(Err(value))
        | MarketRestResponse::Unsubscribe(Err(value))
        | MarketRestResponse::ReleaseOwner(Err(value))
        | MarketRestResponse::Recover(Err(value))
        | MarketRestResponse::PauseReplay(Err(value))
        | MarketRestResponse::ResumeReplay(Err(value)) => (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"error": value})),
        )
            .into_response(),
    }
}

fn error(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"error": message}))).into_response()
}

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(value) if value.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(value) => Err(value),
    }
}

async fn write_health(path: Option<&Path>, status: &str) -> Result<(), std::io::Error> {
    let Some(path) = path else {
        return Ok(());
    };
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let temporary = path.with_extension("tmp");
    tokio::fs::write(&temporary, serde_json::to_vec(&json!({"status": status}))?).await?;
    tokio::fs::rename(temporary, path).await
}
