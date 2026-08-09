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
use std::time::Duration;
use tokio::net::UnixListener;
use tokio::sync::mpsc::UnboundedSender;
use tokio::sync::{mpsc, oneshot};
use tokio::time::{self, MissedTickBehavior};
use tracing::info;

pub struct RiskProcess {
    application: RiskApplication,
    socket_path: PathBuf,
    health_file: Option<PathBuf>,
    stop_requested: bool,
    interval: Duration,
    snapshot_publisher: Option<crate::composition::MmapRiskSnapshotPublisher>,
}

struct RiskHttpRequest {
    path: String,
    body: Vec<u8>,
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

    pub fn with_snapshot_publisher(
        mut self,
        publisher: crate::composition::MmapRiskSnapshotPublisher,
    ) -> Self {
        self.snapshot_publisher = Some(publisher);
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
        let (sender, mut receiver) = mpsc::unbounded_channel();
        let router = Router::new().fallback(risk_http_handler).with_state(sender);
        let server = tokio::spawn(async move { axum::serve(listener, router).await });
        info!(event = "process_ready", component = "risk", socket = %self.socket_path.display(), "risk control socket ready");
        let mut ticks = time::interval(self.interval);
        ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        let _ = self.write_health("ready").await;
        self.publish_snapshot();
        while !self.stop_requested {
            tokio::select! {
                Some(request) = receiver.recv() => {
                    let response = self.handle(&request.path, &String::from_utf8_lossy(&request.body));
                    let _ = request.response.send(response);
                }
                _ = ticks.tick() => {
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
        let Some(publisher) = self.snapshot_publisher.as_mut() else {
            return;
        };
        if let Err(error) = publisher.publish(&self.application.snapshot()) {
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
            "/v1/configure" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .configure(request)
                    .map(|_| serde_json::json!({"status":"configured"}))
                    .map_err(|error| error.to_string())
            }),
            "/v1/assess" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .assess(request)
                    .and_then(|result| {
                        serde_json::to_value(result).map_err(|error| {
                            crate::application::RiskError::State(error.to_string())
                        })
                    })
                    .map_err(|error| error.to_string())
            }),
            "/v1/reserve" => self.json_command(raw_body, |application, body| {
                let request = serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .reserve(request)
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
        let payload = serde_json::to_vec(&serde_json::json!({"status":status,"actor_id":self.application.snapshot().actor_id,"generation":self.application.snapshot().generation,"event_sequence":self.application.snapshot().event_sequence})).map_err(std::io::Error::other)?;
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
            "budget_count": snapshot.budgets.len(),
            "reservation_count": snapshot.reservations.len(),
        })
    }
}

async fn risk_http_handler(
    State(sender): State<UnboundedSender<RiskHttpRequest>>,
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
            response: response_sender,
        })
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
            crate::composition::compose_risk_application("risk", Vec::new(), false, None).unwrap();
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
                let budget = r#"{"budgets":[{"budget_id":"account-notional","owner_id":"account","reference":{"scope":"account","subject":"main"},"metric":"notional","limit":{"mantissa":100,"scale":0},"used":{"mantissa":0,"scale":0},"reserved":{"mantissa":0,"scale":0},"valid_from_unix_nanos":null,"valid_until_unix_nanos":null}]}"#;
                let configured = client.request_json("POST", "/v1/configure", Some(budget.as_bytes())).await.unwrap();
                assert_eq!(configured["status"], "configured");
                let assessment = r#"{"request_id":"request-1","usages":[{"metric":"notional","amount":{"mantissa":40,"scale":0},"budgets":[{"scope":"account","subject":"main"}]}],"at_unix_nanos":1}"#;
                let assessed = client.request_json("POST", "/v1/assess", Some(assessment.as_bytes())).await.unwrap();
                assert_eq!(assessed["allowed"], true);
                let stop = client.request_json("POST", "/v1/stop", None).await.unwrap();
                assert_eq!(stop["status"], "stopping");
                task.await.unwrap().unwrap();
            })
            .await;
    }
}
