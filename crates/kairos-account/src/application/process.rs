//! Account process. It owns lifecycle and control transport, not
//! account business state; the latter remains inside AccountActor.

use std::path::{Path, PathBuf};
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};
use tokio::time::{self, MissedTickBehavior};

use crate::application::{
    AccountApplication, AccountDataQuery, AccountRefreshReport, AccountSession, LoginAccount,
    OrderQuery, RefreshAccount,
};
use crate::domain::{AccountFill, Intent};
use crate::services::publication::FileAccountPublisher;

#[derive(serde::Deserialize)]
struct IntentCommand {
    request_id: String,
    intent: Intent,
}

pub struct AccountProcess {
    application: AccountApplication,
    account_id: String,
    socket_path: PathBuf,
    refresh_interval: Duration,
    health_file: Option<PathBuf>,
    publisher: Option<FileAccountPublisher>,
    stop_requested: bool,
    last_error: Option<String>,
    last_refresh: Option<AccountRefreshReport>,
    stream_enabled: bool,
}

impl AccountProcess {
    pub fn new(
        application: AccountApplication,
        account_id: impl Into<String>,
        socket_path: impl Into<PathBuf>,
        refresh_interval: Duration,
        health_file: Option<PathBuf>,
        publisher: Option<FileAccountPublisher>,
    ) -> Result<Self, String> {
        let account_id = account_id.into();
        if account_id.trim().is_empty() {
            return Err("account process account_id is required".into());
        }
        if refresh_interval.is_zero() {
            return Err("account refresh interval must be positive".into());
        }
        let stream_enabled = application.has_stream();
        Ok(Self {
            application,
            account_id,
            socket_path: socket_path.into(),
            refresh_interval,
            health_file,
            publisher,
            stop_requested: false,
            last_error: None,
            last_refresh: None,
            stream_enabled,
        })
    }

    pub fn application(&self) -> &AccountApplication {
        &self.application
    }

    pub async fn run(mut self) -> Result<(), Box<dyn std::error::Error>> {
        remove_socket(&self.socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket_path)?;
        let mut interval = time::interval(self.refresh_interval);
        interval.set_missed_tick_behavior(MissedTickBehavior::Delay);
        interval.tick().await;
        self.refresh();
        if let Err(error) = self.publish_snapshot() {
            self.last_error = Some(error);
        }
        let _ = self.write_health("ready").await;
        while !self.stop_requested {
            tokio::select! {
                accepted = listener.accept() => {
                    let (stream, _) = accepted?;
                    self.handle_client(stream).await?;
                }
                _ = interval.tick() => {
                    if self.stream_enabled {
                        if let Err(error) = self.application.poll_stream_once() {
                            self.last_error = Some(error.to_string());
                        }
                    }
                    self.refresh();
                    if let Err(error) = self.publish_snapshot() {
                        self.last_error = Some(error);
                    }
                    let _ = self.write_health(if self.last_error.is_some() { "degraded" } else { "ready" }).await;
                }
            }
        }
        remove_socket(&self.socket_path)?;
        Ok(())
    }

    fn refresh(&mut self) {
        let result = self.application.refresh_report(RefreshAccount {
            account_id: self.account_id.clone(),
            segments: Vec::new(),
        });
        match result {
            Ok(report) => {
                self.last_error = report.issues.first().map(|issue| issue.error.clone());
                self.last_refresh = Some(report);
            }
            Err(error) => self.last_error = Some(error.to_string()),
        }
    }

    async fn handle_client(
        &mut self,
        mut stream: UnixStream,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let mut buffer = vec![0; 8192];
        let size = stream.read(&mut buffer).await?;
        let request = String::from_utf8_lossy(&buffer[..size]);
        let (head, body) = request
            .split_once("\r\n\r\n")
            .unwrap_or((request.as_ref(), ""));
        let path = head
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .unwrap_or("/");
        let query = head
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .and_then(|value| value.split_once('?').map(|(_, query)| query))
            .unwrap_or("");
        let path = path.split('?').next().unwrap_or(path);
        let order_id = head
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .and_then(|value| value.split('?').nth(1))
            .and_then(|query| {
                query
                    .split('&')
                    .find_map(|pair| pair.strip_prefix("order_id="))
            })
            .map(str::to_owned);
        let account_query = parse_account_query(query, &self.account_id);
        let (status, body) = match path {
            "/v1/health" => (200, self.health_json()),
            "/v1/snapshot" => (
                200,
                serde_json::to_value(self.application.snapshot_query(&account_query))?,
            ),
            "/v1/balances" => (
                200,
                json!({
                    "accounts": self.application.balances_query(&account_query),
                    "rows": self.application.balance_rows_query(&account_query),
                    "page": account_query.page,
                    "page_size": account_query.page_size,
                }),
            ),
            "/v1/positions" => (
                200,
                json!({"accounts": self.application.positions_query(&account_query)}),
            ),
            "/v1/open-orders" => (
                200,
                json!({"accounts": self.application.open_orders_query(&account_query)}),
            ),
            "/v1/orders" => (
                200,
                json!({"orders": self.application.orders(OrderQuery {
                    account_id: None,
                    order_id,
                })}),
            ),
            "/v1/intents" => (200, json!({"intents": self.application.intents(None)})),
            "/v1/market-profiles" => (200, json!({"profiles": self.application.market_profiles()})),
            "/v1/capabilities" => (
                200,
                json!({"capabilities": self.application.capabilities(Some(&self.account_id))}),
            ),
            "/v1/fees" => (
                200,
                json!({"fees": self.application.fee_schedules(Some(&self.account_id))}),
            ),
            "/v1/login" => {
                let result = self.application.login(LoginAccount {
                    account_id: self.account_id.clone(),
                    segments: account_query.segments.clone(),
                    connection_ids: Vec::new(),
                    observed_at_unix_nanos: unix_now_nanos(),
                });
                match result {
                    Ok(result) => (200, serde_json::to_value(result)?),
                    Err(error) => (503, json!({"error": error.to_string()})),
                }
            }
            "/v1/logout" => match serde_json::from_str::<AccountSession>(body) {
                Ok(session) => match self.application.logout(&session) {
                    Ok(()) => (
                        200,
                        json!({"status": "logged_out", "session_id": session.session_id}),
                    ),
                    Err(error) => (422, json!({"error": error.to_string()})),
                },
                Err(error) => (
                    400,
                    json!({"error": format!("invalid account session: {error}")}),
                ),
            },
            "/v1/intents/submit" => match serde_json::from_str::<IntentCommand>(body) {
                Ok(command) if !command.request_id.trim().is_empty() => {
                    match self.application.record_intent(command.intent.clone()) {
                        Ok(()) => (
                            202,
                            json!({
                                "request_id": command.request_id,
                                "intent_id": command.intent.intent_id,
                                "status": "accepted",
                            }),
                        ),
                        Err(error) => (422, json!({"error": error.to_string()})),
                    }
                }
                Ok(_) => (422, json!({"error":"request_id is required"})),
                Err(error) => (
                    400,
                    json!({"error": format!("invalid intent command: {error}")}),
                ),
            },
            "/v1/fills" => match serde_json::from_str::<AccountFill>(body) {
                Ok(fill) => match self.application.apply_fill(fill) {
                    Ok(()) => (202, json!({"status": "accepted"})),
                    Err(error) => (422, json!({"error": error.to_string()})),
                },
                Err(error) => (
                    400,
                    json!({"error": format!("invalid account fill: {error}")}),
                ),
            },
            "/v1/refresh" => {
                self.refresh();
                (
                    if self.last_error.is_some() { 503 } else { 200 },
                    json!({
                        "health": self.health_json(),
                        "refresh": self.last_refresh,
                    }),
                )
            }
            "/v1/reconcile" => {
                let result = self
                    .application
                    .reconcile(crate::application::ReconcileAccount {
                        account_id: self.account_id.clone(),
                        segments: Vec::new(),
                    });
                match result {
                    Ok(report) => (200, json!({"reconcile": report})),
                    Err(error) => (503, json!({"error": error.to_string()})),
                }
            }
            "/v1/stop" => {
                self.stop_requested = true;
                (202, json!({"status":"stopping"}))
            }
            _ => (404, json!({"error":"unknown account control path"})),
        };
        if let Err(error) = self.publish_snapshot() {
            self.last_error = Some(error);
        }
        write_json(&mut stream, status, &body).await?;
        Ok(())
    }

    fn publish_snapshot(&mut self) -> Result<(), String> {
        let Some(publisher) = self.publisher.as_mut() else {
            return Ok(());
        };
        publisher.publish(&self.application.snapshot())
    }

    fn health_json(&self) -> Value {
        json!({"status": if self.last_error.is_some() { "degraded" } else { "ready" }, "account_id": self.account_id, "actor_id": self.application.snapshot().actor_id, "generation": self.application.snapshot().generation, "event_sequence": self.application.snapshot().event_sequence, "last_error": self.last_error, "last_refresh": self.last_refresh})
    }

    async fn write_health(&self, status: &str) -> Result<(), std::io::Error> {
        let Some(path) = &self.health_file else {
            return Ok(());
        };
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let temporary = path.with_extension("tmp");
        let payload = serde_json::to_vec(&json!({"status":status,"account_id":self.account_id,"actor_id":self.application.snapshot().actor_id,"generation":self.application.snapshot().generation,"event_sequence":self.application.snapshot().event_sequence,"last_error":self.last_error})).map_err(std::io::Error::other)?;
        tokio::fs::write(&temporary, payload).await?;
        tokio::fs::rename(temporary, path).await
    }
}

fn unix_now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos().min(u64::MAX as u128) as u64)
        .unwrap_or_default()
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

fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn parse_account_query(query: &str, account_id: &str) -> AccountDataQuery {
    let mut request = AccountDataQuery {
        account_id: Some(account_id.to_string()),
        ..Default::default()
    };
    for pair in query.split('&').filter(|value| !value.is_empty()) {
        let Some((key, value)) = pair.split_once('=') else {
            continue;
        };
        match key {
            "segment" => request.segments.push(value.to_string()),
            "symbol" => request.symbol = Some(value.to_string()),
            "limit" => request.limit = value.parse().ok(),
            "include_zero" => request.include_zero = value == "true" || value == "1",
            "page" => request.page = value.parse().ok(),
            "page_size" => request.page_size = value.parse().ok(),
            _ => {}
        }
    }
    request
}

#[cfg(test)]
mod tests {
    use super::AccountProcess;
    use crate::application::AccountApplication;
    use crate::composition::{empty_snapshot, InMemoryAccountSource};
    use crate::domain::{AccountSegment, ExternalAccountIdentity};
    use std::collections::BTreeMap;
    use std::time::Duration;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::UnixStream;

    async fn request(socket: &std::path::Path, body: &str) -> String {
        let mut stream = UnixStream::connect(socket).await.unwrap();
        let request = format!(
            "POST /v1/intents/submit HTTP/1.1\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        stream.write_all(request.as_bytes()).await.unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();
        response
    }

    #[tokio::test(flavor = "current_thread")]
    async fn intent_command_enters_account_application() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("account.sock");
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: "spot".into(),
            environment: "paper".into(),
            account_model: Some("no_margin".into()),
        };
        let source = InMemoryAccountSource {
            snapshots: BTreeMap::from([("spot".into(), empty_snapshot("spot"))]),
        };
        let application =
            AccountApplication::with_dependencies(vec![segment], Box::new(source), None).unwrap();
        let process = AccountProcess::new(
            application,
            "main",
            &socket,
            Duration::from_secs(60),
            None,
            None,
        )
        .unwrap();
        tokio::task::LocalSet::new()
            .run_until(async move {
                let task = tokio::task::spawn_local(process.run());
                for _ in 0..100 {
                    if socket.exists() {
                        break;
                    }
                    tokio::task::yield_now().await;
                }
                let body = r#"{"request_id":"request-1","intent":{"intent_id":"intent-1","strategy_id":"sma","account_id":"main","segment_key":"spot","instrument_id":"BTCUSDT","kind":"TargetPosition","target_quantity":{"mantissa":1,"scale":0},"quantity":null,"limit_price":null,"created_at_unix_nanos":1,"reason":"test"}}"#;
                let response = request(&socket, body).await;
                assert!(response.contains("\"status\":\"accepted\""));
                let _ = request_stop(&socket).await;
                task.await.unwrap().unwrap();
            })
            .await;
    }

    async fn request_stop(socket: &std::path::Path) -> String {
        let mut stream = UnixStream::connect(socket).await.unwrap();
        stream
            .write_all(b"POST /v1/stop HTTP/1.1\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            .await
            .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();
        response
    }
}
