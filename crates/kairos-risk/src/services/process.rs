//! Long-lived Risk process: control, health and application driving only.

use crate::application::RiskApplication;
use std::path::{Path, PathBuf};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};
use tokio::time::{self, MissedTickBehavior};

pub struct RiskProcess {
    application: RiskApplication,
    socket_path: PathBuf,
    health_file: Option<PathBuf>,
    stop_requested: bool,
    interval: Duration,
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
        })
    }
    pub fn application(&self) -> &RiskApplication {
        &self.application
    }
    pub async fn run(mut self) -> Result<(), Box<dyn std::error::Error>> {
        remove_socket(&self.socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket_path)?;
        let mut ticks = time::interval(self.interval);
        ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        let _ = self.write_health("ready").await;
        while !self.stop_requested {
            tokio::select! { accepted = listener.accept() => { let (stream, _) = accepted?; self.handle(stream).await?; }, _ = ticks.tick() => { let _ = self.write_health("ready").await; } }
        }
        remove_socket(&self.socket_path)?;
        Ok(())
    }
    async fn handle(&mut self, mut stream: UnixStream) -> Result<(), Box<dyn std::error::Error>> {
        let mut buffer = [0; 8192];
        let size = stream.read(&mut buffer).await?;
        let request = String::from_utf8_lossy(&buffer[..size]);
        let (head, raw_body) = request
            .split_once("\r\n\r\n")
            .unwrap_or((request.as_ref(), ""));
        let path = head
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .unwrap_or("/")
            .to_owned();
        let (status, body) = match path.as_str() {
            "/v1/health" => (200, self.health_body()),
            "/v1/snapshot" => (200, serde_json::to_value(self.application.snapshot())?),
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
            "/v1/stop" => {
                self.stop_requested = true;
                (202, serde_json::json!({"status":"stopping"}))
            }
            _ => (
                404,
                serde_json::json!({"error":"unknown risk control path"}),
            ),
        };
        let body = serde_json::to_vec(&body)?;
        let reason = if status == 200 {
            "OK"
        } else if status == 202 {
            "Accepted"
        } else {
            "Not Found"
        };
        let header = format!("HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
        stream.write_all(header.as_bytes()).await?;
        stream.write_all(&body).await?;
        Ok(())
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
            "actor_id": snapshot.actor_id,
            "generation": snapshot.generation,
            "event_sequence": snapshot.event_sequence,
            "budget_count": snapshot.budgets.len(),
            "reservation_count": snapshot.reservations.len(),
        })
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
    use crate::RiskApplication;
    use std::time::Duration;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::UnixStream;

    async fn request(socket: &std::path::Path, method: &str, path: &str, body: &str) -> String {
        let mut stream = UnixStream::connect(socket).await.unwrap();
        let request = format!(
            "{method} {path} HTTP/1.1\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        stream.write_all(request.as_bytes()).await.unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();
        response
    }

    #[tokio::test(flavor = "current_thread")]
    async fn control_socket_exposes_health_snapshot_and_stop() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("risk.sock");
        let application =
            RiskApplication::with_dependencies("risk", Vec::new(), false, None, None, None)
                .unwrap();
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
                let health = request(&socket, "GET", "/v1/health", "").await;
                assert!(health.contains("\"status\":\"ready\""));
                let snapshot = request(&socket, "GET", "/v1/snapshot", "").await;
                assert!(snapshot.contains("\"actor_id\":\"risk\""));
                let budget = r#"{"budgets":[{"budget_id":"account-notional","owner_id":"account","reference":{"scope":"account","subject":"main"},"metric":"notional","limit":{"mantissa":100,"scale":0},"used":{"mantissa":0,"scale":0},"reserved":{"mantissa":0,"scale":0},"valid_from_unix_nanos":null,"valid_until_unix_nanos":null}]}"#;
                let configured = request(&socket, "POST", "/v1/configure", budget).await;
                assert!(configured.contains("\"status\":\"configured\""));
                let assessment = r#"{"request_id":"request-1","usages":[{"metric":"notional","amount":{"mantissa":40,"scale":0},"budgets":[{"scope":"account","subject":"main"}]}],"at_unix_nanos":1}"#;
                let assessed = request(&socket, "POST", "/v1/assess", assessment).await;
                assert!(assessed.contains("\"allowed\":true"));
                let stop = request(&socket, "POST", "/v1/stop", "").await;
                assert!(stop.contains("\"status\":\"stopping\""));
                task.await.unwrap().unwrap();
            })
            .await;
    }
}
