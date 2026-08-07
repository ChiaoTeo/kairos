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
    Ok(descriptor)
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
