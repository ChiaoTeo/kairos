use std::path::Path;
use std::time::Duration;

use kairos_primitives::{Generation, Sequence};
use reqwest::blocking::{Client, Response};
use serde::Serialize;
use serde::de::DeserializeOwned;

use crate::control::{
    AdvanceRiskTimeRequest, AdvanceRiskTimeResponse, Amount, AuthorizeRequest, CloseCircuitRequest,
    ConsumeReservationRequest, OpenCircuitRequest, PublishPolicyRequest, ReleaseReservationRequest,
    Reservation, ResizeReservationRequest, RiskCommandStatus, RiskDecision, RiskPolicy,
};
use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, serde::Deserialize, serde::Serialize, Eq, PartialEq)]
pub struct Health {
    pub status: String,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub policy_version: Generation,
    pub reservation_count: u64,
    #[serde(default)]
    pub open_circuit_count: u64,
}

pub struct RiskControlClient {
    client: Client,
}
impl RiskControlClient {
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
        self.post("/v1/authorizations", request)
    }

    pub fn publish_policy(&self, policy: &RiskPolicy) -> ContractResult<RiskCommandStatus> {
        self.post(
            "/v1/publish_policy",
            &PublishPolicyRequest {
                policy: policy.clone(),
            },
        )
    }

    pub fn advance_time(
        &self,
        event_time_unix_nanos: kairos_primitives::UnixNanos,
    ) -> ContractResult<AdvanceRiskTimeResponse> {
        self.post(
            "/v1/time/advance",
            &AdvanceRiskTimeRequest {
                event_time_unix_nanos,
            },
        )
    }

    pub fn open_circuit(
        &self,
        request: &OpenCircuitRequest,
    ) -> ContractResult<crate::control::CircuitState> {
        self.post("/v1/open_circuit", request)
    }

    pub fn close_circuit(
        &self,
        request: &CloseCircuitRequest,
    ) -> ContractResult<crate::control::CircuitState> {
        self.post("/v1/close_circuit", request)
    }

    pub fn resize(
        &self,
        reservation_id: &kairos_primitives::ReservationId,
        amount: &Amount,
        at_unix_nanos: kairos_primitives::UnixNanos,
    ) -> ContractResult<Reservation> {
        self.post(
            "/v1/resize",
            &ResizeReservationRequest {
                reservation_id: reservation_id.clone(),
                amount: *amount,
                at_unix_nanos,
            },
        )
    }

    pub fn release(
        &self,
        reservation_id: &kairos_primitives::ReservationId,
        at_unix_nanos: kairos_primitives::UnixNanos,
    ) -> ContractResult<Reservation> {
        self.post(
            "/v1/release",
            &ReleaseReservationRequest {
                reservation_id: reservation_id.clone(),
                at_unix_nanos,
            },
        )
    }

    pub fn consume(
        &self,
        reservation_id: &kairos_primitives::ReservationId,
        at_unix_nanos: kairos_primitives::UnixNanos,
    ) -> ContractResult<Reservation> {
        self.post(
            "/v1/consume",
            &ConsumeReservationRequest {
                reservation_id: reservation_id.clone(),
                at_unix_nanos,
            },
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
            .map_err(|error| {
                if error.is_connect() || error.is_builder() {
                    ContractError::NotSent(format!("POST {path}: {error}"))
                } else {
                    ContractError::Indeterminate(format!("POST {path}: {error}"))
                }
            })?;
        decode_command_response(path, response)
    }
}

fn decode_command_response<T: DeserializeOwned>(
    path: &str,
    response: Response,
) -> ContractResult<T> {
    let status = response.status();
    let value: serde_json::Value = response.json().map_err(|error| {
        ContractError::Indeterminate(format!("decode {path} command response: {error}"))
    })?;
    if !status.is_success() {
        return Err(ContractError::Rejected(format!(
            "{path} failed with HTTP {status}: {value}"
        )));
    }
    serde_json::from_value(value).map_err(|error| {
        ContractError::Indeterminate(format!("decode {path} command result: {error}"))
    })
}

fn decode_response<T: DeserializeOwned>(path: &str, response: Response) -> ContractResult<T> {
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
