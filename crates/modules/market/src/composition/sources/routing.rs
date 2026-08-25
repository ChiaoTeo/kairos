//! Market-owned route matching for concrete source bindings.

use super::super::config::{
    self, BinanceDerivativeProduct, BinanceDerivativeTransport, BinanceSpotTransport,
    MarketProviderBinding, PublicMarketTransport,
};
use crate::ObservationKind;

pub(crate) fn binding_observation_capabilities(
    binding: &MarketProviderBinding,
) -> Vec<ObservationKind> {
    use ObservationKind::{Bar, IndexPrice, OptionGreeks, OrderBook, Quote, Trade};
    match binding {
        MarketProviderBinding::BinanceSpot {
            transport: BinanceSpotTransport::Rest,
            ..
        } => vec![Quote],
        MarketProviderBinding::BinanceSpot {
            transport: BinanceSpotTransport::Websocket,
            ..
        } => vec![Quote, Trade, Bar, OrderBook],
        MarketProviderBinding::BinanceEquity { .. } => vec![Quote],
        MarketProviderBinding::BinanceDerivatives {
            product: BinanceDerivativeProduct::Options,
            transport: BinanceDerivativeTransport::Rest,
            ..
        } => vec![Quote, OptionGreeks],
        MarketProviderBinding::BinanceDerivatives {
            product: BinanceDerivativeProduct::Options,
            transport: BinanceDerivativeTransport::Websocket,
            ..
        } => vec![Quote, Trade, OrderBook, OptionGreeks],
        MarketProviderBinding::BinanceDerivatives {
            transport: BinanceDerivativeTransport::Rest,
            ..
        } => vec![Quote],
        MarketProviderBinding::BinanceDerivatives {
            transport: BinanceDerivativeTransport::Websocket,
            ..
        } => vec![Quote, Trade, OrderBook],
        MarketProviderBinding::Okx {
            transport: PublicMarketTransport::Rest,
            ..
        }
        | MarketProviderBinding::Hyperliquid {
            transport: PublicMarketTransport::Rest,
            ..
        } => vec![Quote],
        MarketProviderBinding::Okx {
            transport: PublicMarketTransport::Websocket,
            ..
        }
        | MarketProviderBinding::Hyperliquid {
            transport: PublicMarketTransport::Websocket,
            ..
        } => vec![Trade, OrderBook],
        MarketProviderBinding::Massive { product, .. } => match product {
            config::MassiveMarketProduct::Equity
            | config::MassiveMarketProduct::Options
            | config::MassiveMarketProduct::Futures => vec![Quote, Trade, Bar],
            config::MassiveMarketProduct::Indices => vec![IndexPrice, Bar],
            config::MassiveMarketProduct::Forex => vec![Quote, Bar],
            config::MassiveMarketProduct::Crypto => vec![Quote, Trade, Bar],
        },
        MarketProviderBinding::Ibkr { .. } => vec![Quote],
    }
}

pub(crate) fn binding_provider_segment(
    binding: &MarketProviderBinding,
) -> (&'static str, &'static str) {
    match binding {
        MarketProviderBinding::BinanceSpot { .. } => ("binance", "spot"),
        MarketProviderBinding::BinanceEquity { .. } => ("binance", "equity"),
        MarketProviderBinding::BinanceDerivatives { product, .. } => (
            "binance",
            match product {
                BinanceDerivativeProduct::UsdMFutures => "usd-m-futures",
                BinanceDerivativeProduct::CoinMFutures => "coin-m-futures",
                BinanceDerivativeProduct::Options => "options",
            },
        ),
        MarketProviderBinding::Massive { product, .. } => (
            "massive",
            match product {
                config::MassiveMarketProduct::Equity => "equity",
                config::MassiveMarketProduct::Options => "options",
                config::MassiveMarketProduct::Futures => "futures",
                config::MassiveMarketProduct::Indices => "indices",
                config::MassiveMarketProduct::Forex => "forex",
                config::MassiveMarketProduct::Crypto => "crypto",
            },
        ),
        MarketProviderBinding::Okx {
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
        MarketProviderBinding::Hyperliquid {
            market_type: configured,
            ..
        } => (
            "hyperliquid",
            match configured {
                config::HyperliquidMarketType::Spot => "spot",
                config::HyperliquidMarketType::Perpetual => "perpetual",
            },
        ),
        MarketProviderBinding::Ibkr { .. } => ("ibkr", "equity"),
    }
}
