use std::path::PathBuf;

use kairos_workspace::RestControlClient;

use super::types::{ExecutionControlResponse, ExecutionRoutesResponse};
use crate::{ContractError, ContractResult};
pub struct ExecutionControlClient {
    client: RestControlClient,
}
impl ExecutionControlClient {
    pub fn connect(socket: impl Into<PathBuf>) -> Self {
        Self {
            client: RestControlClient::new(socket),
        }
    }
    pub async fn health(&self) -> ContractResult<ExecutionControlResponse> {
        self.request("GET", "/v1/health", None).await
    }
    pub async fn routes(&self, query: &str) -> ContractResult<ExecutionRoutesResponse> {
        let path = if query.is_empty() {
            "/v1/routes".to_owned()
        } else {
            format!("/v1/routes?{query}")
        };
        let value = self
            .client
            .request_json("GET", &path, None)
            .await
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        serde_json::from_value(value).map_err(|error| ContractError::Invalid(error.to_string()))
    }
    pub async fn submit_intent(&self, body: &[u8]) -> ContractResult<ExecutionControlResponse> {
        self.request("POST", "/v1/intents", Some(body)).await
    }
    pub async fn cancel_order(
        &self,
        order_id: &str,
        body: Option<&[u8]>,
    ) -> ContractResult<ExecutionControlResponse> {
        self.request("DELETE", &format!("/v1/orders/{order_id}"), body)
            .await
    }
    pub async fn replace_order(
        &self,
        order_id: &str,
        body: &[u8],
    ) -> ContractResult<ExecutionControlResponse> {
        self.request("PATCH", &format!("/v1/orders/{order_id}"), Some(body))
            .await
    }
    pub async fn reconcile(&self, body: &[u8]) -> ContractResult<ExecutionControlResponse> {
        self.request("POST", "/v1/reconciliation", Some(body)).await
    }
    pub async fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&[u8]>,
    ) -> ContractResult<ExecutionControlResponse> {
        let value = self
            .client
            .request_json(method, path, body)
            .await
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        serde_json::from_value(value).map_err(|error| ContractError::Invalid(error.to_string()))
    }
}
