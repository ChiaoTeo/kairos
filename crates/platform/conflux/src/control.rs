use std::io;
use std::net::SocketAddr;
use std::os::unix::fs::FileTypeExt;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::Router;
use axum::body::to_bytes;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Request, State};
use axum::http::header::CONTENT_TYPE;
use axum::http::{HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use futures_util::{SinkExt, StreamExt};
use kairos_protocol::control::{
    ControlAction, HttpControlCodec, HttpControlRequest, HttpControlResponse,
    WebSocketControlResponse, decode_websocket_request, encode_websocket_response,
};
use tokio::net::{TcpListener, UnixListener};
use tokio::task::JoinSet;
use tracing::Instrument;

use crate::{
    Conflux, ConfluxActor, ConfluxEvent, ConfluxHandle, ConfluxOutcome, HandleError, RestRequestOf,
    RestResponseOf, RunError, ShutdownMode,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum HttpControlEndpoint {
    Uds(PathBuf),
    Tcp(SocketAddr),
    WebSocketTcp { address: SocketAddr, path: String },
}

#[derive(Clone, Debug)]
pub struct HttpControlConfig {
    pub endpoints: Vec<HttpControlEndpoint>,
    pub max_body_bytes: usize,
    pub request_timeout: Duration,
    pub health_file: Option<PathBuf>,
}

impl HttpControlConfig {
    pub fn uds(socket_path: impl Into<PathBuf>) -> Self {
        Self {
            endpoints: vec![HttpControlEndpoint::Uds(socket_path.into())],
            max_body_bytes: kairos_workspace::control::MAX_HTTP_BODY_BYTES,
            request_timeout: Duration::from_secs(30),
            health_file: None,
        }
    }

    pub fn tcp(address: SocketAddr) -> Self {
        Self {
            endpoints: vec![HttpControlEndpoint::Tcp(address)],
            max_body_bytes: kairos_workspace::control::MAX_HTTP_BODY_BYTES,
            request_timeout: Duration::from_secs(30),
            health_file: None,
        }
    }

    pub fn websocket_tcp(address: SocketAddr, path: impl Into<String>) -> Self {
        Self {
            endpoints: vec![HttpControlEndpoint::WebSocketTcp {
                address,
                path: path.into(),
            }],
            max_body_bytes: kairos_workspace::control::MAX_HTTP_BODY_BYTES,
            request_timeout: Duration::from_secs(30),
            health_file: None,
        }
    }

    pub fn with_endpoint(mut self, endpoint: HttpControlEndpoint) -> Self {
        self.endpoints.push(endpoint);
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
pub enum HttpControlRunError<E> {
    #[error("HTTP control configuration requires at least one endpoint")]
    NoEndpoints,
    #[error("HTTP control body limit must be greater than zero")]
    ZeroBodyLimit,
    #[error("control request timeout must be greater than zero")]
    ZeroRequestTimeout,
    #[error("HTTP control transport failed: {0}")]
    Io(#[from] io::Error),
    #[error("Conflux process task failed: {0}")]
    ProcessTask(#[from] tokio::task::JoinError),
    #[error("Conflux process failed: {0}")]
    Process(#[from] RunError<E>),
    #[error("{component} Actor stopped before readiness completed")]
    ReadinessStopped { component: &'static str },
    #[error("{component} Actor omitted its readiness response")]
    ReadinessResponseMissing { component: &'static str },
    #[error("{component} readiness returned HTTP {status}")]
    ReadinessRejected {
        component: &'static str,
        status: u16,
    },
    #[error("an HTTP control server stopped before its Conflux process")]
    ServerStopped,
}

struct HttpControlState<A: ConfluxActor, C> {
    handle: ConfluxHandle<A>,
    codec: Arc<C>,
    max_body_bytes: usize,
    request_timeout: Duration,
}

pub struct HttpControlledConflux<A: ConfluxActor, C> {
    conflux: Conflux<A>,
    handle: ConfluxHandle<A>,
    codec: C,
    config: HttpControlConfig,
}

impl<A: ConfluxActor, C> Clone for HttpControlState<A, C> {
    fn clone(&self) -> Self {
        Self {
            handle: self.handle.clone(),
            codec: Arc::clone(&self.codec),
            max_body_bytes: self.max_body_bytes,
            request_timeout: self.request_timeout,
        }
    }
}

impl<A: ConfluxActor> Conflux<A> {
    /// Declaratively attaches framework-owned HTTP control endpoints to this
    /// process. Composition selects the codec and endpoint configuration; the
    /// returned runtime owns their complete lifecycle.
    pub fn with_http_control<C>(
        self,
        handle: ConfluxHandle<A>,
        codec: C,
        config: HttpControlConfig,
    ) -> HttpControlledConflux<A, C>
    where
        C: HttpControlCodec<Request = RestRequestOf<A>, Response = RestResponseOf<A>>,
    {
        HttpControlledConflux {
            conflux: self,
            handle,
            codec,
            config,
        }
    }

    /// Runs this Actor together with one or more framework-owned HTTP control
    /// endpoints. Every endpoint decodes into the Actor's one typed request
    /// queue; no endpoint can invoke the Actor directly.
    pub async fn run_with_http_control<C>(
        self,
        handle: ConfluxHandle<A>,
        codec: C,
        config: HttpControlConfig,
    ) -> Result<ConfluxOutcome<A>, HttpControlRunError<A::FatalError>>
    where
        C: HttpControlCodec<Request = RestRequestOf<A>, Response = RestResponseOf<A>>,
    {
        if config.endpoints.is_empty() {
            return Err(HttpControlRunError::NoEndpoints);
        }
        if config.max_body_bytes == 0 {
            return Err(HttpControlRunError::ZeroBodyLimit);
        }
        if config.request_timeout.is_zero() {
            return Err(HttpControlRunError::ZeroRequestTimeout);
        }

        let codec = Arc::new(codec);
        let mut servers = JoinSet::new();
        let mut uds_paths = UdsPaths::default();
        for endpoint in &config.endpoints {
            let state = HttpControlState {
                handle: handle.clone(),
                codec: Arc::clone(&codec),
                max_body_bytes: config.max_body_bytes,
                request_timeout: config.request_timeout,
            };
            match endpoint {
                HttpControlEndpoint::Uds(path) => {
                    let router = control_router(state);
                    prepare_uds(path).await?;
                    let listener = UnixListener::bind(path)?;
                    uds_paths.0.push(path.clone());
                    servers.spawn(async move { axum::serve(listener, router).await });
                },
                HttpControlEndpoint::Tcp(address) => {
                    let router = control_router(state);
                    let listener = TcpListener::bind(address).await?;
                    servers.spawn(async move { axum::serve(listener, router).await });
                },
                HttpControlEndpoint::WebSocketTcp { address, path } => {
                    let router = websocket_router(state, path);
                    let listener = TcpListener::bind(address).await?;
                    servers.spawn(async move { axum::serve(listener, router).await });
                },
            }
        }

        let process = tokio::task::spawn_local(self.run());
        let readiness = match tokio::time::timeout(
            config.request_timeout,
            handle.handle(ConfluxEvent::Rest(codec.readiness_request())),
        )
        .await
        {
            Err(_) => {
                handle.shutdown(ShutdownMode::Immediate);
                let _ = process.await;
                return Err(HttpControlRunError::ReadinessStopped {
                    component: codec.component(),
                });
            },
            Ok(result) => match result {
                Ok(Some(response)) => response,
                Ok(None) => {
                    handle.shutdown(ShutdownMode::Immediate);
                    let _ = process.await;
                    return Err(HttpControlRunError::ReadinessResponseMissing {
                        component: codec.component(),
                    });
                },
                Err(_) => {
                    handle.shutdown(ShutdownMode::Immediate);
                    let _ = process.await;
                    return Err(HttpControlRunError::ReadinessStopped {
                        component: codec.component(),
                    });
                },
            },
        };
        let readiness = codec.encode(readiness);
        if !readiness.is_success() {
            handle.shutdown(ShutdownMode::Immediate);
            let _ = process.await;
            return Err(HttpControlRunError::ReadinessRejected {
                component: codec.component(),
                status: readiness.status,
            });
        }
        if let Err(error) = write_health(config.health_file.as_deref(), "ready").await {
            handle.shutdown(ShutdownMode::Immediate);
            let _ = process.await;
            return Err(HttpControlRunError::Io(error));
        }
        kairos_workspace::logging::record_gauge("kairos.process.ready", 1);

        tokio::pin!(process);
        let outcome = tokio::select! {
            outcome = &mut process => outcome??,
            server = servers.join_next() => {
                handle.shutdown(ShutdownMode::Immediate);
                match server {
                    Some(Ok(Ok(()))) | None => {},
                    Some(Ok(Err(error))) => {
                        let _ = process.await;
                        return Err(HttpControlRunError::Io(error));
                    },
                    Some(Err(error)) => {
                        let _ = process.await;
                        return Err(HttpControlRunError::ProcessTask(error));
                    },
                }
                let _ = process.await;
                return Err(HttpControlRunError::ServerStopped);
            },
        };

        servers.abort_all();
        while servers.join_next().await.is_some() {}
        write_health(config.health_file.as_deref(), "stopped").await?;
        Ok(outcome)
    }
}

fn websocket_router<A, C>(state: HttpControlState<A, C>, path: &str) -> Router
where
    A: ConfluxActor,
    C: HttpControlCodec<Request = RestRequestOf<A>, Response = RestResponseOf<A>>,
{
    Router::new()
        .route(path, get(websocket_handler::<A, C>))
        .with_state(state)
}

async fn websocket_handler<A, C>(
    State(state): State<HttpControlState<A, C>>,
    upgrade: WebSocketUpgrade,
) -> Response
where
    A: ConfluxActor,
    C: HttpControlCodec<Request = RestRequestOf<A>, Response = RestResponseOf<A>>,
{
    upgrade
        .max_message_size(state.max_body_bytes.saturating_add(64 * 1024))
        .on_upgrade(move |socket| websocket_session(socket, state))
        .into_response()
}

async fn websocket_session<A, C>(socket: WebSocket, state: HttpControlState<A, C>)
where
    A: ConfluxActor,
    C: HttpControlCodec<Request = RestRequestOf<A>, Response = RestResponseOf<A>>,
{
    let (mut sender, mut receiver) = socket.split();
    while let Some(message) = receiver.next().await {
        let response = match message {
            Ok(Message::Binary(frame)) => match decode_websocket_request(&frame) {
                Ok(request) if request.body.len() <= state.max_body_bytes => {
                    let response =
                        dispatch_control(&state, &request.method, &request.target, &request.body)
                            .await;
                    WebSocketControlResponse {
                        request_id: request.request_id,
                        response,
                    }
                },
                Ok(request) => WebSocketControlResponse {
                    request_id: request.request_id,
                    response: control_error(413, "request body too large"),
                },
                Err(error) => WebSocketControlResponse {
                    request_id: 0,
                    response: control_error(400, &error.to_string()),
                },
            },
            Ok(Message::Ping(payload)) => {
                if sender.send(Message::Pong(payload)).await.is_err() {
                    break;
                }
                continue;
            },
            Ok(Message::Close(_)) | Err(_) => break,
            Ok(Message::Text(_)) => WebSocketControlResponse {
                request_id: 0,
                response: control_error(415, "binary control frame required"),
            },
            Ok(Message::Pong(_)) => continue,
        };
        let frame = encode_websocket_response(&response).unwrap_or_else(|error| {
            encode_websocket_response(&WebSocketControlResponse {
                request_id: response.request_id,
                response: control_error(500, &error.to_string()),
            })
            .expect("small platform error must fit a control frame")
        });
        if sender.send(Message::Binary(frame.into())).await.is_err() {
            break;
        }
    }
}

impl<A, C> HttpControlledConflux<A, C>
where
    A: ConfluxActor,
    C: HttpControlCodec<Request = RestRequestOf<A>, Response = RestResponseOf<A>>,
{
    pub async fn run(self) -> Result<ConfluxOutcome<A>, HttpControlRunError<A::FatalError>> {
        self.conflux
            .run_with_http_control(self.handle, self.codec, self.config)
            .await
    }
}

fn control_router<A, C>(state: HttpControlState<A, C>) -> Router
where
    A: ConfluxActor,
    C: HttpControlCodec<Request = RestRequestOf<A>, Response = RestResponseOf<A>>,
{
    Router::new()
        .fallback(control_handler::<A, C>)
        .with_state(state)
}

async fn control_handler<A, C>(
    State(state): State<HttpControlState<A, C>>,
    request: Request,
) -> Response
where
    A: ConfluxActor,
    C: HttpControlCodec<Request = RestRequestOf<A>, Response = RestResponseOf<A>>,
{
    let started = Instant::now();
    let method = request.method().clone();
    let target = request.uri().path_and_query().map_or_else(
        || request.uri().path().to_owned(),
        |value| value.as_str().to_owned(),
    );
    let span = tracing::info_span!(
        "conflux.control_request",
        component = state.codec.component(),
        method = %method,
        target = %target,
        status = tracing::field::Empty,
        duration_ms = tracing::field::Empty,
    );
    kairos_workspace::logging::set_remote_parent(&span, request.headers());
    let response = control_handler_inner(state, method.as_str(), &target, request)
        .instrument(span.clone())
        .await;
    span.record("status", response.status().as_u16());
    span.record("duration_ms", started.elapsed().as_secs_f64() * 1_000.0);
    response
}

async fn control_handler_inner<A, C>(
    state: HttpControlState<A, C>,
    method: &str,
    target: &str,
    request: Request,
) -> Response
where
    A: ConfluxActor,
    C: HttpControlCodec<Request = RestRequestOf<A>, Response = RestResponseOf<A>>,
{
    let body = match to_bytes(request.into_body(), state.max_body_bytes).await {
        Ok(body) => body,
        Err(_) => return platform_error(413, "request body too large"),
    };
    wire_response(dispatch_control(&state, method, target, &body).await)
}

async fn dispatch_control<A, C>(
    state: &HttpControlState<A, C>,
    method: &str,
    target: &str,
    body: &[u8],
) -> HttpControlResponse
where
    A: ConfluxActor,
    C: HttpControlCodec<Request = RestRequestOf<A>, Response = RestResponseOf<A>>,
{
    let action = match state.codec.decode(HttpControlRequest {
        method,
        target,
        body,
    }) {
        Ok(action) => action,
        Err(response) => return response,
    };
    match action {
        ControlAction::Stop => {
            state.handle.shutdown(ShutdownMode::Drain);
            HttpControlResponse::json(202, br#"{"status":"stopping"}"#.to_vec())
        },
        ControlAction::Request(request) => {
            match tokio::time::timeout(state.request_timeout, state.handle.submit_rest(request))
                .await
            {
                Err(_) => control_failure(
                    503,
                    "not_sent",
                    "control request timed out before submission",
                ),
                Ok(Err(HandleError::Closed(_))) => control_failure(
                    503,
                    "not_sent",
                    "Conflux process rejected the request before submission",
                ),
                Ok(Err(HandleError::ActorStopped)) => {
                    unreachable!("submit does not await the Actor")
                },
                Ok(Ok(response)) => {
                    match tokio::time::timeout(state.request_timeout, response).await {
                        Err(_) => control_failure(
                            504,
                            "result_unknown",
                            "control request timed out after submission",
                        ),
                        Ok(Ok(Some(response))) => state.codec.encode(response),
                        Ok(Ok(None)) => control_error(500, "Actor omitted its control response"),
                        Ok(Err(_)) => control_failure(
                            503,
                            "result_unknown",
                            "Conflux process stopped after request submission",
                        ),
                    }
                },
            }
        },
    }
}

fn wire_response(response: HttpControlResponse) -> Response {
    let status = StatusCode::from_u16(response.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let content_type = HeaderValue::from_static(response.content_type);
    (status, [(CONTENT_TYPE, content_type)], response.body).into_response()
}

fn platform_error(status: u16, message: &str) -> Response {
    wire_response(control_error(status, message))
}

fn control_error(status: u16, message: &str) -> HttpControlResponse {
    let escaped = serde_json::to_vec(&serde_json::json!({"error": message}))
        .unwrap_or_else(|_| br#"{"error":"control failure"}"#.to_vec());
    HttpControlResponse::json(status, escaped)
}

fn control_failure(status: u16, outcome: &str, message: &str) -> HttpControlResponse {
    let body = serde_json::to_vec(&serde_json::json!({
        "error": message,
        "command_outcome": outcome,
    }))
    .unwrap_or_else(|_| br#"{"error":"control failure"}"#.to_vec());
    HttpControlResponse::json(status, body)
}

async fn prepare_uds(path: &Path) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_socket() => std::fs::remove_file(path),
        Ok(_) => Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            format!(
                "refusing to replace non-socket control path {}",
                path.display()
            ),
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

#[derive(Default)]
struct UdsPaths(Vec<PathBuf>);

impl Drop for UdsPaths {
    fn drop(&mut self) {
        for path in &self.0 {
            if std::fs::symlink_metadata(path)
                .is_ok_and(|metadata| metadata.file_type().is_socket())
            {
                let _ = std::fs::remove_file(path);
            }
        }
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
    use std::convert::Infallible;
    use std::time::Duration;

    use kairos_protocol::control::{
        ControlAction, HttpControlCodec, HttpControlRequest, HttpControlResponse,
        WebSocketControlRequest, decode_websocket_response, encode_websocket_request,
    };
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    use super::{HttpControlConfig, HttpControlEndpoint};
    use crate::{
        Conflux, ConfluxActor, ConfluxConfig, ConfluxEvent, ConfluxSystem, Context, Contract,
        RestContract,
    };

    struct TestRest;

    impl RestContract for TestRest {
        type Request = i64;
        type Response = i64;
    }

    struct TestActor;

    impl Contract for TestActor {
        type Rest = TestRest;
    }

    impl ConfluxActor for TestActor {
        type FatalError = Infallible;
        type LocalEvent = Infallible;

        async fn handle(
            &mut self,
            event: ConfluxEvent<Self, Self::LocalEvent>,
            _context: &mut Context<'_, Self>,
        ) -> Result<Option<i64>, Self::FatalError> {
            Ok(match event {
                ConfluxEvent::Rest(99) => {
                    tokio::time::sleep(Duration::from_millis(50)).await;
                    Some(100)
                },
                ConfluxEvent::Rest(value) => Some(value + 1),
                ConfluxEvent::Local(value) => match value {},
                _ => None,
            })
        }
    }

    struct TestCodec;

    impl HttpControlCodec for TestCodec {
        type Request = i64;
        type Response = i64;

        fn component(&self) -> &'static str {
            "test"
        }

        fn decode(
            &self,
            request: HttpControlRequest<'_>,
        ) -> Result<ControlAction<Self::Request>, HttpControlResponse> {
            match (request.method, request.target) {
                ("GET", "/v1/health") => Ok(ControlAction::Request(0)),
                ("POST", "/v1/stop") => Ok(ControlAction::Stop),
                ("POST", "/v1/value") => std::str::from_utf8(request.body)
                    .ok()
                    .and_then(|value| value.parse().ok())
                    .map(ControlAction::Request)
                    .ok_or_else(|| HttpControlResponse::json(422, b"invalid".to_vec())),
                _ => Err(HttpControlResponse::json(404, b"missing".to_vec())),
            }
        }

        fn encode(&self, response: Self::Response) -> HttpControlResponse {
            HttpControlResponse::json(200, response.to_string().into_bytes())
        }

        fn readiness_request(&self) -> Self::Request {
            0
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn tcp_http_uses_the_same_typed_actor_ingress() {
        let probe = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = probe.local_addr().unwrap();
        drop(probe);

        let (conflux, handle) =
            Conflux::new(TestActor, ConfluxSystem::new(), ConfluxConfig::default()).unwrap();
        let runtime = conflux.with_http_control(
            handle,
            TestCodec,
            HttpControlConfig {
                endpoints: vec![HttpControlEndpoint::Tcp(address)],
                max_body_bytes: 128,
                request_timeout: Duration::from_secs(1),
                health_file: None,
            },
        );

        tokio::task::LocalSet::new()
            .run_until(async move {
                let task = tokio::task::spawn_local(runtime.run());
                let value = request(address, "POST", "/v1/value", b"41").await;
                assert!(value.starts_with("HTTP/1.1 200"), "{value}");
                assert!(value.ends_with("42"), "{value}");

                let stop = request(address, "POST", "/v1/stop", b"").await;
                assert!(stop.starts_with("HTTP/1.1 202"), "{stop}");
                task.await.unwrap().unwrap();
            })
            .await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn tcp_websocket_uses_correlation_and_the_same_typed_actor_ingress() {
        use futures_util::{SinkExt, StreamExt};
        use tokio_tungstenite::tungstenite::Message;

        let probe = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = probe.local_addr().unwrap();
        drop(probe);

        let (conflux, handle) =
            Conflux::new(TestActor, ConfluxSystem::new(), ConfluxConfig::default()).unwrap();
        let runtime = conflux.with_http_control(
            handle,
            TestCodec,
            HttpControlConfig::websocket_tcp(address, "/control"),
        );

        tokio::task::LocalSet::new()
            .run_until(async move {
                let task = tokio::task::spawn_local(runtime.run());
                let (mut socket, _) = loop {
                    match tokio_tungstenite::connect_async(format!("ws://{address}/control")).await
                    {
                        Ok(connection) => break connection,
                        Err(_) => tokio::task::yield_now().await,
                    }
                };
                let value = WebSocketControlRequest {
                    request_id: 91,
                    method: "POST".into(),
                    target: "/v1/value".into(),
                    body: b"41".to_vec(),
                };
                socket
                    .send(Message::Binary(
                        encode_websocket_request(&value).unwrap().into(),
                    ))
                    .await
                    .unwrap();
                let Message::Binary(frame) = socket.next().await.unwrap().unwrap() else {
                    panic!("expected binary control response");
                };
                let response = decode_websocket_response(&frame).unwrap();
                assert_eq!(response.request_id, 91);
                assert_eq!(response.response.status, 200);
                assert_eq!(response.response.body, b"42");

                let stop = WebSocketControlRequest {
                    request_id: 92,
                    method: "POST".into(),
                    target: "/v1/stop".into(),
                    body: Vec::new(),
                };
                socket
                    .send(Message::Binary(
                        encode_websocket_request(&stop).unwrap().into(),
                    ))
                    .await
                    .unwrap();
                let Message::Binary(frame) = socket.next().await.unwrap().unwrap() else {
                    panic!("expected binary stop response");
                };
                let response = decode_websocket_response(&frame).unwrap();
                assert_eq!(response.request_id, 92);
                assert_eq!(response.response.status, 202);
                drop(socket);
                task.await.unwrap().unwrap();
            })
            .await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn timeout_reports_that_the_submitted_command_result_is_unknown() {
        let probe = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = probe.local_addr().unwrap();
        drop(probe);
        let (conflux, handle) =
            Conflux::new(TestActor, ConfluxSystem::new(), ConfluxConfig::default()).unwrap();
        let runtime = conflux.with_http_control(
            handle,
            TestCodec,
            HttpControlConfig::tcp(address).with_request_timeout(Duration::from_millis(5)),
        );

        tokio::task::LocalSet::new()
            .run_until(async move {
                let task = tokio::task::spawn_local(runtime.run());
                let response = request(address, "POST", "/v1/value", b"99").await;
                assert!(response.starts_with("HTTP/1.1 504"), "{response}");
                assert!(response.contains("\"command_outcome\":\"result_unknown\""));
                tokio::time::sleep(Duration::from_millis(60)).await;
                let _ = request(address, "POST", "/v1/stop", b"").await;
                task.await.unwrap().unwrap();
            })
            .await;
    }

    async fn request(
        address: std::net::SocketAddr,
        method: &str,
        path: &str,
        body: &[u8],
    ) -> String {
        let mut stream = loop {
            match tokio::net::TcpStream::connect(address).await {
                Ok(stream) => break stream,
                Err(_) => tokio::task::yield_now().await,
            }
        };
        let head = format!(
            "{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Length: {}\r\n\r\n",
            body.len()
        );
        stream.write_all(head.as_bytes()).await.unwrap();
        stream.write_all(body).await.unwrap();
        let mut response = Vec::new();
        stream.read_to_end(&mut response).await.unwrap();
        String::from_utf8(response).unwrap()
    }
}
