//! Read-only client for the Workspace-global Reference catalog.

use serde::Deserialize;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;

use crate::domain::market::MarketDescriptor;

#[derive(Debug, Deserialize)]
struct MarketRecord {
    market_id: String,
    instrument_id: String,
    venue_id: String,
    market_type: String,
    #[serde(default)]
    asset_type: Option<String>,
    source_symbol: String,
    #[serde(default)]
    underlying_instrument_id: Option<String>,
}

#[derive(Debug, Deserialize)]
struct MarketResponse {
    markets: Vec<MarketRecord>,
}

pub fn resolve_market(
    socket_path: &Path,
    venue_id: &str,
    market_type: &str,
    asset_type: Option<&str>,
    source_symbol: &str,
) -> Result<MarketDescriptor, String> {
    let mut stream = UnixStream::connect(socket_path).map_err(|error| {
        format!(
            "connect Reference socket {}: {error}",
            socket_path.display()
        )
    })?;
    let mut query = format!(
        "venue_id={}&market_type={}&symbol={}&active_only=true",
        encode(venue_id),
        encode(market_type),
        encode(source_symbol)
    );
    if let Some(asset_type) = asset_type {
        query.push_str("&asset_type=");
        query.push_str(&encode(asset_type));
    }
    let request =
        format!("GET /v1/markets?{query} HTTP/1.1\r\nHost: reference\r\nConnection: close\r\n\r\n");
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("request Reference market: {error}"))?;
    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|error| format!("read Reference market response: {error}"))?;
    let (_, body) = response
        .split_once_bytes(b"\r\n\r\n")
        .ok_or("Reference market response has no HTTP body")?;
    let status = response
        .split(|byte| *byte == b'\n')
        .next()
        .and_then(|line| line.split(|byte| *byte == b' ').nth(1))
        .and_then(|value| std::str::from_utf8(value).ok())
        .unwrap_or("500");
    if status != "200" {
        return Err(format!("Reference market query failed with HTTP {status}"));
    }
    let result: MarketResponse = serde_json::from_slice(body)
        .map_err(|error| format!("decode Reference market response: {error}"))?;
    let [market] = result.markets.as_slice() else {
        return Err(if result.markets.is_empty() {
            format!("Reference has no market for {venue_id}/{market_type}/{source_symbol}")
        } else {
            format!("Reference market is ambiguous for {venue_id}/{market_type}/{source_symbol}")
        });
    };
    let mut descriptor = MarketDescriptor::new(
        market.market_id.clone(),
        market.instrument_id.clone(),
        market.venue_id.clone(),
        market.market_type.clone(),
        market.source_symbol.clone(),
    )?;
    descriptor.asset_type = market.asset_type.clone();
    descriptor.underlying_instrument_id = market.underlying_instrument_id.clone();
    Ok(descriptor)
}

pub fn resolve_option_markets(
    socket_path: &Path,
    venue_id: &str,
    asset_type: Option<&str>,
    underlying: &str,
) -> Result<Vec<MarketDescriptor>, String> {
    let instruments = reference_query(
        socket_path,
        &[
            ("kind", "instrument"),
            ("text", underlying),
            ("active_only", "true"),
        ],
    )?;
    let normalized_underlying = underlying.trim().to_ascii_uppercase();
    let underlying_id = instruments
        .iter()
        .filter_map(|value| value.get("value"))
        .filter(|value| {
            value
                .get("instrument_type")
                .and_then(serde_json::Value::as_str)
                .is_some_and(|kind| matches!(kind, "equity" | "spot" | "index"))
        })
        // Binance option contracts identify BTC as BTCUSDT in the spot
        // catalog. Accept the user-facing base asset while retaining an
        // exact-symbol match when one exists.
        .find(|value| {
            value
                .get("symbol")
                .and_then(serde_json::Value::as_str)
                .is_some_and(|symbol| {
                    symbol.eq_ignore_ascii_case(&normalized_underlying)
                        || (venue_id.eq_ignore_ascii_case("binance")
                            && symbol.eq_ignore_ascii_case(&format!("{normalized_underlying}USDT")))
                })
        })
        .and_then(|value| value.get("instrument_id"))
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| format!("Reference has no underlying instrument for {underlying}"))?;
    let mut params = vec![
        ("kind", "market".to_string()),
        ("venue_id", venue_id.to_string()),
        ("market_type", "options".to_string()),
        ("underlying_instrument_id", underlying_id.to_string()),
        ("active_only", "true".to_string()),
    ];
    if let Some(asset_type) = asset_type {
        params.push(("asset_type", asset_type.to_string()));
    }
    let records = reference_query_owned(socket_path, &params)?;
    records
        .into_iter()
        .filter_map(|value| value.get("value").cloned())
        .map(|value| {
            serde_json::from_value(value).map_err(|error| format!("decode option market: {error}"))
        })
        .map(|value: Result<MarketRecord, _>| {
            value.and_then(|market| {
                let mut descriptor = MarketDescriptor::new(
                    market.market_id,
                    market.instrument_id,
                    market.venue_id,
                    market.market_type,
                    market.source_symbol,
                )?;
                descriptor.asset_type = market.asset_type;
                descriptor.underlying_instrument_id = market.underlying_instrument_id;
                Ok(descriptor)
            })
        })
        .collect()
}

fn reference_query(
    socket_path: &Path,
    params: &[(&str, &str)],
) -> Result<Vec<serde_json::Value>, String> {
    reference_query_owned(
        socket_path,
        &params
            .iter()
            .map(|(key, value)| (*key, (*value).to_string()))
            .collect::<Vec<_>>(),
    )
}

fn reference_query_owned(
    socket_path: &Path,
    params: &[(impl AsRef<str>, String)],
) -> Result<Vec<serde_json::Value>, String> {
    let query = params
        .iter()
        .map(|(key, value)| format!("{}={}", encode(key.as_ref()), encode(value)))
        .collect::<Vec<_>>()
        .join("&");
    let mut stream = UnixStream::connect(socket_path).map_err(|error| {
        format!(
            "connect Reference socket {}: {error}",
            socket_path.display()
        )
    })?;
    let request =
        format!("GET /v1/query?{query} HTTP/1.1\r\nHost: reference\r\nConnection: close\r\n\r\n");
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("request Reference query: {error}"))?;
    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|error| format!("read Reference query: {error}"))?;
    let (_, body) = response
        .split_once_bytes(b"\r\n\r\n")
        .ok_or("Reference response has no HTTP body")?;
    serde_json::from_slice(body).map_err(|error| format!("decode Reference query: {error}"))
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
