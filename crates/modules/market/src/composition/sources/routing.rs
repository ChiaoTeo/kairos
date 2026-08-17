//! Market-owned route matching for concrete source bindings.

use super::super::config::{self, BinanceDerivativeProduct, MarketSourceBinding};

pub(super) fn binding_matches_market(
    binding: &MarketSourceBinding,
    market: &crate::ResolvedMarket,
) -> bool {
    let provider = market.route.provider_id.as_str();
    let provider_product = market.route.provider_product.as_str();
    let (expected_provider, expected_product) = match binding {
        MarketSourceBinding::BinanceSpot { .. } => ("binance", "spot"),
        MarketSourceBinding::BinanceEquity { .. } => ("binance", "equity"),
        MarketSourceBinding::BinanceDerivatives { product, .. } => (
            "binance",
            match product {
                BinanceDerivativeProduct::UsdMFutures => "usd-m-futures",
                BinanceDerivativeProduct::CoinMFutures => "coin-m-futures",
                BinanceDerivativeProduct::Options => "options",
            },
        ),
        MarketSourceBinding::Massive { product, .. } => (
            "massive",
            match product {
                config::MassiveMarketProduct::Equity => "equity",
                config::MassiveMarketProduct::Options => "options",
            },
        ),
        MarketSourceBinding::Okx {
            instrument_type, ..
        } => (
            "okx",
            match instrument_type {
                config::OkxInstrumentType::Spot => "spot",
                config::OkxInstrumentType::Swap => "swap",
                config::OkxInstrumentType::Futures => "futures",
                config::OkxInstrumentType::Options => "options",
            },
        ),
        MarketSourceBinding::Hyperliquid {
            market_type: configured,
            ..
        } => (
            "hyperliquid",
            match configured {
                config::HyperliquidMarketType::Spot => "spot",
                config::HyperliquidMarketType::Perpetual => "perpetual",
            },
        ),
    };
    provider == expected_provider && provider_product == expected_product
}
