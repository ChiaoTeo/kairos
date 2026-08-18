//! Synchronous Account control/query client.
//!
//! This is the JSON control plane only. Account views and events use the v2
//! FlatBuffers data plane exposed by [`crate::view`] and [`crate::event`].

use reqwest::blocking::Client;
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use std::path::Path;
use std::time::Duration;

use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Health {
    pub status: String,
    #[serde(default)]
    pub lease_valid: Option<bool>,
    pub generation: u64,
    pub event_sequence: u64,
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SimulatedSettlement {
    pub fill_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub order_id: Option<String>,
    pub segment_key: String,
    pub instrument_id: String,
    pub quantity: DecimalValue,
    pub price: DecimalValue,
    pub side: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub settlement_asset: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub settlement_delta: Option<DecimalValue>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fee_asset: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fee_amount: Option<DecimalValue>,
    pub occurred_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarkToMarketRequest {
    pub segment_key: String,
    pub instrument_id: String,
    pub quote_asset: String,
    pub mark_price: DecimalValue,
    pub observed_at_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AdvanceAccountTimeRequest {
    pub event_time_unix_nanos: u64,
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

    pub fn apply_simulated_settlement(
        &self,
        settlement: &SimulatedSettlement,
    ) -> ContractResult<()> {
        self.post("/v1/simulation/settlements", settlement)
    }

    pub fn mark_to_market(&self, request: &MarkToMarketRequest) -> ContractResult<()> {
        self.post("/v1/mark-to-market", request)
    }

    pub fn advance_time(&self, event_time_unix_nanos: u64) -> ContractResult<()> {
        self.post(
            "/v1/time/advance",
            &AdvanceAccountTimeRequest {
                event_time_unix_nanos,
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

#[cfg(test)]
mod tests {
    use super::{AdvanceAccountTimeRequest, DecimalValue, MarkToMarketRequest};

    #[test]
    fn simulation_controls_have_typed_contract_shapes() {
        let mark = MarkToMarketRequest {
            segment_key: "spot".into(),
            instrument_id: "instrument:btc".into(),
            quote_asset: "USDT".into(),
            mark_price: DecimalValue {
                mantissa: 6_400_025,
                scale: 2,
            },
            observed_at_unix_nanos: 10,
        };
        assert_eq!(
            serde_json::to_value(mark).unwrap(),
            serde_json::json!({
                "segment_key": "spot",
                "instrument_id": "instrument:btc",
                "quote_asset": "USDT",
                "mark_price": "64000.25",
                "observed_at_unix_nanos": 10
            })
        );
        assert_eq!(
            serde_json::to_value(AdvanceAccountTimeRequest {
                event_time_unix_nanos: 11
            })
            .unwrap(),
            serde_json::json!({"event_time_unix_nanos": 11})
        );
    }
}
