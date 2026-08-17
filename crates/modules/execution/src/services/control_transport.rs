//! Private HTTP-over-Unix-socket transport for the Execution control plane.
//!
//! This adapter owns raw HTTP request parsing, bounded body reads, ingress
//! classification and HTTP response encoding. The application process only
//! receives an internal mailbox request.

use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::any,
    Json, Router,
};
use kairos_workspace::runtime::STOP_PATH;
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::SyncSender;
use std::time::Instant;
use tokio::sync::oneshot;
use tracing::Instrument;

use crate::application::market_input::MarketObservation;
use crate::application::{
    BacktestRequest, CancelIntent, CancelOrder, ExecuteStrategyIntent, ExecutionFillReport,
    ExecutionOrderOptions, ExpireIntent, RefreshQuoteIntent, RemoteOrderQuery, SubmitOrder,
};
use kairos_primitives::{OrderId, Price, Quantity};

#[derive(serde::Deserialize)]
struct CommandEnvelope<T> {
    schema_version: u16,
    operation: String,
    instance_id: String,
    payload: T,
}

#[derive(serde::Deserialize)]
struct ReplaceOrderPatchWire {
    #[serde(default)]
    quantity: Option<Quantity>,
    #[serde(default)]
    options: ExecutionOrderOptions,
}

pub(crate) struct ReplaceOrderPatch {
    pub(crate) quantity: Option<Quantity>,
    pub(crate) limit_price: Option<Option<Price>>,
    pub(crate) options: ExecutionOrderOptions,
}

pub(crate) enum ControlOperation {
    Health,
    AdvanceTime(u64),
    SubmitIntent {
        intent: ExecuteStrategyIntent,
        idempotency_key: String,
    },
    SubmitOrder(SubmitOrder),
    CancelOrder(CancelOrder),
    ReplaceOrder {
        order_id: OrderId,
        patch: ReplaceOrderPatch,
    },
    Reconcile(RemoteOrderQuery),
    LinkUnknownRemote {
        remote_order_id: String,
        local_order_id: String,
    },
    EvaluateBacktest(BacktestRequest),
    RunBacktest(BacktestRequest),
    ApplyBacktestMarket(MarketObservation),
    CancelIntent(CancelIntent),
    ExpireIntent(ExpireIntent),
    RefreshQuote(RefreshQuoteIntent),
    PreviewSubmit(SubmitOrder),
    RecordFill(ExecutionFillReport),
    Stop,
}

impl ControlOperation {
    fn class(&self) -> RequestClass {
        match self {
            Self::Health => RequestClass::Query,
            _ => RequestClass::Command,
        }
    }
}

pub(crate) struct ControlRequest {
    pub(crate) operation: ControlOperation,
    pub(crate) response: oneshot::Sender<Result<(u16, Value), String>>,
}

#[derive(Default)]
pub(crate) struct RuntimeMetrics {
    pub(crate) pending_commands: AtomicUsize,
    pub(crate) pending_queries: AtomicUsize,
    pub(crate) pending_exchange_events: AtomicUsize,
    pub(crate) exchange_events_applied: AtomicU64,
    pub(crate) exchange_batches: AtomicU64,
    pub(crate) max_exchange_batch: AtomicUsize,
    pub(crate) state_loop_errors: AtomicU64,
    pub(crate) last_operation_micros: AtomicU64,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(crate) enum RequestClass {
    Command,
    Query,
}

#[derive(Clone)]
pub(crate) struct ControlIngress {
    command_tx: SyncSender<ControlRequest>,
    query_tx: SyncSender<ControlRequest>,
    metrics: std::sync::Arc<RuntimeMetrics>,
}

impl ControlIngress {
    pub(crate) fn new(
        command_tx: SyncSender<ControlRequest>,
        query_tx: SyncSender<ControlRequest>,
        metrics: std::sync::Arc<RuntimeMetrics>,
    ) -> Self {
        Self {
            command_tx,
            query_tx,
            metrics,
        }
    }
}

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

#[cfg(test)]
pub(crate) fn request_class(method: &str, target: &str) -> RequestClass {
    let path = target.split_once('?').map_or(target, |(path, _)| path);
    match path {
        "/v1/intents/cancel"
        | "/v1/intents/expire"
        | "/v1/intents/refresh-quote"
        | "/v1/fill"
        | "/v1/link-unknown-remote"
        | "/v1/reconciliation"
        | STOP_PATH => RequestClass::Command,
        "/v1/intents" if method == "POST" => RequestClass::Command,
        "/v1/orders" if method == "POST" => RequestClass::Command,
        path if method == "DELETE" && path.starts_with("/v1/orders/") => RequestClass::Command,
        path if method == "PATCH" && path.starts_with("/v1/orders/") => RequestClass::Command,
        _ => RequestClass::Query,
    }
}

fn parse_operation(
    method: &str,
    target: &str,
    body: &[u8],
) -> Result<ControlOperation, (u16, Value)> {
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    if method == "GET" && path != kairos_workspace::runtime::HEALTH_PATH {
        return Err((
            405,
            json!({"error":"REST business queries are disabled; read the typed mmap view"}),
        ));
    }
    if path == kairos_workspace::runtime::HEALTH_PATH {
        return if method == "GET" {
            Ok(ControlOperation::Health)
        } else {
            Err((405, json!({"error":"health only supports GET"})))
        };
    }
    if method != "POST" && method != "DELETE" && method != "PATCH" {
        return Err((
            405,
            json!({"error":"Execution REST accepts only control commands"}),
        ));
    }
    let body =
        std::str::from_utf8(body).map_err(|error| (422, json!({"error": error.to_string()})))?;
    let invalid = |error: String| (422, json!({"error": error}));
    match (method, path) {
        ("POST", "/v1/time/advance") => {
            let value: Value =
                serde_json::from_str(body).map_err(|error| invalid(error.to_string()))?;
            let at = value
                .get("event_time_unix_nanos")
                .and_then(Value::as_u64)
                .ok_or_else(|| invalid("event_time_unix_nanos is required".into()))?;
            Ok(ControlOperation::AdvanceTime(at))
        }
        ("POST", "/v1/intents") => v2_submit_intent(body)
            .map(|(intent, idempotency_key)| ControlOperation::SubmitIntent {
                intent,
                idempotency_key,
            })
            .map_err(invalid),
        ("POST", "/v1/orders") => serde_json::from_str(body)
            .map(ControlOperation::SubmitOrder)
            .map_err(|error| invalid(error.to_string())),
        ("DELETE", path) if path.starts_with("/v1/orders/") => {
            let order_id = path.trim_start_matches("/v1/orders/");
            if order_id.is_empty() {
                return Err((404, json!({"error":"order id is required"})));
            }
            let reason = serde_json::from_str::<Value>(body)
                .ok()
                .and_then(|value| {
                    value
                        .get("reason")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                })
                .unwrap_or_default();
            Ok(ControlOperation::CancelOrder(CancelOrder {
                order_id: OrderId::new(order_id).map_err(|error| invalid(error.to_string()))?,
                reason,
            }))
        }
        ("PATCH", path) if path.starts_with("/v1/orders/") => {
            let order_id = path.trim_start_matches("/v1/orders/");
            if order_id.is_empty() {
                return Err((404, json!({"error":"order id is required"})));
            }
            let value: Value =
                serde_json::from_str(body).map_err(|error| invalid(error.to_string()))?;
            let patch: ReplaceOrderPatchWire = serde_json::from_value(value.clone())
                .map_err(|error| invalid(error.to_string()))?;
            let limit_price = value
                .get("limit_price")
                .map(|value| serde_json::from_value::<Option<Price>>(value.clone()))
                .transpose()
                .map_err(|error| invalid(error.to_string()))?;
            Ok(ControlOperation::ReplaceOrder {
                order_id: OrderId::new(order_id).map_err(|error| invalid(error.to_string()))?,
                patch: ReplaceOrderPatch {
                    quantity: patch.quantity,
                    limit_price,
                    options: patch.options,
                },
            })
        }
        ("POST", "/v1/reconciliation") => {
            let request: kairos_execution_contract::control::ReconcileExecutionRequest =
                serde_json::from_str(body).map_err(|error| invalid(error.to_string()))?;
            let order_id = request
                .order_id
                .map(OrderId::new)
                .transpose()
                .map_err(|error| invalid(error.to_string()))?;
            Ok(ControlOperation::Reconcile(RemoteOrderQuery {
                order_id,
                ..RemoteOrderQuery::default()
            }))
        }
        ("POST", "/v1/link-unknown-remote") => Ok(ControlOperation::LinkUnknownRemote {
            remote_order_id: query_value(query, "remote_order_id").unwrap_or_default(),
            local_order_id: query_value(query, "local_order_id").unwrap_or_default(),
        }),
        ("POST", "/v1/backtest") => serde_json::from_str(body)
            .map(ControlOperation::EvaluateBacktest)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/backtest/run") => serde_json::from_str(body)
            .map(ControlOperation::RunBacktest)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/backtest/market") => serde_json::from_str(body)
            .map(ControlOperation::ApplyBacktestMarket)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/intents/cancel") => serde_json::from_str(body)
            .map(ControlOperation::CancelIntent)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/intents/expire") => serde_json::from_str(body)
            .map(ControlOperation::ExpireIntent)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/intents/refresh-quote") => {
            let command: CommandEnvelope<RefreshQuoteIntent> =
                serde_json::from_str(body).map_err(|error| invalid(error.to_string()))?;
            if command.schema_version != 1
                || command.operation != "execution.refresh_quote"
                || command.instance_id.trim().is_empty()
            {
                return Err(invalid("invalid quote refresh command envelope".into()));
            }
            Ok(ControlOperation::RefreshQuote(command.payload))
        }
        ("POST", "/v1/preview-submit") => serde_json::from_str(body)
            .map(ControlOperation::PreviewSubmit)
            .map_err(|error| invalid(error.to_string())),
        ("POST", "/v1/fill") => serde_json::from_str(body)
            .map(ControlOperation::RecordFill)
            .map_err(|error| invalid(error.to_string())),
        ("POST", STOP_PATH) => Ok(ControlOperation::Stop),
        _ => Err((404, json!({"error":"unknown execution control path"}))),
    }
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
    tracing::info!(parent: &span, event = "control_request_completed", component = "execution", duration_ms, result = if response.status().is_success() { "accepted" } else { "rejected" }, "execution control request completed");
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
        Ok(Ok((status, payload))) => (
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
            Json(json!({"error":"execution process did not respond"})),
        )
            .into_response(),
    }
}

pub(crate) fn query_value(query: &str, key: &str) -> Option<String> {
    query
        .split('&')
        .find_map(|part| part.strip_prefix(&format!("{key}=")).map(str::to_owned))
}
pub(crate) fn v2_submit_intent(body: &str) -> Result<(ExecuteStrategyIntent, String), String> {
    let request: Value = serde_json::from_str(body).map_err(|error| error.to_string())?;
    let intent = request
        .get("intent")
        .cloned()
        .ok_or_else(|| "intent is required".to_owned())?;
    let intent_object = intent
        .as_object()
        .ok_or_else(|| "intent must be an object".to_owned())?;
    let idempotency_key = request
        .get("idempotency_key")
        .or_else(|| request.get("command_id"))
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| "idempotency_key or command_id is required".to_owned())?
        .to_owned();
    let legs: Vec<Value> = match intent_object.get("legs").and_then(Value::as_array) {
        Some(legs) if !legs.is_empty() => legs.clone(),
        _ => vec![json!({
            "leg_id": format!("{}:leg", intent_object.get("intent_id").and_then(Value::as_str).unwrap_or("intent")),
            "account_id": intent_object
                .get("account_ids")
                .and_then(Value::as_array)
                .and_then(|accounts| accounts.first())
                .cloned()
                .unwrap_or_else(|| Value::String("main".to_owned())),
            "segment_key": intent_object.get("segment_key").cloned().unwrap_or_else(|| Value::String("spot".to_owned())),
            "instrument_id": intent_object.get("instrument_id").cloned().ok_or_else(|| "intent instrument_id is required".to_owned())?,
            "market_id": intent_object.get("market_id").cloned().unwrap_or(Value::Null),
            "side": "buy",
            "quantity": intent_object.get("target_quantity").cloned().ok_or_else(|| "intent target_quantity is required".to_owned())?,
            "quantity_semantics": "target_position",
            "limit_price": intent_object.get("limit_price").cloned().unwrap_or(Value::Null),
            "options": intent_object.get("order_options").cloned().unwrap_or_else(|| json!({})),
        })],
    };
    let first = legs[0]
        .as_object()
        .ok_or_else(|| "intent leg must be an object".to_owned())?;
    let account_ids: Vec<Value> = legs
        .iter()
        .map(|leg| {
            leg.get("account_id")
                .cloned()
                .ok_or_else(|| "intent leg account_id is required".to_owned())
        })
        .collect::<Result<_, _>>()?;
    let legacy_legs: Vec<Value> = legs
        .iter()
        .map(|leg| {
            let leg = leg
                .as_object()
                .ok_or_else(|| "intent leg must be an object".to_owned())?;
            let mut value = leg.clone();
            let quantity_semantics = leg
                .get("quantity_semantics")
                .and_then(Value::as_str)
                .unwrap_or("order_quantity");
            value.insert(
                "target_position".to_owned(),
                Value::Bool(quantity_semantics.replace('_', "-") == "target-position"),
            );
            value.insert(
                "options".to_owned(),
                leg.get("options").cloned().unwrap_or_else(|| json!({})),
            );
            Ok(Value::Object(value))
        })
        .collect::<Result<_, String>>()?;
    let mut legacy = intent_object.clone();
    legacy.insert(
        "instrument_id".to_owned(),
        first
            .get("instrument_id")
            .cloned()
            .ok_or_else(|| "intent leg instrument_id is required".to_owned())?,
    );
    legacy.insert(
        "market_id".to_owned(),
        first.get("market_id").cloned().unwrap_or(Value::Null),
    );
    legacy.insert("account_ids".to_owned(), Value::Array(account_ids));
    legacy.insert(
        "segment_key".to_owned(),
        first
            .get("segment_key")
            .cloned()
            .unwrap_or_else(|| Value::String("spot".to_owned())),
    );
    legacy.insert(
        "target_quantity".to_owned(),
        first
            .get("quantity")
            .cloned()
            .ok_or_else(|| "intent leg quantity is required".to_owned())?,
    );
    legacy.insert(
        "limit_price".to_owned(),
        first.get("limit_price").cloned().unwrap_or(Value::Null),
    );
    legacy.insert("source_snapshot_id".to_owned(), Value::Null);
    legacy.insert("source_event_sequence".to_owned(), Value::Null);
    legacy.insert("source_event_time_unix_nanos".to_owned(), Value::Null);
    legacy.insert(
        "order_options".to_owned(),
        first.get("options").cloned().unwrap_or_else(|| json!({})),
    );
    legacy.insert(
        "reason".to_owned(),
        intent_object
            .get("reason")
            .cloned()
            .unwrap_or_else(|| Value::String(String::new())),
    );
    legacy.insert("legs".to_owned(), Value::Array(legacy_legs));
    let intent =
        serde_json::from_value(Value::Object(legacy)).map_err(|error| error.to_string())?;
    Ok((intent, idempotency_key))
}
