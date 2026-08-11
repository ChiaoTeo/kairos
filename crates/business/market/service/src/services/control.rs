//! Private Unix HTTP control transport for the Market process.

use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::any,
    Json, Router,
};
use kairos_domain_types::Sequence;
use kairos_workspace::runtime::{HEALTH_PATH, SNAPSHOT_PATH, STOP_PATH};
use serde_json::{json, Value};
use std::time::Instant;
use tokio::net::UnixListener;
use tokio::sync::{mpsc::Sender, oneshot};
use tokio::task::JoinHandle;
use tracing::Instrument;

pub(crate) struct MarketHttpRequest {
    pub(crate) method: String,
    pub(crate) path: String,
    pub(crate) body: Vec<u8>,
    pub(crate) response: oneshot::Sender<MarketHttpResponse>,
}

pub(crate) struct MarketHttpResponse {
    pub(crate) status: u16,
    pub(crate) payload: Value,
}

pub(crate) enum EngineCommand {
    Http(MarketHttpRequest),
    ReferenceChanged { sequence: Sequence, gap: bool },
}

fn router(sender: Sender<EngineCommand>) -> Router {
    Router::new()
        .route(HEALTH_PATH, any(market_http_handler))
        .route(SNAPSHOT_PATH, any(market_http_handler))
        .route(STOP_PATH, any(market_http_handler))
        .route("/v1/subscribe", any(market_http_handler))
        .route("/v1/unsubscribe", any(market_http_handler))
        .route("/v1/recover", any(market_http_handler))
        .route("/v1/replay/pause", any(market_http_handler))
        .route("/v1/replay/resume", any(market_http_handler))
        .with_state(sender)
}

pub(crate) fn spawn_server(
    listener: UnixListener,
    sender: Sender<EngineCommand>,
) -> JoinHandle<Result<(), std::io::Error>> {
    let router = router(sender);
    tokio::spawn(async move {
        axum::serve(listener, router)
            .await
            .map_err(std::io::Error::other)
    })
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
            method: method.as_str().to_owned(),
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
        Ok(response) => (
            StatusCode::from_u16(response.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            Json(response.payload),
        )
            .into_response(),
        Err(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"market process did not respond"})),
        )
            .into_response(),
    }
}
