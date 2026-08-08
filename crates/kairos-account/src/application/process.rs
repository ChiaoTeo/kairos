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
use crate::domain::{AccountFill, OrderEvent, OrderRequest};
use crate::services::publication::FileAccountPublisher;
use kairos_workspace::runtime::{HEALTH_PATH, SNAPSHOT_PATH, STOP_PATH};

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
    lease_file: Option<PathBuf>,
    lease_instance_id: Option<String>,
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
            lease_file: None,
            lease_instance_id: None,
        })
    }

    pub fn with_trade_lease(
        mut self,
        lease_file: impl Into<PathBuf>,
        instance_id: impl Into<String>,
    ) -> Self {
        self.lease_file = Some(lease_file.into());
        self.lease_instance_id = Some(instance_id.into());
        self
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
        let _ = self
            .write_health(if self.lease_valid() {
                "ready"
            } else {
                "unavailable"
            })
            .await;
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
                    let _ = self.write_health(if self.last_error.is_some() || !self.lease_valid() { "degraded" } else { "ready" }).await;
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
            HEALTH_PATH => (200, self.health_json()),
            SNAPSHOT_PATH => (
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
            "/v1/plan-order" => self.json_command(body, |application, body| {
                let request: OrderRequest =
                    serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .plan_order(request, unix_now_nanos())
                    .map_err(|error| error.to_string())
                    .and_then(|order| {
                        serde_json::to_value(order).map_err(|error| error.to_string())
                    })
            }),
            "/v1/order-event" => self.json_command(body, |application, body| {
                let event: OrderEvent =
                    serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .apply_order_event(event)
                    .map_err(|error| error.to_string())
                    .and_then(|order| {
                        serde_json::to_value(order).map_err(|error| error.to_string())
                    })
            }),
            "/v1/fill" => self.json_command(body, |application, body| {
                let fill: AccountFill =
                    serde_json::from_slice(body).map_err(|error| error.to_string())?;
                application
                    .apply_fill(fill)
                    .map(|_| json!({"status":"applied"}))
                    .map_err(|error| error.to_string())
            }),
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
            STOP_PATH => {
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

    fn json_command<F>(&mut self, body: &str, handler: F) -> (u16, Value)
    where
        F: FnOnce(&mut AccountApplication, &[u8]) -> Result<Value, String>,
    {
        match handler(&mut self.application, body.as_bytes()) {
            Ok(value) => (202, value),
            Err(error) => (422, json!({"error": error})),
        }
    }

    fn health_json(&self) -> Value {
        json!({"status": if self.last_error.is_some() { "degraded" } else if !self.lease_valid() { "unavailable" } else { "ready" }, "account_id": self.account_id, "actor_id": self.application.snapshot().actor_id, "generation": self.application.snapshot().generation, "event_sequence": self.application.snapshot().event_sequence, "last_error": self.last_error, "last_refresh": self.last_refresh, "lease_valid": self.lease_valid()})
    }

    fn lease_valid(&self) -> bool {
        let (Some(path), Some(instance_id)) = (&self.lease_file, &self.lease_instance_id) else {
            return true;
        };
        let Ok(bytes) = std::fs::read(path) else {
            return false;
        };
        let Ok(value) = serde_json::from_slice::<Value>(&bytes) else {
            return false;
        };
        if value.get("launch_instance_id").and_then(Value::as_str) != Some(instance_id.as_str()) {
            return false;
        }
        let Ok(modified) = std::fs::metadata(path).and_then(|metadata| metadata.modified()) else {
            return false;
        };
        SystemTime::now()
            .duration_since(modified)
            .map(|age| age <= Duration::from_secs(60))
            .unwrap_or(false)
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
