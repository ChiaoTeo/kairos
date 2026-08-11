use std::{
    io,
    os::unix::fs::FileTypeExt,
    path::{Path, PathBuf},
    sync::Arc,
};

use axum::{
    extract::{Path as RoutePath, State},
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use http_body_util::{BodyExt, Full};
use hyper::{body::Bytes, Request, Uri};
use hyperlocal_next::{UnixClientExt, Uri as UnixUri};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::net::UnixListener;

pub const CONTROL_API_VERSION: &str = "v1";
pub const MAX_HTTP_BODY_BYTES: usize = 1024 * 1024;

#[derive(Clone)]
pub struct ControlApi {
    component_id: Arc<str>,
}

impl ControlApi {
    pub fn new(component_id: impl Into<Arc<str>>) -> Self {
        Self {
            component_id: component_id.into(),
        }
    }

    pub fn router(self) -> Router {
        Router::new()
            .route("/v1/health", get(health))
            .route("/v1/components/{component_id}", get(component))
            .route("/v1/components/{component_id}/commands", post(command))
            .with_state(self)
    }
}

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
    component_id: String,
    protocol: &'static str,
}

#[derive(Serialize)]
struct ComponentResponse {
    component_id: String,
    state: &'static str,
    protocol: &'static str,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ControlCommand {
    Start,
    Stop { reason: Option<String> },
    Pause { reason: Option<String> },
    Resume,
    Reload { config_revision: String },
    Health,
}

#[derive(Debug, Serialize)]
struct CommandResponse {
    request_id: String,
    component_id: String,
    status: &'static str,
    command: String,
    message: String,
}

async fn health(State(api): State<ControlApi>) -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok",
        component_id: api.component_id.to_string(),
        protocol: CONTROL_API_VERSION,
    })
}

async fn component(
    State(api): State<ControlApi>,
    RoutePath(component_id): RoutePath<String>,
) -> impl IntoResponse {
    if component_id != api.component_id.as_ref() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "component not found"})),
        );
    }
    (
        StatusCode::OK,
        Json(json!(ComponentResponse {
            component_id,
            state: "running",
            protocol: CONTROL_API_VERSION,
        })),
    )
}

async fn command(
    State(api): State<ControlApi>,
    RoutePath(component_id): RoutePath<String>,
    Json(command): Json<ControlCommand>,
) -> impl IntoResponse {
    if component_id != api.component_id.as_ref() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "component not found"})),
        );
    }

    let command_name = command_name(&command);
    let request_id = format!("cmd-{}", unix_nanos());
    (
        StatusCode::ACCEPTED,
        Json(json!(CommandResponse {
            request_id,
            component_id,
            status: "accepted",
            command: command_name,
            message: "command accepted by control boundary".to_string(),
        })),
    )
}

fn command_name(command: &ControlCommand) -> String {
    match command {
        ControlCommand::Start => "start",
        ControlCommand::Stop { .. } => "stop",
        ControlCommand::Pause { .. } => "pause",
        ControlCommand::Resume => "resume",
        ControlCommand::Reload { .. } => "reload",
        ControlCommand::Health => "health",
    }
    .to_string()
}

pub struct ControlServer {
    socket_path: PathBuf,
}

impl ControlServer {
    pub fn new(socket_path: impl Into<PathBuf>) -> Self {
        Self {
            socket_path: socket_path.into(),
        }
    }

    pub fn socket_path(&self) -> &Path {
        &self.socket_path
    }

    pub async fn serve(self, api: ControlApi) -> io::Result<()> {
        remove_stale_socket(&self.socket_path)?;
        let listener = UnixListener::bind(&self.socket_path)?;
        let router = api.router();
        axum::serve(listener, router)
            .with_graceful_shutdown(shutdown_signal())
            .await
            .map_err(io::Error::other)
    }
}

impl Drop for ControlServer {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.socket_path);
    }
}

pub struct RestControlClient {
    socket_path: PathBuf,
}

impl RestControlClient {
    pub fn new(socket_path: impl Into<PathBuf>) -> Self {
        Self {
            socket_path: socket_path.into(),
        }
    }

    pub async fn health(&self) -> io::Result<Value> {
        self.request("GET", "/v1/health", None).await
    }

    pub async fn request_json(
        &self,
        method: &str,
        path: &str,
        body: Option<&[u8]>,
    ) -> io::Result<Value> {
        self.request(method, path, body).await
    }

    pub async fn send_command(
        &self,
        component_id: &str,
        command: &ControlCommand,
    ) -> io::Result<Value> {
        let body = serde_json::to_vec(command)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?;
        self.request(
            "POST",
            &format!("/v1/components/{component_id}/commands"),
            Some(&body),
        )
        .await
    }

    async fn request(&self, method: &str, path: &str, body: Option<&[u8]>) -> io::Result<Value> {
        let body = body.unwrap_or_default();
        let uri: Uri = UnixUri::new(&self.socket_path, path).into();
        let mut request = Request::builder()
            .method(method)
            .uri(uri)
            .header("host", "localhost")
            .header("connection", "close")
            .header("content-length", body.len())
            .body(Full::new(Bytes::copy_from_slice(body)))
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?;
        if !body.is_empty() {
            request.headers_mut().insert(
                "content-type",
                "application/json".parse().expect("static header value"),
            );
        }
        crate::logging::inject_current_context(request.headers_mut());
        let client = hyper_util::client::legacy::Client::unix();
        let response = client.request(request).await.map_err(io::Error::other)?;
        let status = response.status();
        let bytes = response
            .into_body()
            .collect()
            .await
            .map_err(io::Error::other)?
            .to_bytes();
        let value: Value = serde_json::from_slice(&bytes)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        if !status.is_success() {
            return Err(io::Error::other(format!(
                "control request failed ({}): {value}",
                status.as_u16()
            )));
        }
        Ok(value)
    }
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
}

fn remove_stale_socket(path: &Path) -> io::Result<()> {
    match std::fs::metadata(path) {
        Ok(metadata) if metadata.file_type().is_socket() => std::fs::remove_file(path),
        Ok(_) => Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            format!(
                "control path exists and is not a socket: {}",
                path.display()
            ),
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn unix_nanos() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{sync::Arc, time::Duration};
    use tokio::time::timeout;

    #[tokio::test]
    async fn rest_round_trip_over_unix_socket() {
        let socket =
            std::env::temp_dir().join(format!("kairos-control-{}.sock", std::process::id()));
        let server = ControlServer::new(&socket);
        let api = ControlApi::new(Arc::<str>::from("component:market"));
        let server_task = tokio::spawn(server.serve(api));

        timeout(Duration::from_secs(1), async {
            while !socket.exists() {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("control socket ready");

        let client = RestControlClient::new(&socket);
        let health = client.health().await.expect("health response");
        assert_eq!(health["status"], "ok");

        let command = client
            .send_command("component:market", &ControlCommand::Pause { reason: None })
            .await
            .expect("command response");
        assert_eq!(command["status"], "accepted");
        assert_eq!(command["command"], "pause");

        server_task.abort();
        let _ = server_task.await;
    }
}
