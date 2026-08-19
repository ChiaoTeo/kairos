use std::path::PathBuf;

use kairos_workspace::RestControlClient;

use super::types::MarketHealthResponse;
use crate::{ContractError, ContractResult};

pub struct MarketControlClient {
    client: RestControlClient,
}

impl MarketControlClient {
    pub fn connect(socket: impl Into<PathBuf>) -> Self {
        Self {
            client: RestControlClient::new(socket),
        }
    }

    pub async fn health(&self) -> ContractResult<MarketHealthResponse> {
        let value = self
            .client
            .request_json("GET", "/v1/health", None)
            .await
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        serde_json::from_value(value).map_err(|error| ContractError::Invalid(error.to_string()))
    }
}
