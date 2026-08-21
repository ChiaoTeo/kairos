use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

pub const CONTROL_API_VERSION: &str = "jsonrpc-2.0";
pub const MAX_HTTP_BODY_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SystemHealthResponse {
    pub status: String,
    pub phase: String,
    pub protocol: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct SystemStopRequest {
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub immediate: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SystemCommandResponse {
    pub status: String,
    pub command: String,
    pub message: String,
}

#[derive(Debug)]
pub struct JsonRpcControlClient {
    socket_path: PathBuf,
    next_id: AtomicU64,
}

impl Clone for JsonRpcControlClient {
    fn clone(&self) -> Self {
        Self {
            socket_path: self.socket_path.clone(),
            next_id: AtomicU64::new(self.next_id.load(Ordering::Relaxed)),
        }
    }
}

impl PartialEq for JsonRpcControlClient {
    fn eq(&self, other: &Self) -> bool {
        self.socket_path == other.socket_path
    }
}

impl Eq for JsonRpcControlClient {}

impl JsonRpcControlClient {
    pub fn new(socket_path: impl Into<PathBuf>) -> Self {
        Self {
            socket_path: socket_path.into(),
            next_id: AtomicU64::new(1),
        }
    }

    pub fn socket_path(&self) -> &Path {
        &self.socket_path
    }

    pub async fn system_health(&self) -> io::Result<SystemHealthResponse> {
        self.call("system_health", serde_json::json!([])).await
    }

    pub async fn system_stop(
        &self,
        request: SystemStopRequest,
    ) -> io::Result<SystemCommandResponse> {
        self.call("system_stop", serde_json::json!([request])).await
    }

    pub async fn call<T: DeserializeOwned>(&self, method: &str, params: Value) -> io::Result<T> {
        let value = self.call_value(method, params).await?;
        serde_json::from_value(value)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
    }

    pub async fn call_value(&self, method: &str, params: Value) -> io::Result<Value> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let body = serde_json::json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        })
        .to_string();
        if body.len() > MAX_HTTP_BODY_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "JSON-RPC request body is too large",
            ));
        }
        let request = format!(
            "POST / HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        let mut stream = tokio::net::UnixStream::connect(&self.socket_path).await?;
        stream.write_all(request.as_bytes()).await?;
        stream.shutdown().await?;
        let mut response = Vec::new();
        stream.read_to_end(&mut response).await?;
        let response = String::from_utf8(response)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        let (_, body) = response
            .split_once("\r\n\r\n")
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing HTTP body"))?;
        let payload: Value = serde_json::from_str(body)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        if let Some(error) = payload.get("error") {
            return Err(io::Error::other(format!(
                "JSON-RPC {method} failed: {error}"
            )));
        }
        payload
            .get("result")
            .cloned()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing JSON-RPC result"))
    }

    pub fn blocking_call<T: DeserializeOwned>(&self, method: &str, params: Value) -> io::Result<T> {
        let value = self.blocking_call_value(method, params)?;
        serde_json::from_value(value)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
    }

    pub fn blocking_call_value(&self, method: &str, params: Value) -> io::Result<Value> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let body = serde_json::json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        })
        .to_string();
        if body.len() > MAX_HTTP_BODY_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "JSON-RPC request body is too large",
            ));
        }
        let request = format!(
            "POST / HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        let mut stream = std::os::unix::net::UnixStream::connect(&self.socket_path)?;
        stream.write_all(request.as_bytes())?;
        stream.shutdown(std::net::Shutdown::Write)?;
        let mut response = String::new();
        stream.read_to_string(&mut response)?;
        decode_json_rpc_response(method, &response)
    }
}

impl jsonrpsee::core::client::ClientT for JsonRpcControlClient {
    fn notification<Params>(
        &self,
        method: &str,
        params: Params,
    ) -> impl std::future::Future<Output = Result<(), jsonrpsee::core::client::Error>> + Send
    where
        Params: jsonrpsee::core::traits::ToRpcParams + Send,
    {
        async move {
            let params = rpc_params_value(params)?;
            self.call_value(method, params)
                .await
                .map(|_| ())
                .map_err(rpc_transport_error)
        }
    }

    fn request<R, Params>(
        &self,
        method: &str,
        params: Params,
    ) -> impl std::future::Future<Output = Result<R, jsonrpsee::core::client::Error>> + Send
    where
        R: DeserializeOwned,
        Params: jsonrpsee::core::traits::ToRpcParams + Send,
    {
        async move {
            let params = rpc_params_value(params)?;
            self.call(method, params).await.map_err(rpc_transport_error)
        }
    }

    fn batch_request<'a, R>(
        &self,
        _batch: jsonrpsee::core::params::BatchRequestBuilder<'a>,
    ) -> impl std::future::Future<
        Output = Result<
            jsonrpsee::core::client::BatchResponse<'a, R>,
            jsonrpsee::core::client::Error,
        >,
    > + Send
    where
        R: DeserializeOwned + std::fmt::Debug + 'a,
    {
        async { Err(jsonrpsee::core::client::Error::HttpNotImplemented) }
    }
}

impl jsonrpsee::core::client::ClientT for &JsonRpcControlClient {
    fn notification<Params>(
        &self,
        method: &str,
        params: Params,
    ) -> impl std::future::Future<Output = Result<(), jsonrpsee::core::client::Error>> + Send
    where
        Params: jsonrpsee::core::traits::ToRpcParams + Send,
    {
        async move {
            let params = rpc_params_value(params)?;
            self.call_value(method, params)
                .await
                .map(|_| ())
                .map_err(rpc_transport_error)
        }
    }

    fn request<R, Params>(
        &self,
        method: &str,
        params: Params,
    ) -> impl std::future::Future<Output = Result<R, jsonrpsee::core::client::Error>> + Send
    where
        R: DeserializeOwned,
        Params: jsonrpsee::core::traits::ToRpcParams + Send,
    {
        async move {
            let params = rpc_params_value(params)?;
            self.call(method, params).await.map_err(rpc_transport_error)
        }
    }

    fn batch_request<'a, R>(
        &self,
        _batch: jsonrpsee::core::params::BatchRequestBuilder<'a>,
    ) -> impl std::future::Future<
        Output = Result<
            jsonrpsee::core::client::BatchResponse<'a, R>,
            jsonrpsee::core::client::Error,
        >,
    > + Send
    where
        R: DeserializeOwned + std::fmt::Debug + 'a,
    {
        async { Err(jsonrpsee::core::client::Error::HttpNotImplemented) }
    }
}

fn rpc_params_value(
    params: impl jsonrpsee::core::traits::ToRpcParams,
) -> Result<Value, jsonrpsee::core::client::Error> {
    match params.to_rpc_params()? {
        Some(raw) => serde_json::from_str(raw.get()).map_err(Into::into),
        None => Ok(serde_json::json!([])),
    }
}

fn rpc_transport_error(error: io::Error) -> jsonrpsee::core::client::Error {
    jsonrpsee::core::client::Error::Transport(Box::new(error))
}

fn decode_json_rpc_response(method: &str, response: &str) -> io::Result<Value> {
    let (_, body) = response
        .split_once("\r\n\r\n")
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing HTTP body"))?;
    let payload: Value = serde_json::from_str(body)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    if let Some(error) = payload.get("error") {
        return Err(io::Error::other(format!(
            "JSON-RPC {method} failed: {error}"
        )));
    }
    payload
        .get("result")
        .cloned()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing JSON-RPC result"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn system_stop_request_defaults_to_drain() {
        let value: SystemStopRequest = serde_json::from_value(serde_json::json!({})).unwrap();
        assert_eq!(value.reason, None);
        assert!(!value.immediate);
    }
}
