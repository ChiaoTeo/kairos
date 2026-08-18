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
use kairos_execution_contract::{
    ExecutionRestRequest, ExecutionRestResponse, ExecutionRoutesQuery,
};
use serde::de::DeserializeOwned;
use serde_json::json;
use tokio::net::UnixListener;

use crate::ExecutionApplication;

pub struct ExecutionHost {
    application: ExecutionApplication,
    system: ConfluxSystem,
    socket: PathBuf,
}

impl ExecutionHost {
    pub(crate) fn new(
        application: ExecutionApplication,
        system: ConfluxSystem,
        socket: PathBuf,
    ) -> Self {
        Self {
            application,
            system,
            socket,
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
            .fallback(execution_http_handler)
            .with_state(handle.clone());
        let server = tokio::spawn(async move { axum::serve(listener, router).await });
        let outcome = actor.await.map_err(|error| error.to_string())??;
        server.abort();
        let _ = server.await;
        remove_socket(&self.socket)?;
        tracing::info!(event = "process_stopped", component = "execution", phase = ?outcome.phase, discarded_inputs = outcome.discarded_inputs, "Execution Conflux process stopped");
        Ok(())
    }
}

async fn execution_http_handler(
    State(handle): State<ConfluxHandle<ExecutionApplication>>,
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
            "Execution Actor omitted REST response",
        ),
        Err(_) => error(
            StatusCode::SERVICE_UNAVAILABLE,
            "execution process is stopping",
        ),
    }
}

fn decode_request(
    method: &str,
    target: &str,
    body: &[u8],
) -> Result<Option<ExecutionRestRequest>, Response> {
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    match (method, path) {
        ("POST", "/v1/stop") => Ok(None),
        ("GET", "/v1/health") => Ok(Some(ExecutionRestRequest::Health)),
        ("GET", "/v1/routes") => Ok(Some(ExecutionRestRequest::Routes(routes_query(query)))),
        ("POST", "/v1/intents") => Ok(Some(ExecutionRestRequest::SubmitIntent(decode(body)?))),
        ("POST", "/v1/reconciliation") => Ok(Some(ExecutionRestRequest::Reconcile(decode(body)?))),
        ("DELETE", _) if path.starts_with("/v1/orders/") => {
            Ok(Some(ExecutionRestRequest::CancelOrder {
                order_id: path.trim_start_matches("/v1/orders/").to_owned(),
                request: decode_or_default(body)?,
            }))
        }
        ("PATCH", _) if path.starts_with("/v1/orders/") => {
            Ok(Some(ExecutionRestRequest::ReplaceOrder {
                order_id: path.trim_start_matches("/v1/orders/").to_owned(),
                request: decode(body)?,
            }))
        }
        _ => Err(error(StatusCode::NOT_FOUND, "unknown Execution endpoint")),
    }
}

fn routes_query(query: &str) -> ExecutionRoutesQuery {
    let value = |key: &str| {
        query.split('&').find_map(|part| {
            part.split_once('=')
                .filter(|(name, _)| *name == key)
                .map(|(_, value)| value.to_owned())
        })
    };
    ExecutionRoutesQuery {
        account_id: value("account_id"),
        segment_key: value("segment_key"),
        instrument_id: value("instrument_id"),
        market_id: value("market_id"),
        participant_id: value("participant_id"),
    }
}

fn decode<T: DeserializeOwned>(body: &[u8]) -> Result<T, Response> {
    serde_json::from_slice(body).map_err(|value| error(StatusCode::BAD_REQUEST, &value.to_string()))
}

fn decode_or_default<T: DeserializeOwned + Default>(body: &[u8]) -> Result<T, Response> {
    if body.is_empty() {
        Ok(T::default())
    } else {
        decode(body)
    }
}

fn encode_response(response: ExecutionRestResponse) -> Response {
    match response {
        ExecutionRestResponse::Health(Ok(value)) => {
            (StatusCode::OK, Json(json!(value))).into_response()
        }
        ExecutionRestResponse::Routes(Ok(value)) => {
            (StatusCode::OK, Json(json!(value))).into_response()
        }
        ExecutionRestResponse::SubmitIntent(Ok(value)) => {
            (StatusCode::CREATED, Json(json!(value))).into_response()
        }
        ExecutionRestResponse::CancelOrder(Ok(value))
        | ExecutionRestResponse::ReplaceOrder(Ok(value)) => {
            (StatusCode::ACCEPTED, Json(json!(value))).into_response()
        }
        ExecutionRestResponse::Reconcile(Ok(value)) => {
            (StatusCode::ACCEPTED, Json(json!(value))).into_response()
        }
        ExecutionRestResponse::Health(Err(value))
        | ExecutionRestResponse::Routes(Err(value))
        | ExecutionRestResponse::SubmitIntent(Err(value))
        | ExecutionRestResponse::CancelOrder(Err(value))
        | ExecutionRestResponse::ReplaceOrder(Err(value))
        | ExecutionRestResponse::Reconcile(Err(value)) => (
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
