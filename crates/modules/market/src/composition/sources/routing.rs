//! Market-owned route matching for concrete source bindings.

use super::super::config::{self, BinanceDerivativeProduct, MarketSourceBinding};

pub(crate) fn binding_provider_product(
    binding: &MarketSourceBinding,
) -> (&'static str, &'static str) {
    match binding {
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
        MarketSourceBinding::Ibkr { .. } => ("ibkr", "equity"),
    }
}

pub(crate) fn binding_supports_canonical_market(
    binding: &MarketSourceBinding,
    exchange_id: &str,
    kind: kairos_primitives::reference::InstrumentKind,
) -> bool {
    use kairos_primitives::reference::InstrumentKind::{Future, Option, Perpetual, Spot};
    match binding {
        MarketSourceBinding::BinanceSpot { .. } => {
            exchange_id.eq_ignore_ascii_case("exchange:binance") && kind == Spot
        },
        MarketSourceBinding::BinanceDerivatives { product, .. } => {
            exchange_id.eq_ignore_ascii_case("exchange:binance")
                && match product {
                    BinanceDerivativeProduct::Options => kind == Option,
                    BinanceDerivativeProduct::UsdMFutures
                    | BinanceDerivativeProduct::CoinMFutures => {
                        matches!(kind, Future | Perpetual)
                    },
                }
        },
        MarketSourceBinding::Okx {
            instrument_type, ..
        } => {
            exchange_id.eq_ignore_ascii_case("exchange:okx")
                && match instrument_type {
                    config::OkxInstrumentType::Spot => kind == Spot,
                    config::OkxInstrumentType::Swap => kind == Perpetual,
                    config::OkxInstrumentType::Futures => kind == Future,
                    config::OkxInstrumentType::Options => kind == Option,
                }
        },
        MarketSourceBinding::Hyperliquid {
            market_type: configured,
            ..
        } => {
            exchange_id.eq_ignore_ascii_case("exchange:hyperliquid")
                && match configured {
                    config::HyperliquidMarketType::Spot => kind == Spot,
                    config::HyperliquidMarketType::Perpetual => kind == Perpetual,
                }
        },
        // Both are broker/data-provider surfaces rather than canonical venues.
        MarketSourceBinding::BinanceEquity { .. }
        | MarketSourceBinding::Massive { .. }
        | MarketSourceBinding::Ibkr { .. } => false,
    }
}
