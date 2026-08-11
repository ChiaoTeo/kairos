//! Typed Risk command client.  JSON is kept as an administrative transport
//! adapter; the Risk state owner receives already-decoded business commands.

use reqwest::blocking::Client;
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use std::path::Path;
use std::time::Duration;

use crate::model::{
    AuthorizeRequest, CircuitState, CloseCircuitRequest, OpenCircuitRequest, Reservation,
    RiskDecision,
};
use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct Health {
    pub status: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub policy_version: u64,
    pub reservation_count: usize,
    #[serde(default)]
    pub open_circuit_count: usize,
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

    pub fn authorize_and_reserve(
        &self,
        request: &AuthorizeRequest,
    ) -> ContractResult<RiskDecision> {
        self.post("/v1/authorize_and_reserve", request)
    }

    pub fn pre_trade_check(&self, request: &AuthorizeRequest) -> ContractResult<RiskDecision> {
        self.post("/v1/pre_trade_check", request)
    }

    pub fn post_trade_check(&self, request: &AuthorizeRequest) -> ContractResult<RiskDecision> {
        self.post("/v1/post_trade_check", request)
    }

    pub fn open_circuit(&self, request: &OpenCircuitRequest) -> ContractResult<CircuitState> {
        self.post("/v1/open_circuit", request)
    }

    pub fn close_circuit(&self, request: &CloseCircuitRequest) -> ContractResult<CircuitState> {
        self.post("/v1/close_circuit", request)
    }

    pub fn release(&self, reservation_id: &str, at_unix_nanos: u64) -> ContractResult<Reservation> {
        self.post(
            "/v1/release",
            &serde_json::json!({
                "reservation_id": reservation_id,
                "at_unix_nanos": at_unix_nanos,
            }),
        )
    }

    pub fn resize(
        &self,
        reservation_id: &str,
        amount: &crate::model::Amount,
        at_unix_nanos: u64,
    ) -> ContractResult<Reservation> {
        self.post(
            "/v1/resize",
            &serde_json::json!({
                "reservation_id": reservation_id,
                "amount": amount,
                "at_unix_nanos": at_unix_nanos,
            }),
        )
    }

    pub fn consume(&self, reservation_id: &str, at_unix_nanos: u64) -> ContractResult<Reservation> {
        self.post(
            "/v1/consume",
            &serde_json::json!({
                "reservation_id": reservation_id,
                "at_unix_nanos": at_unix_nanos,
            }),
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

    fn post<T: Serialize, R: DeserializeOwned>(&self, path: &str, body: &T) -> ContractResult<R> {
        let response = self
            .client
            .post(format!("http://localhost{path}"))
            .json(body)
            .send()
            .map_err(|error| ContractError::Transport(format!("POST {path}: {error}")))?;
        decode_response(path, response)
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
        return Err(ContractError::Rejected(format!(
            "{path} failed with HTTP {status}: {value}"
        )));
    }
    serde_json::from_value(value)
        .map_err(|error| ContractError::Invalid(format!("decode {path} response: {error}")))
}
