//! Low-frequency Reference query contract.
//!
//! The socket transport is intentionally kept here rather than in a consumer
//! service.  This is a compatibility/query plane; current-state bootstrap
//! still belongs to the mmap snapshot readers.

use serde::Deserialize;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};

use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct ReferenceMarket {
    pub market_id: String,
    pub instrument_id: String,
    pub venue_id: String,
    pub market_type: String,
    #[serde(default)]
    pub asset_type: Option<String>,
    pub source_symbol: String,
    #[serde(default)]
    pub underlying_instrument_id: Option<String>,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub price_tick: Option<String>,
    #[serde(default)]
    pub quantity_tick: Option<String>,
    #[serde(default)]
    pub minimum_quantity: Option<String>,
    #[serde(default)]
    pub minimum_notional: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct ReferenceInstrument {
    pub instrument_id: String,
    pub symbol: String,
    pub instrument_type: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct Health {
    pub status: String,
    pub generation: u64,
    pub event_sequence: u64,
}

#[derive(Debug, Deserialize)]
struct MarketResponse {
    markets: Vec<ReferenceMarket>,
}

#[derive(Debug, Deserialize)]
struct QueryRecord<T> {
    value: T,
}

#[derive(Clone, Debug)]
pub struct ReferenceQueryClient {
    socket_path: PathBuf,
}

impl ReferenceQueryClient {
    pub fn connect(path: impl AsRef<Path>) -> Self {
        Self {
            socket_path: path.as_ref().to_path_buf(),
        }
    }

    pub fn health(&self) -> ContractResult<Health> {
        self.get_json("/v1/health", &[] as &[(String, String)])
    }

    pub fn markets(
        &self,
        venue_id: &str,
        market_type: &str,
        asset_type: Option<&str>,
        source_symbol: &str,
    ) -> ContractResult<Vec<ReferenceMarket>> {
        let mut params = vec![
            ("venue_id", venue_id.to_owned()),
            ("market_type", market_type.to_owned()),
            ("symbol", source_symbol.to_owned()),
            ("active_only", "true".to_owned()),
        ];
        if let Some(asset_type) = asset_type {
            params.push(("asset_type", asset_type.to_owned()));
        }
        let response: MarketResponse = self.get_json("/v1/markets", &params)?;
        Ok(response.markets)
    }

    pub fn resolve_market(
        &self,
        market_id: Option<&str>,
        venue_id: Option<&str>,
        market_type: Option<&str>,
        asset_type: Option<&str>,
        source_symbol: Option<&str>,
    ) -> ContractResult<ReferenceMarket> {
        let mut params = vec![("active_only", "true".to_owned())];
        for (key, value) in [
            ("market_id", market_id),
            ("venue_id", venue_id),
            ("market_type", market_type),
            ("asset_type", asset_type),
            ("symbol", source_symbol),
        ] {
            if let Some(value) = value {
                params.push((key, value.to_owned()));
            }
        }
        self.get_json("/v1/markets/resolve", &params)
    }

    pub fn instruments(&self, text: &str) -> ContractResult<Vec<ReferenceInstrument>> {
        let records: Vec<QueryRecord<ReferenceInstrument>> = self.get_json(
            "/v1/query",
            &[
                ("kind", "instrument".to_owned()),
                ("text", text.to_owned()),
                ("active_only", "true".to_owned()),
            ],
        )?;
        Ok(records.into_iter().map(|record| record.value).collect())
    }

    pub fn option_markets(
        &self,
        venue_id: &str,
        asset_type: Option<&str>,
        underlying_instrument_id: &str,
    ) -> ContractResult<Vec<ReferenceMarket>> {
        let mut params = vec![
            ("kind", "market".to_owned()),
            ("venue_id", venue_id.to_owned()),
            ("market_type", "options".to_owned()),
            (
                "underlying_instrument_id",
                underlying_instrument_id.to_owned(),
            ),
            ("active_only", "true".to_owned()),
        ];
        if let Some(asset_type) = asset_type {
            params.push(("asset_type", asset_type.to_owned()));
        }
        let records: Vec<QueryRecord<ReferenceMarket>> = self.get_json("/v1/query", &params)?;
        Ok(records.into_iter().map(|record| record.value).collect())
    }

    pub fn active_markets(&self) -> ContractResult<Vec<ReferenceMarket>> {
        let records: Vec<QueryRecord<ReferenceMarket>> = self.get_json(
            "/v1/query",
            &[
                ("kind", "market".to_owned()),
                ("active_only", "true".to_owned()),
            ],
        )?;
        Ok(records.into_iter().map(|record| record.value).collect())
    }

    fn get_json<T: for<'de> serde::Deserialize<'de>>(
        &self,
        path: &str,
        params: &[(impl AsRef<str>, String)],
    ) -> ContractResult<T> {
        let query = params
            .iter()
            .map(|(key, value)| format!("{}={}", encode(key.as_ref()), encode(value)))
            .collect::<Vec<_>>()
            .join("&");
        let request =
            format!("GET {path}?{query} HTTP/1.1\r\nHost: reference\r\nConnection: close\r\n\r\n");
        let mut stream = UnixStream::connect(&self.socket_path).map_err(|error| {
            ContractError::Transport(format!(
                "connect Reference socket {}: {error}",
                self.socket_path.display()
            ))
        })?;
        stream.write_all(request.as_bytes()).map_err(|error| {
            ContractError::Transport(format!("request Reference query: {error}"))
        })?;
        let mut response = Vec::new();
        stream
            .read_to_end(&mut response)
            .map_err(|error| ContractError::Transport(format!("read Reference query: {error}")))?;
        let (headers, body) = response
            .split_once_bytes(b"\r\n\r\n")
            .ok_or_else(|| ContractError::Invalid("Reference response has no HTTP body".into()))?;
        let status = headers
            .split(|byte| *byte == b'\n')
            .next()
            .and_then(|line| line.split(|byte| *byte == b' ').nth(1))
            .and_then(|value| std::str::from_utf8(value).ok())
            .unwrap_or("500");
        if status != "200" {
            return Err(ContractError::Transport(format!(
                "Reference query failed with HTTP {status}"
            )));
        }
        serde_json::from_slice(body)
            .map_err(|error| ContractError::Invalid(format!("decode Reference query: {error}")))
    }
}

fn encode(value: &str) -> String {
    value.bytes().fold(String::new(), |mut result, byte| {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':') {
            result.push(byte as char);
        } else {
            result.push_str(&format!("%{byte:02X}"));
        }
        result
    })
}

trait SplitOnceBytes {
    fn split_once_bytes(&self, needle: &[u8]) -> Option<(&[u8], &[u8])>;
}

impl SplitOnceBytes for [u8] {
    fn split_once_bytes(&self, needle: &[u8]) -> Option<(&[u8], &[u8])> {
        self.windows(needle.len())
            .position(|window| window == needle)
            .map(|index| (&self[..index], &self[index + needle.len()..]))
    }
}
