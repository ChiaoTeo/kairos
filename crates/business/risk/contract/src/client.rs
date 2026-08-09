//! Typed low-frequency Risk command client.

use reqwest::blocking::Client;
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use std::path::Path;
use std::time::Duration;

use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct Health {
    pub status: String,
    pub generation: u64,
    pub event_sequence: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct Amount {
    pub mantissa: i64,
    pub scale: u8,
}

#[derive(Clone, Debug, Serialize)]
pub struct Usage {
    pub metric: String,
    pub amount: Amount,
    pub budgets: Vec<BudgetRef>,
}

#[derive(Clone, Debug, Serialize)]
pub struct BudgetRef {
    pub scope: String,
    pub subject: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct Assessment {
    pub request_id: String,
    pub usages: Vec<Usage>,
    pub at_unix_nanos: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct ReserveRequest {
    pub reservation_id: String,
    pub assessment: Assessment,
}

pub struct RiskContractClient {
    client: Client,
}

impl RiskContractClient {
    pub fn connect(socket: impl AsRef<Path>) -> ContractResult<Self> {
        let client = Client::builder()
            .unix_socket(socket.as_ref().to_path_buf())
            .timeout(Duration::from_secs(3))
            .build()
            .map_err(|error| ContractError::Transport(format!("build Risk client: {error}")))?;
        Ok(Self { client })
    }

    pub fn health(&self) -> ContractResult<Health> {
        self.get("/v1/health")
    }

    pub fn reserve(&self, request: &ReserveRequest) -> ContractResult<()> {
        self.post("/v1/reserve", request)
    }

    pub fn release(&self, reservation_id: &str) -> ContractResult<()> {
        self.post(
            "/v1/release",
            &serde_json::json!({"reservation_id": reservation_id}),
        )
    }

    pub fn consume(&self, reservation_id: &str) -> ContractResult<()> {
        self.post(
            "/v1/consume",
            &serde_json::json!({"reservation_id": reservation_id}),
        )
    }

    fn get<T: DeserializeOwned>(&self, path: &str) -> ContractResult<T> {
        let response = self
            .client
            .get(format!("http://localhost{path}"))
            .send()
            .map_err(|error| ContractError::Transport(format!("GET {path}: {error}")))?;
        decode_response(path, response)
    }

    fn post<T: Serialize>(&self, path: &str, body: &T) -> ContractResult<()> {
        let response = self
            .client
            .post(format!("http://localhost{path}"))
            .json(body)
            .send()
            .map_err(|error| ContractError::Transport(format!("POST {path}: {error}")))?;
        decode_response::<serde_json::Value>(path, response).map(|_| ())
    }
}

fn decode_response<T: DeserializeOwned>(
    path: &str,
    response: reqwest::blocking::Response,
) -> ContractResult<T> {
    let status = response.status();
    let value: serde_json::Value = response
        .json()
        .map_err(|error| ContractError::Transport(format!("decode {path}: {error}")))?;
    if !status.is_success() {
        return Err(ContractError::Transport(format!(
            "{path} failed with HTTP {status}: {value}"
        )));
    }
    serde_json::from_value(value)
        .map_err(|error| ContractError::Invalid(format!("decode {path} response: {error}")))
}
