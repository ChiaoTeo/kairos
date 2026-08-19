use std::path::PathBuf;

use kairos_workspace::RestControlClient;

use super::types::ReferenceHealthResponse;
use crate::{ContractError, ContractResult};

pub struct ReferenceControlClient {
    client: RestControlClient,
}

impl ReferenceControlClient {
    pub fn connect(socket: impl Into<PathBuf>) -> Self {
        Self {
            client: RestControlClient::new(socket),
        }
    }
    pub async fn health(&self) -> ContractResult<ReferenceHealthResponse> {
        let value = self
            .client
            .request_json("GET", "/v1/health", None)
            .await
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        serde_json::from_value(value).map_err(|error| ContractError::Invalid(error.to_string()))
    }
}
