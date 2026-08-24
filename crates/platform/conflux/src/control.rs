use std::io;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::time::Duration;

use jsonrpsee::RpcModule;
use jsonrpsee::server::{Methods, ServerBuilder, ServerHandle, serve_with_graceful_shutdown};
use kairos_protocol::control::jsonrpc::RpcResult;
use kairos_workspace::{
    CONTROL_API_VERSION, SystemCommandResponse, SystemHealthResponse, SystemStopRequest,
};
use tokio::net::{TcpListener, UnixListener};
use tower::layer::util::Identity;

use crate::{Conflux, ConfluxActor, ConfluxHandle, ConfluxOutcome, RunError, ShutdownMode};

type JsonRpcServiceBuilder = jsonrpsee::server::TowerServiceBuilder<Identity, Identity>;

#[derive(Clone, Debug)]
pub struct JsonRpcRuntimeConfig {
    pub listeners: Vec<JsonRpcListener>,
    pub request_timeout: Duration,
    pub health_file: Option<PathBuf>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum JsonRpcListener {
    Tcp(SocketAddr),
    Unix(PathBuf),
}

impl JsonRpcRuntimeConfig {
    pub fn tcp(address: SocketAddr) -> Self {
        Self {
            listeners: vec![JsonRpcListener::Tcp(address)],
            request_timeout: Duration::from_secs(30),
            health_file: None,
        }
    }

    pub fn uds(socket: impl Into<PathBuf>) -> Self {
        Self {
            listeners: vec![JsonRpcListener::Unix(socket.into())],
            request_timeout: Duration::from_secs(30),
            health_file: None,
        }
    }

    pub fn with_tcp(mut self, address: SocketAddr) -> Self {
        self.listeners.push(JsonRpcListener::Tcp(address));
        self
    }

    pub fn with_uds(mut self, socket: impl Into<PathBuf>) -> Self {
        self.listeners.push(JsonRpcListener::Unix(socket.into()));
        self
    }

    pub fn with_health_file(mut self, path: Option<PathBuf>) -> Self {
        self.health_file = path;
        self
    }

    pub fn with_request_timeout(mut self, timeout: Duration) -> Self {
        self.request_timeout = timeout;
        self
    }
}

#[derive(Debug, thiserror::Error)]
pub enum JsonRpcRuntimeError<E> {
    #[error("JSON-RPC runtime must have at least one listener")]
    NoListeners,
    #[error("JSON-RPC request timeout must be greater than zero")]
    ZeroRequestTimeout,
    #[error("JSON-RPC transport failed: {0}")]
    Transport(#[from] jsonrpsee::core::client::Error),
    #[error("JSON-RPC listener failed: {0}")]
    Listener(#[from] io::Error),
    #[error("JSON-RPC method registration failed: {0}")]
    MethodRegistration(#[from] jsonrpsee::core::RegisterMethodError),
    #[error("JSON-RPC health file failed: {0}")]
    HealthFile(io::Error),
    #[error("Conflux process task failed: {0}")]
    ProcessTask(#[from] tokio::task::JoinError),
    #[error("Conflux process failed: {0}")]
    Process(#[from] RunError<E>),
    #[error("the JSON-RPC server stopped before its Conflux process")]
    ServerStopped,
}

pub struct JsonRpcConfluxRuntime<A: ConfluxActor> {
    conflux: Conflux<A>,
    handle: ConfluxHandle<A>,
    methods: Methods,
    config: JsonRpcRuntimeConfig,
}

impl<A: ConfluxActor> Conflux<A> {
    pub fn with_json_rpc(
        self,
        handle: ConfluxHandle<A>,
        methods: impl Into<Methods>,
        config: JsonRpcRuntimeConfig,
    ) -> JsonRpcConfluxRuntime<A> {
        JsonRpcConfluxRuntime {
            conflux: self,
            handle,
            methods: methods.into(),
            config,
        }
    }
}

impl<A> JsonRpcConfluxRuntime<A>
where
    A: ConfluxActor,
{
    pub async fn run(self) -> Result<ConfluxOutcome<A>, JsonRpcRuntimeError<A::FatalError>> {
        if self.config.listeners.is_empty() {
            return Err(JsonRpcRuntimeError::NoListeners);
        }
        if self.config.request_timeout.is_zero() {
            return Err(JsonRpcRuntimeError::ZeroRequestTimeout);
        }

        let methods = system_methods(&self.handle, self.methods)?;
        let server = JsonRpcListeners::start(&self.config.listeners, methods).await?;
        write_health(self.config.health_file.as_deref(), "ready")
            .await
            .map_err(JsonRpcRuntimeError::HealthFile)?;

        let process = tokio::task::spawn_local(self.conflux.run());
        let stopped = server.handle.clone().stopped();
        tokio::pin!(stopped);
        tokio::select! {
            outcome = process => {
                let outcome = outcome??;
                server.stop().await;
                write_health(self.config.health_file.as_deref(), "stopped")
                    .await
                    .map_err(JsonRpcRuntimeError::HealthFile)?;
                Ok(outcome)
            },
            () = &mut stopped => {
                server.cleanup().await;
                write_health(self.config.health_file.as_deref(), "stopped")
                    .await
                    .map_err(JsonRpcRuntimeError::HealthFile)?;
                self.handle.shutdown(ShutdownMode::Immediate);
                Err(JsonRpcRuntimeError::ServerStopped)
            },
        }
    }
}

fn system_methods<A: ConfluxActor>(
    handle: &ConfluxHandle<A>,
    module_methods: Methods,
) -> Result<Methods, jsonrpsee::core::RegisterMethodError> {
    let mut module = RpcModule::new(handle.clone());
    module.register_method("system_health", |_, handle, _| -> RpcResult<_> {
        Ok(SystemHealthResponse {
            status: if matches!(handle.phase(), crate::ProcessPhase::Running) {
                "ready".into()
            } else {
                "not_ready".into()
            },
            phase: phase_name(handle.phase()).into(),
            protocol: CONTROL_API_VERSION.into(),
        })
    })?;
    module.register_method("system_stop", |params, handle, _| -> RpcResult<_> {
        let request = params
            .one::<SystemStopRequest>()
            .unwrap_or_else(|_| SystemStopRequest::default());
        let mode = if request.immediate {
            ShutdownMode::Immediate
        } else {
            ShutdownMode::Drain
        };
        handle.shutdown(mode);
        Ok(SystemCommandResponse {
            status: "accepted".into(),
            command: "system_stop".into(),
            message: request
                .reason
                .unwrap_or_else(|| "shutdown requested".into()),
        })
    })?;
    let mut methods: Methods = module.into();
    methods.merge(module_methods)?;
    Ok(methods)
}

const fn phase_name(phase: crate::ProcessPhase) -> &'static str {
    match phase {
        crate::ProcessPhase::Created => "created",
        crate::ProcessPhase::Starting => "starting",
        crate::ProcessPhase::Running => "running",
        crate::ProcessPhase::Stopping => "stopping",
        crate::ProcessPhase::Stopped => "stopped",
        crate::ProcessPhase::Forced => "forced",
        crate::ProcessPhase::Failed => "failed",
    }
}

struct JsonRpcListeners {
    handle: ServerHandle,
    unix_sockets: Vec<PathBuf>,
}

impl JsonRpcListeners {
    async fn start(listeners: &[JsonRpcListener], methods: Methods) -> io::Result<Self> {
        let (stop_handle, handle) = jsonrpsee::server::stop_channel();
        let service_builder = ServerBuilder::default().to_service_builder();
        let mut unix_sockets = Vec::new();

        for listener in listeners {
            match listener {
                JsonRpcListener::Tcp(address) => {
                    let listener = TcpListener::bind(address).await?;
                    let methods = methods.clone();
                    let stop_handle = stop_handle.clone();
                    let service_builder = service_builder.clone();
                    tokio::spawn(async move {
                        run_tcp_listener(listener, methods, stop_handle, service_builder).await;
                    });
                },
                JsonRpcListener::Unix(path) => {
                    prepare_unix_socket(path).await?;
                    let listener = UnixListener::bind(path)?;
                    unix_sockets.push(path.clone());
                    let methods = methods.clone();
                    let stop_handle = stop_handle.clone();
                    let service_builder = service_builder.clone();
                    tokio::spawn(async move {
                        run_unix_listener(listener, methods, stop_handle, service_builder).await;
                    });
                },
            }
        }

        Ok(Self {
            handle,
            unix_sockets,
        })
    }

    async fn stop(self) {
        let _ = self.handle.stop();
        self.handle.clone().stopped().await;
        self.cleanup().await;
    }

    async fn cleanup(&self) {
        for socket in &self.unix_sockets {
            let _ = tokio::fs::remove_file(socket).await;
        }
    }
}

async fn run_tcp_listener(
    listener: TcpListener,
    methods: Methods,
    stop_handle: jsonrpsee::server::StopHandle,
    service_builder: JsonRpcServiceBuilder,
) {
    loop {
        let (stream, _) = tokio::select! {
            accepted = listener.accept() => match accepted {
                Ok(accepted) => accepted,
                Err(error) => {
                    tracing::warn!(event = "json_rpc_tcp_accept_failed", error = %error);
                    continue;
                },
            },
            _ = stop_handle.clone().shutdown() => break,
        };
        serve_json_rpc_stream(
            stream,
            methods.clone(),
            stop_handle.clone(),
            service_builder.clone(),
        );
    }
}

async fn run_unix_listener(
    listener: UnixListener,
    methods: Methods,
    stop_handle: jsonrpsee::server::StopHandle,
    service_builder: JsonRpcServiceBuilder,
) {
    loop {
        let (stream, _) = tokio::select! {
            accepted = listener.accept() => match accepted {
                Ok(accepted) => accepted,
                Err(error) => {
                    tracing::warn!(event = "json_rpc_uds_accept_failed", error = %error);
                    continue;
                },
            },
            _ = stop_handle.clone().shutdown() => break,
        };
        serve_json_rpc_stream(
            stream,
            methods.clone(),
            stop_handle.clone(),
            service_builder.clone(),
        );
    }
}

fn serve_json_rpc_stream<S>(
    stream: S,
    methods: Methods,
    stop_handle: jsonrpsee::server::StopHandle,
    service_builder: JsonRpcServiceBuilder,
) where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Send + Unpin + 'static,
{
    let service = service_builder.build(methods, stop_handle.clone());
    let shutdown_stop_handle = stop_handle.clone();
    tokio::spawn(async move {
        if let Err(error) =
            serve_with_graceful_shutdown(stream, service, shutdown_stop_handle.shutdown()).await
        {
            tracing::warn!(event = "json_rpc_connection_failed", error = %error);
        }
    });
}

async fn prepare_unix_socket(path: &Path) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    match tokio::fs::remove_file(path).await {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

async fn write_health(path: Option<&Path>, status: &str) -> io::Result<()> {
    let Some(path) = path else {
        return Ok(());
    };
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let temporary = path.with_extension("tmp");
    let body =
        serde_json::to_vec(&serde_json::json!({"status": status})).map_err(io::Error::other)?;
    tokio::fs::write(&temporary, body).await?;
    tokio::fs::rename(temporary, path).await
}

#[cfg(test)]
mod tests {
    use jsonrpsee::RpcModule;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    use super::{JsonRpcListener, JsonRpcListeners};

    #[tokio::test]
    async fn uds_listener_serves_http_json_rpc() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("json-rpc.sock");
        let mut module = RpcModule::new(());
        module
            .register_method("ping", |_, _, _| "pong")
            .expect("register ping method");

        let listeners =
            JsonRpcListeners::start(&[JsonRpcListener::Unix(socket.clone())], module.into())
                .await
                .expect("start UDS JSON-RPC listener");

        let mut stream = tokio::net::UnixStream::connect(&socket)
            .await
            .expect("connect JSON-RPC socket");
        let body = r#"{"jsonrpc":"2.0","method":"ping","id":1}"#;
        let request = format!(
            "POST / HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            body
        );
        stream
            .write_all(request.as_bytes())
            .await
            .expect("write JSON-RPC request");

        let mut response = String::new();
        stream
            .read_to_string(&mut response)
            .await
            .expect("read JSON-RPC response");
        listeners.stop().await;

        assert!(response.contains("200 OK"), "{response}");
        assert!(response.contains(r#""result":"pong""#), "{response}");
    }
}
