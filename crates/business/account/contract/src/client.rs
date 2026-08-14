//! Synchronous Account control/query client.
//!
//! This is the JSON control plane only. Account views and events use the v2
//! FlatBuffers data plane exposed by [`crate::view`] and [`crate::event`].

use reqwest::blocking::Client;
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use std::path::Path;
use std::time::Duration;

use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct Health {
    pub status: String,
    #[serde(default)]
    pub lease_valid: Option<bool>,
    pub generation: u64,
    pub event_sequence: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct Capability {
    pub account_id: String,
    pub can_trade: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DecimalValue {
    pub mantissa: i64,
    pub scale: u8,
}

impl Serialize for DecimalValue {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        serializer.serialize_str(&format_decimal(self.mantissa, self.scale))
    }
}

impl<'de> Deserialize<'de> for DecimalValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        parse_decimal(&value)
            .map(|(mantissa, scale)| Self { mantissa, scale })
            .map_err(serde::de::Error::custom)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct Balance {
    pub asset_code: String,
    pub total: DecimalValue,
    pub available: Option<DecimalValue>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct Position {
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub quantity: DecimalValue,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct BalanceGroup(pub String, pub String, pub Vec<Balance>);

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct PositionGroup(pub String, pub String, pub Vec<Position>);

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct BalancesResponse {
    pub accounts: Vec<BalanceGroup>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct PositionsResponse {
    pub accounts: Vec<PositionGroup>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct OrderEvent {
    pub order_id: String,
    pub status: String,
    pub remote_order_id: Option<String>,
    pub filled_quantity: DecimalValue,
    pub occurred_at_unix_nanos: u64,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct Fill {
    pub fill_id: String,
    pub order_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub quantity: DecimalValue,
    pub price: DecimalValue,
    pub side: String,
    pub occurred_at_unix_nanos: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fee_asset: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fee_amount: Option<DecimalValue>,
}

#[derive(Clone, Debug, Serialize)]
pub struct SimulatedFill {
    pub fill_id: String,
    pub order_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub quantity: DecimalValue,
    pub price: DecimalValue,
    pub side: String,
    pub settlement_asset: String,
    pub settlement_delta: DecimalValue,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fee_asset: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fee_amount: Option<DecimalValue>,
    pub occurred_at_unix_nanos: u64,
}

pub struct AccountContractClient {
    client: Client,
}

impl AccountContractClient {
    pub fn connect(socket: impl AsRef<Path>) -> ContractResult<Self> {
        let client = Client::builder()
            .unix_socket(socket.as_ref().to_path_buf())
            .timeout(Duration::from_secs(3))
            .build()
            .map_err(|error| ContractError::Transport(format!("build Account client: {error}")))?;
        Ok(Self { client })
    }

    pub fn health(&self) -> ContractResult<Health> {
        self.get("/v1/health")
    }

    pub fn capabilities(&self) -> ContractResult<Vec<Capability>> {
        #[derive(Deserialize)]
        struct Response {
            capabilities: Vec<Capability>,
        }
        Ok(self.get::<Response>("/v1/capabilities")?.capabilities)
    }

    pub fn balances(&self, symbol: Option<&str>) -> ContractResult<BalancesResponse> {
        let path = symbol
            .map(|value| format!("/v1/balances?symbol={value}"))
            .unwrap_or_else(|| "/v1/balances".into());
        self.get(&path)
    }

    pub fn positions(&self, symbol: Option<&str>) -> ContractResult<PositionsResponse> {
        let path = symbol
            .map(|value| format!("/v1/positions?symbol={value}"))
            .unwrap_or_else(|| "/v1/positions".into());
        self.get(&path)
    }

    pub fn publish_order_event(&self, event: &OrderEvent) -> ContractResult<()> {
        self.post("/v1/order-event", event)
    }
    pub fn publish_fill(&self, fill: &Fill) -> ContractResult<()> {
        self.post("/v1/fill", fill)
    }
    pub fn publish_simulated_fill(&self, fill: &SimulatedFill) -> ContractResult<()> {
        self.post("/v1/simulated-fill", fill)
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

fn format_decimal(mantissa: i64, scale: u8) -> String {
    let sign = if mantissa < 0 { "-" } else { "" };
    let digits = mantissa.unsigned_abs().to_string();
    if scale == 0 {
        return format!("{sign}{digits}");
    }
    let width = usize::from(scale) + 1;
    let padded = format!("{digits:0>width$}");
    format!(
        "{sign}{}.{}",
        &padded[..padded.len() - usize::from(scale)],
        &padded[padded.len() - usize::from(scale)..]
    )
}

fn parse_decimal(value: &str) -> Result<(i64, u8), String> {
    let value = value.trim();
    if value.is_empty() {
        return Err("decimal is empty".into());
    }
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches(['-', '+']);
    let mut parts = unsigned.split('.');
    let whole = parts.next().unwrap_or_default();
    let fraction = parts.next().unwrap_or_default();
    if parts.next().is_some()
        || whole.is_empty() && fraction.is_empty()
        || !whole.chars().all(|c| c.is_ascii_digit())
        || !fraction.chars().all(|c| c.is_ascii_digit())
        || fraction.len() > usize::from(u8::MAX)
    {
        return Err(format!("invalid decimal: {value}"));
    }
    let scale = u8::try_from(fraction.len()).map_err(|_| "decimal scale is too large")?;
    let digits = format!("{whole}{fraction}");
    let mut mantissa = digits
        .parse::<i64>()
        .map_err(|_| format!("decimal is out of range: {value}"))?;
    if negative {
        mantissa = mantissa
            .checked_neg()
            .ok_or_else(|| format!("decimal is out of range: {value}"))?;
    }
    Ok((mantissa, scale))
}
