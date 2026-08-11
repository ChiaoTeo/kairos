//! Long-lived Risk process: control, health and application driving only.

use crate::application::RiskApplication;
use axum::{
    body::to_bytes,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json, Router,
};
use kairos_workspace::runtime::{HEALTH_PATH, SNAPSHOT_PATH, STOP_PATH};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use tokio::net::UnixListener;
use tokio::sync::{mpsc, oneshot};
use tokio::time::{self, MissedTickBehavior};
use tracing::{info, Instrument};

pub struct RiskProcess {
    application: RiskApplication,
    socket_path: PathBuf,
    health_file: Option<PathBuf>,
    stop_requested: bool,
    interval: Duration,
    snapshot_publisher: Option<Box<dyn RiskSnapshotPublisher>>,
}

/// Application-owned publication capability. Concrete transport publishers
/// are selected by composition and injected into the process facade.
pub trait RiskSnapshotPublisher: Send {
    fn publish(&mut self, snapshot: &crate::RiskSnapshot) -> Result<(), String>;
}

struct RiskHttpRequest {
    path: String,
    body: Vec<u8>,
    span: tracing::Span,
    response: oneshot::Sender<(StatusCode, Value)>,
}

impl RiskProcess {
    pub fn new(
        application: RiskApplication,
        socket_path: impl Into<PathBuf>,
        interval: Duration,
        health_file: Option<PathBuf>,
    ) -> Result<Self, String> {
        if interval.is_zero() {
            return Err("risk process interval must be positive".into());
        }
        Ok(Self {
            application,
            socket_path: socket_path.into(),
            health_file,
            stop_requested: false,
            interval,
            snapshot_publisher: None,
        })
    }

    pub fn with_snapshot_publisher<P>(mut self, publisher: P) -> Self
    where
        P: RiskSnapshotPublisher + 'static,
    {
        self.snapshot_publisher = Some(Box::new(publisher));
        self
    }
    pub fn application(&self) -> &RiskApplication {
        &self.application
    }

    pub async fn run(mut self) -> Result<(), Box<dyn std::error::Error>> {
        info!(event = "process_starting", component = "risk", socket = %self.socket_path.display(), interval_ms = self.interval.as_millis(), "risk process starting");
        remove_socket(&self.socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket_path)?;
        let (sender, mut receiver) = mpsc::channel(256);
        let router = Router::new().fallback(risk_http_handler).with_state(sender);
        let server = tokio::spawn(async move { axum::serve(listener, router).await });
        kairos_workspace::logging::record_gauge("kairos.process.ready", 1);
        info!(event = "process_ready", component = "risk", socket = %self.socket_path.display(), "risk control socket ready");
        let mut ticks = time::interval(self.interval);
        ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        let _ = self.write_health("ready").await;
        self.publish_snapshot();
        while !self.stop_requested {
            tokio::select! {
                Some(request) = receiver.recv() => {
                    let _entered = request.span.enter();
                    let response = self.handle(&request.path, &String::from_utf8_lossy(&request.body));
                    let _ = request.response.send(response);
                }
                _ = ticks.tick() => {
                    let _ = self.application.expire(crate::ExpireReservations { at_unix_nanos: unix_now_nanos().into() });
                    self.publish_snapshot();
                    let _ = self.write_health("ready").await;
                }
            }
        }
        remove_socket(&self.socket_path)?;
        server.abort();
        let _ = server.await;
        info!(
            event = "process_stopped",
            component = "risk",
            "risk process stopped"
        );
        Ok(())
    }

    fn publish_snapshot(&mut self) {
        let snapshot = self.application.snapshot();
        kairos_workspace::logging::record_gauge(
            "kairos.snapshot.generation",
            snapshot.generation.get(),
        );
        let Some(publisher) = self.snapshot_publisher.as_mut() else {
            return;
        };
        if let Err(error) = publisher.publish(&snapshot) {
            tracing::error!(event = "snapshot_publish_failed", component = "risk", error = %error, "risk snapshot publication failed");
        }
    }

    fn handle(&mut self, path: &str, raw_body: &str) -> (StatusCode, Value) {
        info!(event = "control_request", component = "risk", path = %path, "risk control request received");
        let (status, body) = match path {
            HEALTH_PATH => (200, self.health_body()),
            SNAPSHOT_PATH => match serde_json::to_value(self.application.snapshot()) {
                Ok(value) => (200, value),
                Err(error) => (500, serde_json::json!({"error": error.to_string()})),
            },
            "/v1/publish_policy" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .publish_policy(request)
                    .map(|_| serde_json::json!({"status":"active"}))
                    .map_err(|error| error.to_string())
            }),
            "/v1/authorize_and_reserve" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .authorize_and_reserve(request)
                    .and_then(|result| {
                        serde_json::to_value(result).map_err(|error| {
                            crate::application::RiskError::State(error.to_string())
                        })
                    })
                    .map_err(|error| error.to_string())
            }),
            "/v1/pre_trade_check" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .pre_trade_check(request)
                    .and_then(|result| {
                        serde_json::to_value(result).map_err(|error| {
                            crate::application::RiskError::State(error.to_string())
                        })
                    })
                    .map_err(|error| error.to_string())
            }),
            "/v1/post_trade_check" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .post_trade_check(request)
                    .and_then(|result| {
                        serde_json::to_value(result).map_err(|error| {
                            crate::application::RiskError::State(error.to_string())
                        })
                    })
                    .map_err(|error| error.to_string())
            }),
            "/v1/open_circuit" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .open_circuit(request)
                    .and_then(|result| {
                        serde_json::to_value(result).map_err(|error| {
                            crate::application::RiskError::State(error.to_string())
                        })
                    })
                    .map_err(|error| error.to_string())
            }),
            "/v1/close_circuit" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .close_circuit(request)
                    .and_then(|result| {
                        serde_json::to_value(result).map_err(|error| {
                            crate::application::RiskError::State(error.to_string())
                        })
                    })
                    .map_err(|error| error.to_string())
            }),
            "/v1/release" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .release(request)
                    .and_then(|result| {
                        serde_json::to_value(result).map_err(|error| {
                            crate::application::RiskError::State(error.to_string())
                        })
                    })
                    .map_err(|error| error.to_string())
            }),
            "/v1/resize" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .resize(request)
                    .and_then(|result| {
                        serde_json::to_value(result).map_err(|error| {
                            crate::application::RiskError::State(error.to_string())
                        })
                    })
                    .map_err(|error| error.to_string())
            }),
            "/v1/consume" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .consume(request)
                    .and_then(|result| {
                        serde_json::to_value(result).map_err(|error| {
                            crate::application::RiskError::State(error.to_string())
                        })
                    })
                    .map_err(|error| error.to_string())
            }),
            STOP_PATH => {
                self.stop_requested = true;
                (202, serde_json::json!({"status":"stopping"}))
            }
            _ => (
                404,
                serde_json::json!({"error":"unknown risk control path"}),
            ),
        };
        let status = StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        info!(event = "control_response", component = "risk", path = %path, status = status.as_u16(), "risk control response sent");
        (status, body)
    }

    fn json_command<F>(&mut self, body: &str, command: F) -> (u16, serde_json::Value)
    where
        F: FnOnce(&mut RiskApplication, &[u8]) -> Result<serde_json::Value, String>,
    {
        match command(&mut self.application, body.as_bytes()) {
            Ok(value) => (200, value),
            Err(error) => (422, serde_json::json!({"error": error})),
        }
    }
    async fn write_health(&self, status: &str) -> Result<(), std::io::Error> {
        let Some(path) = &self.health_file else {
            return Ok(());
        };
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let temp = path.with_extension("tmp");
        let snapshot = self.application.snapshot();
        let payload = serde_json::to_vec(&serde_json::json!({"status":status,"actor_id":snapshot.actor_id,"generation":snapshot.generation,"event_sequence":snapshot.event_sequence,"policy_version":snapshot.policy_version,"reservation_count":snapshot.reservations.len(),"open_circuit_count":snapshot.circuits.iter().filter(|c| c.open).count()})).map_err(std::io::Error::other)?;
        tokio::fs::write(&temp, payload).await?;
        tokio::fs::rename(temp, path).await
    }

    fn health_body(&self) -> serde_json::Value {
        let snapshot = self.application.snapshot();
        serde_json::json!({
            "status": "ready",
            "pid": std::process::id(),
            "actor_id": snapshot.actor_id,
            "generation": snapshot.generation,
            "event_sequence": snapshot.event_sequence,
            "policy_version": snapshot.policy_version,
            "budget_count": snapshot.limits.len(),
            "reservation_count": snapshot.reservations.len(),
            "open_circuit_count": snapshot.circuits.iter().filter(|c| c.open).count(),
        })
    }
}

async fn risk_http_handler(
    State(sender): State<mpsc::Sender<RiskHttpRequest>>,
    request: Request,
) -> Response {
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
    let response = risk_http_handler_inner(sender, request)
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
    tracing::info!(parent: &span, event = "control_request_completed", component = "risk", duration_ms, result = if response.status().is_success() { "accepted" } else { "rejected" }, "risk control request completed");
    response
}

async fn risk_http_handler_inner(
    sender: mpsc::Sender<RiskHttpRequest>,
    request: Request,
) -> Response {
    let path = request
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
                Json(serde_json::json!({"error":"request body too large"})),
            )
                .into_response()
        }
    };
    let (response_sender, response_receiver) = oneshot::channel();
    if sender
        .send(RiskHttpRequest {
            path,
            body,
            span: tracing::Span::current(),
            response: response_sender,
        })
        .await
        .is_err()
    {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({"error":"risk process is stopping"})),
        )
            .into_response();
    }
    match response_receiver.await {
        Ok((status, payload)) => (status, Json(payload)).into_response(),
        Err(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({"error":"risk process did not respond"})),
        )
            .into_response(),
    }
}

fn unix_now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
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
    use super::RiskProcess;
    use kairos_workspace::RestControlClient;
    use std::time::Duration;

    #[tokio::test(flavor = "current_thread")]
    async fn control_socket_exposes_health_snapshot_and_stop() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("risk.sock");
        let application =
            crate::composition::compose_risk_application("risk", Vec::new(), None).unwrap();
        let process =
            RiskProcess::new(application, &socket, Duration::from_millis(10), None).unwrap();
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
                let snapshot = client.request_json("GET", "/v1/snapshot", None).await.unwrap();
                assert_eq!(snapshot["actor_id"], "risk");
                let policy = r#"{"policy":{"policy_id":"account-notional","version":1,"scope":{"account_id":"main","strategy_id":null,"instrument_id":null,"exchange_id":null},"metric":"notional","limit":{"mantissa":100,"scale":0},"enforcement":"reject","valid_from_unix_nanos":0,"valid_until_unix_nanos":null}}"#;
                let configured = client.request_json("POST", "/v1/publish_policy", Some(policy.as_bytes())).await.unwrap();
                assert_eq!(configured["status"], "active");
                let request = r#"{"request_id":"request-1","idempotency_key":"key-1","reservation_id":"reservation-1","account_id":"main","strategy_id":"strategy","instrument_id":"instrument","exchange_id":"exchange","metric":"notional","amount":{"mantissa":40,"scale":0},"at_unix_nanos":1,"reservation_ttl_nanos":100,"dependency_generation":1,"dependency_event_sequence":1}"#;
                let decision = client.request_json("POST", "/v1/authorize_and_reserve", Some(request.as_bytes())).await.unwrap();
                assert_eq!(decision["allowed"], true);
                let stop = client.request_json("POST", "/v1/stop", None).await.unwrap();
                assert_eq!(stop["status"], "stopping");
                task.await.unwrap().unwrap();
            })
            .await;
    }
}
