//! Local adapters for the Reference snapshot projection.

use crate::domain::market::MarketDescriptor;

pub fn resolve_market(
    markets: &[MarketDescriptor],
    exchange_id: &str,
    market_type: &str,
    asset_type: Option<&str>,
    source_symbol: &str,
) -> Result<MarketDescriptor, String> {
    let matches: Vec<_> = markets
        .iter()
        .filter(|market| {
            exchange_matches(market.exchange_id.as_str(), exchange_id)
                && market.market_type == market_type
                && asset_type.is_none_or(|value| {
                    market.asset_type.map(|class| class.as_str()) == Some(value)
                })
                && market.source_symbol.eq_ignore_ascii_case(source_symbol)
                && market.is_active()
        })
        .collect();
    let [market] = matches.as_slice() else {
        return Err(if matches.is_empty() {
            format!("Reference has no market for {exchange_id}/{market_type}/{source_symbol}")
        } else {
            format!("Reference market is ambiguous for {exchange_id}/{market_type}/{source_symbol}")
        });
    };
    Ok((*market).clone())
}

pub fn resolve_option_markets(
    markets: &[MarketDescriptor],
    exchange_id: &str,
    asset_type: Option<&str>,
    underlying: &str,
) -> Result<Vec<MarketDescriptor>, String> {
    let normalized_underlying = underlying.trim().to_ascii_uppercase();
    let underlying_id = markets
        .iter()
        .filter(|value| matches!(value.market_type.as_str(), "spot" | "index"))
        .find(|value| {
            value
                .source_symbol
                .eq_ignore_ascii_case(&normalized_underlying)
                || (exchange_id.eq_ignore_ascii_case("binance")
                    && (value
                        .source_symbol
                        .eq_ignore_ascii_case(&format!("{normalized_underlying}USDT"))
                        || value
                            .source_symbol
                            .eq_ignore_ascii_case(&format!("{normalized_underlying}/USDT"))))
        })
        .map(|value| value.instrument_id.as_str())
        .ok_or_else(|| format!("Reference has no underlying instrument for {underlying}"))?;

    Ok(markets
        .iter()
        .filter(|market| {
            market.market_type == "options"
                && exchange_matches(market.exchange_id.as_str(), exchange_id)
                && asset_type.is_none_or(|value| {
                    market.asset_type.map(|class| class.as_str()) == Some(value)
                })
                && market.underlying_instrument_id.as_deref() == Some(underlying_id)
                && market.is_active()
        })
        .cloned()
        .collect::<Vec<_>>())
}

fn exchange_matches(left: &str, right: &str) -> bool {
    left.strip_prefix("exchange:")
        .unwrap_or(left)
        .eq_ignore_ascii_case(right.strip_prefix("exchange:").unwrap_or(right))
}

#[cfg(test)]
mod tests {
    use super::resolve_market;
    use crate::domain::market::MarketDescriptor;

    #[test]
    fn resolves_canonical_reference_exchange_from_business_exchange_selector() {
        let market = MarketDescriptor::new(
            "market:binance:spot:BTCUSDT",
            "instrument:binance:spot:BTCUSDT",
            "exchange:binance",
            "spot",
            "BTCUSDT",
        )
        .unwrap();

        let resolved = resolve_market(&[market], "binance", "spot", None, "btcusdt")
            .expect("namespaced Reference exchange must match the business selector");

        assert_eq!(resolved.exchange_id.as_str(), "exchange:binance");
    }
}
