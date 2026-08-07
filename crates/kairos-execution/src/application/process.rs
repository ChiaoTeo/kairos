use crate::application::{
    BacktestApplication, BacktestRequest, CancelOrder, ExecuteIntent, ExecutionApplication,
    ExecutionAuditEvent, ExecutionAuditQuery, ExecutionAuditSink, ExecutionFillReport,
    RemoteOrderQuery, ReplaceOrder, SubmitOrder,
};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};

pub struct ExecutionProcess {
    application: ExecutionApplication,
    socket_path: PathBuf,
    audit: Option<Box<dyn ExecutionAuditSink>>,
    stopping: bool,
}

impl ExecutionProcess {
    pub fn new(application: ExecutionApplication, socket_path: impl Into<PathBuf>) -> Self {
        Self {
            application,
            socket_path: socket_path.into(),
            audit: None,
            stopping: false,
        }
    }

    pub fn with_audit(
        application: ExecutionApplication,
        socket_path: impl Into<PathBuf>,
        audit: impl ExecutionAuditSink + 'static,
    ) -> Self {
        Self {
            application,
            socket_path: socket_path.into(),
            audit: Some(Box::new(audit)),
            stopping: false,
        }
    }

    pub async fn run(mut self) -> Result<(), Box<dyn std::error::Error>> {
        remove_socket(&self.socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket_path)?;
        self.flush_events()?;
        while !self.stopping {
            let (stream, _) = listener.accept().await?;
            self.handle(stream).await?;
            self.flush_events()?;
        }
        remove_socket(&self.socket_path)?;
        Ok(())
    }

    async fn handle(&mut self, mut stream: UnixStream) -> Result<(), Box<dyn std::error::Error>> {
        let mut buffer = vec![0_u8; 64 * 1024];
        let size = stream.read(&mut buffer).await?;
        let request = String::from_utf8_lossy(&buffer[..size]);
        let (head, body) = request
            .split_once("\r\n\r\n")
            .unwrap_or((request.as_ref(), ""));
        let target = head
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .unwrap_or("/");
        let (path, query) = target.split_once('?').unwrap_or((target, ""));
        let (status, payload) = match path {
            "/v1/health" => (
                200,
                json!({"status":"ready","actor_id":self.application.snapshot().actor_id,"generation":self.application.snapshot().generation,"event_sequence":self.application.snapshot().event_sequence,"order_count":self.application.snapshot().orders.len()}),
            ),
            "/v1/snapshot" => (200, serde_json::to_value(self.application.snapshot())?),
            "/v1/orders" => (
                200,
                json!({"orders": self.application.orders(query_value(query, "account_id").as_deref())}),
            ),
            "/v1/open-orders" => (
                200,
                json!({"orders": self.application.orders(query_value(query, "account_id").as_deref()).into_iter().filter(|order| !order.status.terminal()).collect::<Vec<_>>() }),
            ),
            "/v1/history" => (
                200,
                json!({"orders": self.application.orders(query_value(query, "account_id").as_deref())}),
            ),
            "/v1/remote-open-orders" => {
                match self.application.remote_open_orders(remote_query(query)) {
                    Ok(orders) => (200, json!({"orders": orders})),
                    Err(error) => (422, json!({"error": error.to_string()})),
                }
            }
            "/v1/remote-history" => match self.application.remote_history(remote_query(query)) {
                Ok(orders) => (200, json!({"orders": orders})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/remote-order" => match self.application.remote_detail(remote_query(query)) {
                Ok(order) => (200, json!(order)),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/stream/next" => match self.application.next_remote_execution_event() {
                Ok(event) => (200, json!(event)),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/stream/consume" => match self.application.consume_remote_execution_event() {
                Ok(event) => (200, json!(event)),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/events" => (
                200,
                json!({"events": self.application.events(query_value(query, "order_id").as_deref())}),
            ),
            "/v1/fills" => (
                200,
                json!({"fills": self.application.fills(query_value(query, "order_id").as_deref())}),
            ),
            "/v1/trace" => (
                200,
                json!({"events": self.application.trace(&query_value(query, "order_id").unwrap_or_default())}),
            ),
            "/v1/audit" => match self.audit_events(audit_query(query)) {
                Ok(events) => (200, json!({"events": events})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/journal" => match self.audit_events(audit_query(query)) {
                Ok(events) => (
                    200,
                    json!({"order_id": query_value(query, "order_id"), "entries": events}),
                ),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/backtest" => match serde_json::from_str::<BacktestRequest>(body)
                .map_err(|error| error.to_string())
                .and_then(BacktestApplication::evaluate)
            {
                Ok(metrics) => (200, serde_json::to_value(metrics)?),
                Err(error) => (422, json!({"error": error})),
            },
            "/v1/submit" => match serde_json::from_str::<SubmitOrder>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .submit(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (202, serde_json::to_value(order)?),
                Err(error) => (422, json!({"error":error})),
            },
            "/v1/preview-submit" => match serde_json::from_str::<SubmitOrder>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .preview_submit(&request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (200, serde_json::to_value(order)?),
                Err(error) => (422, json!({"error":error})),
            },
            "/v1/cancel" => match serde_json::from_str::<CancelOrder>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .cancel(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (202, serde_json::to_value(order)?),
                Err(error) => (422, json!({"error":error})),
            },
            "/v1/replace" => match serde_json::from_str::<ReplaceOrder>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .replace(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (202, serde_json::to_value(order)?),
                Err(error) => (422, json!({"error":error})),
            },
            "/v1/intents/execute" => match serde_json::from_str::<ExecuteIntent>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .execute_intent(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (202, serde_json::to_value(order)?),
                Err(error) => (422, json!({"error":error})),
            },
            "/v1/fill" => match serde_json::from_str::<ExecutionFillReport>(body)
                .map_err(|error| error.to_string())
                .and_then(|request| {
                    self.application
                        .record_fill(request)
                        .map_err(|error| error.to_string())
                }) {
                Ok(order) => (202, serde_json::to_value(order)?),
                Err(error) => (422, json!({"error":error})),
            },
            "/v1/stop" => {
                self.stopping = true;
                (202, json!({"status":"stopping"}))
            }
            _ => (404, json!({"error":"unknown execution control path"})),
        };
        write_json(&mut stream, status, &payload).await?;
        Ok(())
    }

    fn flush_events(&mut self) -> Result<(), String> {
        let Some(audit) = self.audit.as_mut() else {
            return Ok(());
        };
        let events = self.application.drain_events();
        for event in &events {
            audit.publish(event)?;
        }
        Ok(())
    }

    fn audit_events(
        &mut self,
        query: ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, crate::application::ExecutionError> {
        if let Some(audit) = self.audit.as_mut() {
            audit
                .query(&query)
                .map_err(crate::application::ExecutionError::Persistence)
        } else {
            self.application.audit_events(query)
        }
    }
}

fn query_value(query: &str, key: &str) -> Option<String> {
    query
        .split('&')
        .find_map(|part| part.strip_prefix(&format!("{key}=")).map(str::to_owned))
}
fn remote_query(query: &str) -> RemoteOrderQuery {
    RemoteOrderQuery {
        symbol: query_value(query, "symbol"),
        order_id: query_value(query, "order_id"),
        limit: query_value(query, "limit").and_then(|value| value.parse().ok()),
        since_unix_millis: query_value(query, "since_unix_millis")
            .and_then(|value| value.parse().ok()),
    }
}

fn audit_query(query: &str) -> ExecutionAuditQuery {
    ExecutionAuditQuery {
        order_id: query_value(query, "order_id"),
        venue_order_id: query_value(query, "venue_order_id"),
        status: query_value(query, "status"),
        limit: query_value(query, "limit").and_then(|value| value.parse().ok()),
        ..Default::default()
    }
}
fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}
async fn write_json(
    stream: &mut UnixStream,
    status: u16,
    value: &Value,
) -> Result<(), std::io::Error> {
    let body = serde_json::to_vec(value).map_err(std::io::Error::other)?;
    let reason = if status == 200 {
        "OK"
    } else if status == 202 {
        "Accepted"
    } else if status == 404 {
        "Not Found"
    } else {
        "Unprocessable Entity"
    };
    let header = format!("HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
    stream.write_all(header.as_bytes()).await?;
    stream.write_all(&body).await
}
