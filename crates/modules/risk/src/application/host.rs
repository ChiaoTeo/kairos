//! Risk Contract host. Business state and scheduling are driven only through
//! the Conflux ingress; this module owns HTTP-over-UDS framing and process
//! lifecycle adaptation.

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
use kairos_risk_contract::{AuthorizeRequest, RiskControlError, RiskRestRequest, RiskRestResponse};
use kairos_workspace::runtime::{HEALTH_PATH, STOP_PATH};
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use tokio::net::UnixListener;
use tracing::Instrument;

use super::{RiskApplication, RiskClockMode};

pub struct RiskHost {
    application: RiskApplication,
    system: ConfluxSystem,
    socket_path: PathBuf,
    health_file: Option<PathBuf>,
    interval: Duration,
}

#[derive(Clone)]
struct RiskHttpState {
    handle: ConfluxHandle<RiskApplication>,
}

enum HostRequest {
    Rest(RiskRestRequest),
    Stop,
}

impl RiskHost {
    pub fn new(
        application: RiskApplication,
        system: ConfluxSystem,
        socket_path: impl Into<PathBuf>,
        interval: Duration,
        health_file: Option<PathBuf>,
    ) -> Result<Self, String> {
        if interval.is_zero() {
            return Err("risk process interval must be positive".into());
        }
        Ok(Self {
            application,
            system,
            socket_path: socket_path.into(),
            health_file,
            interval,
        })
    }

    pub fn with_replay_clock(mut self, enabled: bool) -> Self {
        self.application.set_clock_mode(if enabled {
            RiskClockMode::Replay
        } else {
            RiskClockMode::Wall
        });
        self
    }

    pub async fn run(self) -> Result<(), Box<dyn std::error::Error>> {
        tracing::info!(
            event = "process_starting",
            component = "risk",
            socket = %self.socket_path.display(),
            interval_ms = self.interval.as_millis(),
            "Risk Conflux process starting"
        );
        remove_socket(&self.socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }

        let (conflux, handle) = Conflux::new(
            self.application,
            self.system,
            ConfluxConfig {
                ingress_capacity: 256,
                ..ConfluxConfig::default()
            },
        )?;
        let process = tokio::task::spawn_local(conflux.run());

        let listener = UnixListener::bind(&self.socket_path)?;
        let router = Router::new()
            .fallback(risk_http_handler)
            .with_state(RiskHttpState {
                handle: handle.clone(),
            });
        let server = tokio::spawn(async move { axum::serve(listener, router).await });

        // The first typed turn publishes the initial current view and proves
        // the Actor is accepting work before readiness is announced.
        handle
            .handle(ConfluxEvent::Rest(RiskRestRequest::Health))
            .await
            .map_err(|error| format!("Risk Conflux startup health failed: {error:?}"))?;
        write_health(self.health_file.as_deref(), "ready").await?;
        kairos_workspace::logging::record_gauge("kairos.process.ready", 1);
        tracing::info!(
            event = "process_ready",
            component = "risk",
            socket = %self.socket_path.display(),
            "Risk Conflux control socket ready"
        );

        let outcome = process.await.map_err(|error| error.to_string())??;
        server.abort();
        let _ = server.await;
        remove_socket(&self.socket_path)?;
        write_health(self.health_file.as_deref(), "stopped").await?;
        tracing::info!(
            event = "process_stopped",
            component = "risk",
            phase = ?outcome.phase,
            discarded_inputs = outcome.discarded_inputs,
            "Risk Conflux process stopped"
        );
        Ok(())
    }
}

async fn risk_http_handler(State(host): State<RiskHttpState>, request: Request) -> Response {
    let started = Instant::now();
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
    let span = tracing::info_span!(
        "risk.control_request",
        component = "risk",
        method = %method,
        path = %path,
        status = tracing::field::Empty,
        duration_ms = tracing::field::Empty,
    );
    kairos_workspace::logging::set_remote_parent(&span, request.headers());
    let response = risk_http_handler_inner(host, request)
        .instrument(span.clone())
        .await;
    span.record("status", response.status().as_u16());
    span.record("duration_ms", started.elapsed().as_secs_f64() * 1_000.0);
    response
}

async fn risk_http_handler_inner(host: RiskHttpState, request: Request) -> Response {
    let method = request.method().as_str().to_owned();
    let path = request.uri().path().to_owned();
    let body = match to_bytes(
        request.into_body(),
        kairos_workspace::control::MAX_HTTP_BODY_BYTES,
    )
    .await
    {
        Ok(body) => body,
        Err(_) => return json(StatusCode::PAYLOAD_TOO_LARGE, "request body too large"),
    };
    let request = match decode_request(&method, &path, &body) {
        Ok(request) => request,
        Err(response) => return response,
    };
    match request {
        HostRequest::Stop => {
            host.handle.shutdown(ShutdownMode::Drain);
            (
                StatusCode::ACCEPTED,
                Json(serde_json::json!({"status":"stopping"})),
            )
                .into_response()
        }
        HostRequest::Rest(request) => match host.handle.handle(ConfluxEvent::Rest(request)).await {
            Ok(Some(response)) => encode_response(response),
            Ok(None) => json(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Risk Actor omitted its REST response",
            ),
            Err(_) => json(StatusCode::SERVICE_UNAVAILABLE, "Risk process is stopping"),
        },
    }
}

fn decode_request(method: &str, path: &str, body: &[u8]) -> Result<HostRequest, Response> {
    if path == STOP_PATH {
        return if method == "POST" {
            Ok(HostRequest::Stop)
        } else {
            Err(json(
                StatusCode::METHOD_NOT_ALLOWED,
                "stop accepts only POST",
            ))
        };
    }
    if path == HEALTH_PATH {
        return if method == "GET" {
            Ok(HostRequest::Rest(RiskRestRequest::Health))
        } else {
            Err(json(
                StatusCode::METHOD_NOT_ALLOWED,
                "health accepts only GET",
            ))
        };
    }
    if method != "POST" {
        return Err(json(
            StatusCode::METHOD_NOT_ALLOWED,
            "Risk business queries use typed mmap views; REST accepts control commands",
        ));
    }

    let request = match path {
        "/v1/publish_policy" => RiskRestRequest::PublishPolicy(decode(body)?),
        "/v1/authorizations" | "/v1/authorize_and_reserve" => {
            RiskRestRequest::AuthorizeAndReserve(decode::<AuthorizeRequest>(body)?)
        }
        "/v1/pre_trade_check" => RiskRestRequest::PreTradeCheck(decode(body)?),
        "/v1/post_trade_check" => RiskRestRequest::PostTradeCheck(decode(body)?),
        "/v1/open_circuit" => RiskRestRequest::OpenCircuit(decode(body)?),
        "/v1/close_circuit" => RiskRestRequest::CloseCircuit(decode(body)?),
        "/v1/resize" => RiskRestRequest::ResizeReservation(decode(body)?),
        path if path == "/v1/release"
            || (path.starts_with("/v1/reservations/") && path.ends_with("/release")) =>
        {
            RiskRestRequest::ReleaseReservation(decode(body)?)
        }
        path if path == "/v1/consume"
            || (path.starts_with("/v1/reservations/") && path.ends_with("/consume")) =>
        {
            RiskRestRequest::ConsumeReservation(decode(body)?)
        }
        "/v1/time/advance" => RiskRestRequest::AdvanceTime(decode(body)?),
        _ => return Err(json(StatusCode::NOT_FOUND, "unknown Risk control path")),
    };
    Ok(HostRequest::Rest(request))
}

fn decode<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T, Response> {
    serde_json::from_slice(body).map_err(|error| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({
                "error": "invalid Risk request",
                "details": error.to_string(),
            })),
        )
            .into_response()
    })
}

fn encode_response(response: RiskRestResponse) -> Response {
    match response {
        RiskRestResponse::Health(result) => encode_result(result),
        RiskRestResponse::PublishPolicy(result) => encode_result(result),
        RiskRestResponse::AuthorizeAndReserve(result) => encode_result(result),
        RiskRestResponse::PreTradeCheck(result) => encode_result(result),
        RiskRestResponse::PostTradeCheck(result) => encode_result(result),
        RiskRestResponse::OpenCircuit(result) => encode_result(result),
        RiskRestResponse::CloseCircuit(result) => encode_result(result),
        RiskRestResponse::ResizeReservation(result) => encode_result(result),
        RiskRestResponse::ReleaseReservation(result) => encode_result(result),
        RiskRestResponse::ConsumeReservation(result) => encode_result(result),
        RiskRestResponse::AdvanceTime(result) => encode_result(result),
    }
}

fn encode_result<T: Serialize>(result: Result<T, RiskControlError>) -> Response {
    match result {
        Ok(value) => match serde_json::to_value(value) {
            Ok(value) => (StatusCode::OK, Json(value)).into_response(),
            Err(error) => json(
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("encode Risk response: {error}"),
            ),
        },
        Err(error) => (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({
                "error": error.message,
                "code": error.code,
                "retryable": error.retryable,
                "details": error.details,
            })),
        )
            .into_response(),
    }
}

fn json(status: StatusCode, message: impl Into<String>) -> Response {
    (status, Json(serde_json::json!({"error": message.into()}))).into_response()
}

async fn write_health(path: Option<&Path>, status: &str) -> Result<(), std::io::Error> {
    let Some(path) = path else {
        return Ok(());
    };
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let temp = path.with_extension("tmp");
    let payload =
        serde_json::to_vec(&serde_json::json!({"status":status})).map_err(std::io::Error::other)?;
    tokio::fs::write(&temp, payload).await?;
    tokio::fs::rename(temp, path).await
}

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

#[cfg(test)]
mod tests {
    use super::RiskHost;
    use kairos_conflux::ConfluxSystem;
    use kairos_workspace::RestControlClient;
    use std::time::Duration;

    #[tokio::test(flavor = "current_thread")]
    async fn control_socket_exposes_typed_conflux_contract_and_stop() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("risk.sock");
        let application =
            crate::composition::compose_risk_application("risk", Vec::new(), None).unwrap();
        let process = RiskHost::new(
            application,
            ConfluxSystem::new(),
            &socket,
            Duration::from_millis(10),
            None,
        )
        .unwrap()
        .with_replay_clock(true);
        tokio::task::LocalSet::new()
            .run_until(async move {
                let task = tokio::task::spawn_local(process.run());
                for _ in 0..100 {
                    if socket.exists() {
                        break;
                    }
                    tokio::task::yield_now().await;
                }
                let client = RestControlClient::new(&socket);
                let health = client.health().await.unwrap();
                assert_eq!(health["status"], "ready");
                let policy = r#"{"policy":{"policy_id":"account-notional","version":1,"scope":{"account_id":"main","strategy_id":null,"instrument_id":null,"exchange_id":null},"metric":"notional","limit":"100","enforcement":"reject","valid_from_unix_nanos":0,"valid_until_unix_nanos":null}}"#;
                let configured = client.request_json("POST", "/v1/publish_policy", Some(policy.as_bytes())).await.unwrap();
                assert_eq!(configured["status"], "active");
                let request = r#"{"request_id":"request-1","idempotency_key":"key-1","reservation_id":"reservation-1","account_id":"main","strategy_id":"strategy","instrument_id":"instrument","exchange_id":"exchange","metric":"notional","amount":"40","at_unix_nanos":1,"reservation_ttl_nanos":100,"dependency_generation":1,"dependency_event_sequence":1}"#;
                let decision = client.request_json("POST", "/v1/authorizations", Some(request.as_bytes())).await.unwrap();
                assert_eq!(decision["allowed"], true);
                assert_eq!(decision["instrument_id"], "instrument");
                let advance = client.request_json("POST", "/v1/time/advance", Some(br#"{"event_time_unix_nanos":101}"#)).await.unwrap();
                assert_eq!(advance["expired"], 1);
                let stop = client.request_json("POST", "/v1/stop", None).await.unwrap();
                assert_eq!(stop["status"], "stopping");
                task.await.unwrap().unwrap();
            })
            .await;
    }
}
