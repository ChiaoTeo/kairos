use std::path::PathBuf;

use kairos_workspace::RestControlClient;

use crate::{ContractError, ContractResult};

use super::types::AccountControlResponse;

pub struct AccountControlClient {
    client: RestControlClient,
}

impl AccountControlClient {
    pub fn connect(socket: impl Into<PathBuf>) -> Self {
        Self {
            client: RestControlClient::new(socket),
        }
    }

    pub async fn health(&self) -> ContractResult<AccountControlResponse> {
        self.request("GET", "/v1/health", None).await
    }

    pub async fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&[u8]>,
    ) -> ContractResult<AccountControlResponse> {
        let value = self
            .client
            .request_json(method, path, body)
            .await
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        serde_json::from_value(value).map_err(|error| ContractError::Invalid(error.to_string()))
    }
}
