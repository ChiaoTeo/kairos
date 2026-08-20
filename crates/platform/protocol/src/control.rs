//! Shared control-boundary mechanics.
//!
//! Business modules own their typed requests, responses, and wire mappings.
//! Platform runtimes own listeners, sessions, queues, and lifecycle.  These
//! types let the two sides meet without exposing an HTTP or socket type to a
//! business Actor.

use std::collections::BTreeMap;

use serde::{Serialize, de::DeserializeOwned};

/// jsonrpsee-backed control service helpers.
///
/// Business contract crates should define new control services with
/// `kairos_protocol::control::jsonrpc::rpc`. Runtimes such as Conflux then
/// adapt the generated server trait into their own ingress instead of owning
/// the protocol definition.
pub mod jsonrpc {
    pub use jsonrpsee::core::async_trait;
    pub use jsonrpsee::core::RpcResult;
    pub use jsonrpsee::proc_macros::rpc;
    pub use jsonrpsee::types::ErrorObjectOwned;

    pub const NOT_SENT_CODE: i32 = -32_001;
    pub const RESULT_UNKNOWN_CODE: i32 = -32_002;
    pub const ACTOR_STOPPED_CODE: i32 = -32_003;
    pub const READINESS_REJECTED_CODE: i32 = -32_004;

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub enum ControlRuntimeFailure {
        NotSent,
        ResultUnknown,
        ActorStopped,
        ReadinessRejected,
    }

    impl ControlRuntimeFailure {
        pub fn code(self) -> i32 {
            match self {
                Self::NotSent => NOT_SENT_CODE,
                Self::ResultUnknown => RESULT_UNKNOWN_CODE,
                Self::ActorStopped => ACTOR_STOPPED_CODE,
                Self::ReadinessRejected => READINESS_REJECTED_CODE,
            }
        }

        pub fn reason(self) -> &'static str {
            match self {
                Self::NotSent => "not_sent",
                Self::ResultUnknown => "result_unknown",
                Self::ActorStopped => "actor_stopped",
                Self::ReadinessRejected => "readiness_rejected",
            }
        }

        pub fn message(self) -> &'static str {
            match self {
                Self::NotSent => "control request was not submitted",
                Self::ResultUnknown => "control request result is unknown",
                Self::ActorStopped => "control actor stopped",
                Self::ReadinessRejected => "control readiness was rejected",
            }
        }

        pub fn into_error(self) -> ErrorObjectOwned {
            ErrorObjectOwned::owned(self.code(), self.message(), Some(self.reason()))
        }
    }

    pub fn runtime_error(failure: ControlRuntimeFailure) -> ErrorObjectOwned {
        failure.into_error()
    }

    pub fn business_error(
        code: i32,
        message: impl Into<String>,
        details: impl Serialize,
    ) -> ErrorObjectOwned {
        ErrorObjectOwned::owned(code, message.into(), Some(details))
    }

    use serde::Serialize;
}

/// One HTTP request after transport framing but before Contract decoding.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct HttpControlRequest<'a> {
    pub method: &'a str,
    pub target: &'a str,
    pub body: &'a [u8],
}

/// A transport-ready HTTP response produced by a Contract wire codec.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HttpControlResponse {
    pub status: u16,
    pub content_type: &'static str,
    pub body: Vec<u8>,
}

impl HttpControlResponse {
    pub fn json(status: u16, body: Vec<u8>) -> Self {
        Self {
            status,
            content_type: "application/json",
            body,
        }
    }

    pub fn is_success(&self) -> bool {
        (200..300).contains(&self.status)
    }
}

/// A platform lifecycle operation or a typed business request decoded from
/// one control transport request.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ControlAction<R> {
    Request(R),
    Stop,
}

/// One typed service operation declared by an owner Contract.
///
/// The operation owns the 1:1 request/reply pairing. Aggregated runtime
/// messages, if needed by an adapter, should be generated from operations
/// rather than handwritten as a second source of truth.
pub trait ControlOperation {
    type Request: Send + 'static;
    type Reply: Send + 'static;

    const NAME: &'static str;
}

/// A closed owner-defined control service.
///
/// This is the protocol-level shape that runtimes such as Conflux can consume.
/// It is intentionally not Conflux-specific: test, CLI, mock, or other
/// transport adapters can use the same service definition.
pub trait ControlService: Send + Sync + 'static {
    type Message: Send + 'static;
    type Reply: Send + 'static;

    fn component(&self) -> &'static str;

    fn decode(
        &self,
        request: ControlTransportRequest<'_>,
    ) -> Result<ControlAction<Self::Message>, ControlTransportResponse>;

    fn encode(&self, reply: Self::Reply) -> ControlTransportResponse;

    fn readiness_message(&self) -> Self::Message;
}

/// HTTP-shaped transport input consumed by a control service adapter.
///
/// This type alias keeps the current wire mechanics compatible while allowing
/// new protocol code to avoid exposing the older REST-oriented names.
pub type ControlTransportRequest<'a> = HttpControlRequest<'a>;

/// HTTP-shaped transport output produced by a control service adapter.
pub type ControlTransportResponse = HttpControlResponse;

/// Request pieces after method/path/query matching.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ControlRequestParts<'a> {
    pub method: &'a str,
    pub path: &'a str,
    pub query: ControlQuery<'a>,
    pub path_params: ControlPathParams<'a>,
    pub body: &'a [u8],
}

impl<'a> ControlRequestParts<'a> {
    pub fn from_transport(request: ControlTransportRequest<'a>) -> Self {
        let (path, query) = split_target(request.target);
        Self {
            method: request.method,
            path,
            query: ControlQuery::new(query),
            path_params: ControlPathParams::default(),
            body: request.body,
        }
    }

    pub fn with_path_params(mut self, path_params: ControlPathParams<'a>) -> Self {
        self.path_params = path_params;
        self
    }
}

/// Query string accessor for control service decoders.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ControlQuery<'a> {
    raw: &'a str,
}

impl<'a> ControlQuery<'a> {
    pub fn new(raw: &'a str) -> Self {
        Self { raw }
    }

    pub fn raw(&self) -> &'a str {
        self.raw
    }

    pub fn get(&self, name: &str) -> Option<&'a str> {
        self.raw.split('&').find_map(|part| {
            let (key, value) = part.split_once('=')?;
            (key == name).then_some(value)
        })
    }

    pub fn required(&self, name: &str) -> Result<&'a str, ControlTransportResponse> {
        self.get(name)
            .ok_or_else(|| control_error(422, format!("{name} is required")))
    }
}

/// Path parameters captured by an adapter route match.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ControlPathParams<'a> {
    values: BTreeMap<&'a str, &'a str>,
}

impl<'a> ControlPathParams<'a> {
    pub fn new(values: impl IntoIterator<Item = (&'a str, &'a str)>) -> Self {
        Self {
            values: values.into_iter().collect(),
        }
    }

    pub fn get(&self, name: &str) -> Option<&'a str> {
        self.values.get(name).copied()
    }

    pub fn required(&self, name: &str) -> Result<&'a str, ControlTransportResponse> {
        self.get(name)
            .ok_or_else(|| control_error(422, format!("{name} is required")))
    }
}

/// JSON helpers for control service adapters.
pub struct JsonBody;

impl JsonBody {
    pub fn required<T: DeserializeOwned>(body: &[u8]) -> Result<T, ControlTransportResponse> {
        serde_json::from_slice(body).map_err(|cause| {
            json_response(
                400,
                &serde_json::json!({
                    "error": "invalid control request",
                    "details": cause.to_string(),
                }),
            )
        })
    }

    pub fn optional_or_default<T: DeserializeOwned + Default>(
        body: &[u8],
    ) -> Result<T, ControlTransportResponse> {
        if body.is_empty() {
            Ok(T::default())
        } else {
            Self::required(body)
        }
    }

    pub fn response<T: Serialize>(status: u16, value: &T) -> ControlTransportResponse {
        json_response(status, value)
    }
}

pub fn control_error(status: u16, message: impl Into<String>) -> ControlTransportResponse {
    json_response(status, &serde_json::json!({"error": message.into()}))
}

pub fn json_response<T: Serialize>(status: u16, value: &T) -> ControlTransportResponse {
    match serde_json::to_vec(value) {
        Ok(body) => ControlTransportResponse::json(status, body),
        Err(_) => {
            ControlTransportResponse::json(500, br#"{"error":"encode control response"}"#.to_vec())
        },
    }
}

pub fn split_target(target: &str) -> (&str, &str) {
    target.split_once('?').unwrap_or((target, ""))
}

/// Module-owned HTTP mapping for one closed typed control Contract.
///
/// Implementations belong to the module Contract crate.  They may select
/// methods and paths and encode their concrete request/response types, but do
/// not bind listeners or own server lifecycle.
pub trait HttpControlCodec: Send + Sync + 'static {
    type Request: Send + 'static;
    type Response: Send + 'static;

    fn component(&self) -> &'static str;

    fn decode(
        &self,
        request: HttpControlRequest<'_>,
    ) -> Result<ControlAction<Self::Request>, HttpControlResponse>;

    fn encode(&self, response: Self::Response) -> HttpControlResponse;

    /// A typed request used to prove that the Actor has completed startup and
    /// is accepting work before process readiness is published.
    fn readiness_request(&self) -> Self::Request;
}

const WEBSOCKET_MAGIC: &[u8; 4] = b"KCTL";
const WEBSOCKET_VERSION: u8 = 1;
const WEBSOCKET_REQUEST: u8 = 1;
const WEBSOCKET_RESPONSE: u8 = 2;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WebSocketControlRequest {
    pub request_id: u64,
    pub method: String,
    pub target: String,
    pub body: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WebSocketControlResponse {
    pub request_id: u64,
    pub response: HttpControlResponse,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ControlFrameError(pub String);

impl std::fmt::Display for ControlFrameError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ControlFrameError {}

pub fn encode_websocket_request(
    request: &WebSocketControlRequest,
) -> Result<Vec<u8>, ControlFrameError> {
    let method = request.method.as_bytes();
    let target = request.target.as_bytes();
    let method_len = u16::try_from(method.len())
        .map_err(|_| ControlFrameError("control method is too long".into()))?;
    let target_len = u16::try_from(target.len())
        .map_err(|_| ControlFrameError("control target is too long".into()))?;
    let body_len = u32::try_from(request.body.len())
        .map_err(|_| ControlFrameError("control body is too large".into()))?;
    let mut frame = Vec::with_capacity(22 + method.len() + target.len() + request.body.len());
    frame.extend_from_slice(WEBSOCKET_MAGIC);
    frame.push(WEBSOCKET_VERSION);
    frame.push(WEBSOCKET_REQUEST);
    frame.extend_from_slice(&request.request_id.to_be_bytes());
    frame.extend_from_slice(&method_len.to_be_bytes());
    frame.extend_from_slice(&target_len.to_be_bytes());
    frame.extend_from_slice(&body_len.to_be_bytes());
    frame.extend_from_slice(method);
    frame.extend_from_slice(target);
    frame.extend_from_slice(&request.body);
    Ok(frame)
}

pub fn decode_websocket_request(
    frame: &[u8],
) -> Result<WebSocketControlRequest, ControlFrameError> {
    let mut cursor = FrameCursor::new(frame, WEBSOCKET_REQUEST)?;
    let request_id = cursor.u64()?;
    let method_len = cursor.u16()? as usize;
    let target_len = cursor.u16()? as usize;
    let body_len = cursor.u32()? as usize;
    let method = cursor.utf8(method_len, "method")?;
    let target = cursor.utf8(target_len, "target")?;
    let body = cursor.bytes(body_len)?.to_vec();
    cursor.finish()?;
    Ok(WebSocketControlRequest {
        request_id,
        method,
        target,
        body,
    })
}

pub fn encode_websocket_response(
    response: &WebSocketControlResponse,
) -> Result<Vec<u8>, ControlFrameError> {
    let content_type = response.response.content_type.as_bytes();
    let content_type_len = u16::try_from(content_type.len())
        .map_err(|_| ControlFrameError("control content type is too long".into()))?;
    let body_len = u32::try_from(response.response.body.len())
        .map_err(|_| ControlFrameError("control body is too large".into()))?;
    let mut frame = Vec::with_capacity(22 + content_type.len() + response.response.body.len());
    frame.extend_from_slice(WEBSOCKET_MAGIC);
    frame.push(WEBSOCKET_VERSION);
    frame.push(WEBSOCKET_RESPONSE);
    frame.extend_from_slice(&response.request_id.to_be_bytes());
    frame.extend_from_slice(&response.response.status.to_be_bytes());
    frame.extend_from_slice(&content_type_len.to_be_bytes());
    frame.extend_from_slice(&body_len.to_be_bytes());
    frame.extend_from_slice(content_type);
    frame.extend_from_slice(&response.response.body);
    Ok(frame)
}

pub fn decode_websocket_response(
    frame: &[u8],
) -> Result<WebSocketControlResponse, ControlFrameError> {
    let mut cursor = FrameCursor::new(frame, WEBSOCKET_RESPONSE)?;
    let request_id = cursor.u64()?;
    let status = cursor.u16()?;
    let content_type_len = cursor.u16()? as usize;
    let body_len = cursor.u32()? as usize;
    let content_type = cursor.utf8(content_type_len, "content type")?;
    let content_type = match content_type.as_str() {
        "application/json" => "application/json",
        _ => return Err(ControlFrameError("unsupported control content type".into())),
    };
    let body = cursor.bytes(body_len)?.to_vec();
    cursor.finish()?;
    Ok(WebSocketControlResponse {
        request_id,
        response: HttpControlResponse {
            status,
            content_type,
            body,
        },
    })
}

struct FrameCursor<'a> {
    frame: &'a [u8],
    offset: usize,
}

impl<'a> FrameCursor<'a> {
    fn new(frame: &'a [u8], kind: u8) -> Result<Self, ControlFrameError> {
        if frame.len() < 6 || &frame[..4] != WEBSOCKET_MAGIC {
            return Err(ControlFrameError("invalid control frame magic".into()));
        }
        if frame[4] != WEBSOCKET_VERSION {
            return Err(ControlFrameError(format!(
                "unsupported control frame version {}",
                frame[4]
            )));
        }
        if frame[5] != kind {
            return Err(ControlFrameError("unexpected control frame kind".into()));
        }
        Ok(Self { frame, offset: 6 })
    }

    fn bytes(&mut self, length: usize) -> Result<&'a [u8], ControlFrameError> {
        let end = self
            .offset
            .checked_add(length)
            .ok_or_else(|| ControlFrameError("control frame length overflow".into()))?;
        let value = self
            .frame
            .get(self.offset..end)
            .ok_or_else(|| ControlFrameError("truncated control frame".into()))?;
        self.offset = end;
        Ok(value)
    }

    fn u16(&mut self) -> Result<u16, ControlFrameError> {
        let bytes: [u8; 2] = self.bytes(2)?.try_into().expect("exact frame field");
        Ok(u16::from_be_bytes(bytes))
    }

    fn u32(&mut self) -> Result<u32, ControlFrameError> {
        let bytes: [u8; 4] = self.bytes(4)?.try_into().expect("exact frame field");
        Ok(u32::from_be_bytes(bytes))
    }

    fn u64(&mut self) -> Result<u64, ControlFrameError> {
        let bytes: [u8; 8] = self.bytes(8)?.try_into().expect("exact frame field");
        Ok(u64::from_be_bytes(bytes))
    }

    fn utf8(&mut self, length: usize, field: &str) -> Result<String, ControlFrameError> {
        std::str::from_utf8(self.bytes(length)?)
            .map(str::to_owned)
            .map_err(|_| ControlFrameError(format!("control {field} is not UTF-8")))
    }

    fn finish(self) -> Result<(), ControlFrameError> {
        if self.offset == self.frame.len() {
            Ok(())
        } else {
            Err(ControlFrameError("control frame has trailing bytes".into()))
        }
    }
}

#[cfg(test)]
mod tests {
    use serde::{Deserialize, Serialize};

    use super::{
        ControlAction, ControlOperation, ControlPathParams, ControlRequestParts, ControlService,
        ControlTransportRequest, HttpControlResponse, JsonBody, WebSocketControlRequest,
        WebSocketControlResponse, decode_websocket_request, decode_websocket_response,
        encode_websocket_request, encode_websocket_response, split_target,
    };

    #[test]
    fn success_is_defined_by_the_http_status_class() {
        assert!(HttpControlResponse::json(200, Vec::new()).is_success());
        assert!(HttpControlResponse::json(299, Vec::new()).is_success());
        assert!(!HttpControlResponse::json(300, Vec::new()).is_success());
        assert!(!HttpControlResponse::json(422, Vec::new()).is_success());
    }

    #[test]
    fn websocket_frames_preserve_correlation_and_http_wire_facts() {
        let request = WebSocketControlRequest {
            request_id: 42,
            method: "POST".into(),
            target: "/v1/value".into(),
            body: b"payload".to_vec(),
        };
        let encoded = encode_websocket_request(&request).unwrap();
        assert_eq!(decode_websocket_request(&encoded).unwrap(), request);

        let response = WebSocketControlResponse {
            request_id: 42,
            response: HttpControlResponse::json(202, b"accepted".to_vec()),
        };
        let encoded = encode_websocket_response(&response).unwrap();
        assert_eq!(decode_websocket_response(&encoded).unwrap(), response);
    }

    #[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
    struct EchoRequest {
        value: String,
    }

    #[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
    struct EchoReply {
        value: String,
    }

    struct Echo;

    impl ControlOperation for Echo {
        type Request = EchoRequest;
        type Reply = EchoReply;

        const NAME: &'static str = "Echo";
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    enum DemoMessage {
        Echo(<Echo as ControlOperation>::Request),
        Health,
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    enum DemoReply {
        Echo(<Echo as ControlOperation>::Reply),
        Health,
    }

    struct DemoService;

    impl ControlService for DemoService {
        type Message = DemoMessage;
        type Reply = DemoReply;

        fn component(&self) -> &'static str {
            "demo"
        }

        fn decode(
            &self,
            request: ControlTransportRequest<'_>,
        ) -> Result<ControlAction<Self::Message>, super::ControlTransportResponse> {
            let parts = ControlRequestParts::from_transport(request);
            match (parts.method, parts.path) {
                ("GET", "/v1/health") => Ok(ControlAction::Request(DemoMessage::Health)),
                ("POST", "/v1/echo") => Ok(ControlAction::Request(DemoMessage::Echo(
                    JsonBody::required(parts.body)?,
                ))),
                _ => Err(super::control_error(404, "unknown demo endpoint")),
            }
        }

        fn encode(&self, reply: Self::Reply) -> super::ControlTransportResponse {
            match reply {
                DemoReply::Echo(reply) => JsonBody::response(200, &reply),
                DemoReply::Health => JsonBody::response(200, &serde_json::json!({"status":"ok"})),
            }
        }

        fn readiness_message(&self) -> Self::Message {
            DemoMessage::Health
        }
    }

    #[test]
    fn control_service_decodes_and_encodes_typed_operation() {
        let service = DemoService;
        let decoded = service
            .decode(ControlTransportRequest {
                method: "POST",
                target: "/v1/echo",
                body: br#"{"value":"hello"}"#,
            })
            .unwrap();
        assert_eq!(
            decoded,
            ControlAction::Request(DemoMessage::Echo(EchoRequest {
                value: "hello".into(),
            }))
        );

        let encoded = service.encode(DemoReply::Echo(EchoReply {
            value: "world".into(),
        }));
        assert_eq!(encoded.status, 200);
        assert_eq!(encoded.content_type, "application/json");
        assert_eq!(encoded.body, br#"{"value":"world"}"#);
        assert_eq!(service.encode(DemoReply::Health).status, 200);
        assert_eq!(service.readiness_message(), DemoMessage::Health);
    }

    #[test]
    fn control_request_parts_split_query_and_path_params() {
        let parts = ControlRequestParts::from_transport(ControlTransportRequest {
            method: "GET",
            target: "/v1/orders/order-1?account_id=main",
            body: &[],
        })
        .with_path_params(ControlPathParams::new([("order_id", "order-1")]));

        assert_eq!(parts.path, "/v1/orders/order-1");
        assert_eq!(parts.query.get("account_id"), Some("main"));
        assert_eq!(parts.query.required("account_id").unwrap(), "main");
        assert_eq!(parts.path_params.required("order_id").unwrap(), "order-1");
        assert_eq!(
            parts.path_params.required("missing").unwrap_err().status,
            422
        );
        assert_eq!(
            split_target("/v1/health?verbose=true"),
            ("/v1/health", "verbose=true")
        );
    }

    #[test]
    fn json_body_supports_required_and_default_bodies() {
        let request: EchoRequest = JsonBody::required(br#"{"value":"hello"}"#).unwrap();
        assert_eq!(request.value, "hello");

        #[derive(Debug, Default, Deserialize, PartialEq)]
        struct OptionalBody {
            value: Option<String>,
        }

        assert_eq!(
            JsonBody::optional_or_default::<OptionalBody>(&[]).unwrap(),
            OptionalBody::default()
        );
        assert_eq!(
            JsonBody::required::<EchoRequest>(b"not-json")
                .unwrap_err()
                .status,
            400
        );
    }

    #[test]
    fn jsonrpc_runtime_failures_have_stable_error_codes() {
        let error = super::jsonrpc::runtime_error(super::jsonrpc::ControlRuntimeFailure::NotSent);
        assert_eq!(error.code(), super::jsonrpc::NOT_SENT_CODE);
        assert_eq!(error.message(), "control request was not submitted");

        let business = super::jsonrpc::business_error(
            -31_000,
            "business rejected request",
            serde_json::json!({"reason":"invalid"}),
        );
        assert_eq!(business.code(), -31_000);
        assert_eq!(business.message(), "business rejected request");
    }
}
