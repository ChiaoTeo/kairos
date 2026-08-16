use std::path::PathBuf;

use kairos_workspace::RestControlClient;

use crate::{ContractError, ContractResult};

use super::types::MarketControlResponse;

pub struct MarketControlClient {
    client: RestControlClient,
}

impl MarketControlClient {
    pub fn connect(socket: impl Into<PathBuf>) -> Self {
        Self {
            client: RestControlClient::new(socket),
        }
    }

    pub async fn health(&self) -> ContractResult<MarketControlResponse> {
        self.request("GET", "/v1/health", None).await
    }

    pub async fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&[u8]>,
    ) -> ContractResult<MarketControlResponse> {
        let value = self
            .client
            .request_json(method, path, body)
            .await
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        serde_json::from_value(value).map_err(|error| ContractError::Invalid(error.to_string()))
    }
}
