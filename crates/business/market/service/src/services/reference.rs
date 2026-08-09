//! Domain adapter for the public Reference query contract.

use kairos_reference_contract::query::ReferenceQueryClient;
use std::path::Path;

use crate::domain::market::MarketDescriptor;

pub fn resolve_market(
    socket_path: &Path,
    venue_id: &str,
    market_type: &str,
    asset_type: Option<&str>,
    source_symbol: &str,
) -> Result<MarketDescriptor, String> {
    let client = ReferenceQueryClient::connect(socket_path);
    let markets = client
        .markets(venue_id, market_type, asset_type, source_symbol)
        .map_err(|error| error.to_string())?;
    let [market] = markets.as_slice() else {
        return Err(if markets.is_empty() {
            format!("Reference has no market for {venue_id}/{market_type}/{source_symbol}")
        } else {
            format!("Reference market is ambiguous for {venue_id}/{market_type}/{source_symbol}")
        });
    };
    descriptor(market)
}

pub fn resolve_option_markets(
    socket_path: &Path,
    venue_id: &str,
    asset_type: Option<&str>,
    underlying: &str,
) -> Result<Vec<MarketDescriptor>, String> {
    let client = ReferenceQueryClient::connect(socket_path);
    let instruments = client
        .instruments(underlying)
        .map_err(|error| error.to_string())?;
    let normalized_underlying = underlying.trim().to_ascii_uppercase();
    let underlying_id = instruments
        .iter()
        .filter(|value| matches!(value.instrument_type.as_str(), "equity" | "spot" | "index"))
        .find(|value| {
            value.symbol.eq_ignore_ascii_case(&normalized_underlying)
                || (venue_id.eq_ignore_ascii_case("binance")
                    && (value
                        .symbol
                        .eq_ignore_ascii_case(&format!("{normalized_underlying}USDT"))
                        || value
                            .symbol
                            .eq_ignore_ascii_case(&format!("{normalized_underlying}/USDT"))))
        })
        .map(|value| value.instrument_id.as_str())
        .ok_or_else(|| format!("Reference has no underlying instrument for {underlying}"))?;

    let markets = client
        .option_markets(venue_id, asset_type, underlying_id)
        .map_err(|error| error.to_string())?;
    markets.iter().map(descriptor).collect()
}

pub fn resolve_active_markets(socket_path: &Path) -> Result<Vec<MarketDescriptor>, String> {
    let client = ReferenceQueryClient::connect(socket_path);
    client
        .active_markets()
        .map_err(|error| error.to_string())?
        .iter()
        .map(descriptor)
        .collect()
}

fn descriptor(
    market: &kairos_reference_contract::query::ReferenceMarket,
) -> Result<MarketDescriptor, String> {
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
