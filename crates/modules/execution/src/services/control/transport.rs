//! Axum/UDS transport and mailbox delivery for the Execution control plane.

use super::wire::parse_operation;
use super::*;
use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::any,
    Json, Router,
};
use serde_json::json;
use std::sync::atomic::Ordering;
use std::time::Instant;
use tracing::Instrument;

fn router(ingress: ControlIngress) -> Router {
    Router::new()
        .fallback(any(execution_http_handler))
        .with_state(ingress)
}

pub(crate) fn start(
    listener: tokio::net::UnixListener,
    ingress: ControlIngress,
) -> tokio::task::JoinHandle<Result<(), std::io::Error>> {
    tokio::spawn(async move { axum::serve(listener, router(ingress)).await })
}

async fn execution_http_handler(
    State(ingress): State<ControlIngress>,
    request: Request,
) -> Response {
    let started = Instant::now();
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
    let span = tracing::info_span!(
        "execution.control_request",
        component = "execution",
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
    let response = execution_http_handler_inner(ingress, request)
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
    if path == kairos_workspace::runtime::HEALTH_PATH && response.status().is_success() {
        tracing::debug!(parent: &span, event = "control_request_completed", component = "execution", duration_ms, result = "accepted", "execution health request completed");
    } else if response.status().is_success() {
        tracing::info!(parent: &span, event = "control_request_completed", component = "execution", duration_ms, result = "accepted", "execution control request completed");
    } else {
        tracing::warn!(parent: &span, event = "control_request_completed", component = "execution", duration_ms, result = "rejected", "execution control request failed");
    }
    response
}

async fn execution_http_handler_inner(ingress: ControlIngress, request: Request) -> Response {
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
        Ok(body) => body.to_vec(),
        Err(_) => {
            return (
                StatusCode::PAYLOAD_TOO_LARGE,
                Json(json!({"error":"request body too large"})),
            )
                .into_response()
        }
    };
    let operation = match parse_operation(&method, &target, &body) {
        Ok(operation) => operation,
        Err((status, payload)) => {
            return (
                StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST),
                Json(payload),
            )
                .into_response()
        }
    };
    let (response_sender, response_receiver) = oneshot::channel();
    let class = operation.class();
    let sender = match class {
        RequestClass::Command => &ingress.command_tx,
        RequestClass::Query => &ingress.query_tx,
    };
    match class {
        RequestClass::Command => {
            ingress
                .metrics
                .pending_commands
                .fetch_add(1, Ordering::Relaxed);
        }
        RequestClass::Query => {
            ingress
                .metrics
                .pending_queries
                .fetch_add(1, Ordering::Relaxed);
        }
    }
    if sender
        .try_send(ControlRequest {
            operation,
            response: response_sender,
        })
        .is_err()
    {
        match class {
            RequestClass::Command => {
                ingress
                    .metrics
                    .pending_commands
                    .fetch_sub(1, Ordering::Relaxed);
            }
            RequestClass::Query => {
                ingress
                    .metrics
                    .pending_queries
                    .fetch_sub(1, Ordering::Relaxed);
            }
        }
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"execution process is stopping"})),
        )
            .into_response();
    }
    match response_receiver.await {
        Ok(Ok(response)) => {
            let (status, payload) = response.into_wire();
            (
                StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
                Json(payload),
            )
                .into_response()
        }
        Ok(Err(error)) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error":error})),
        )
            .into_response(),
        Err(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"execution process did not respond"})),
        )
            .into_response(),
    }
}
