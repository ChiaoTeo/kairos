//! Explicit one-shot diagnostic composition used only by `kairos-market-cli`.
//! Production topology is selected exclusively from Workspace profiles.

use kairos_integration::participants::binance::{
    coinm::BinanceCoinMRestConnection,
    options::BinanceOptionsRestConnection,
    spot::{BinanceSpotRestConnection, BinanceSpotWebSocketConnection},
    usdm::BinanceUsdMRestConnection,
    BinanceRestConfig, BinanceWebSocketConfig,
};

use crate::MarketApplication;

use super::super::sources::{attach_binance_snapshot, attach_stream, MarketProduct};

pub fn attach_binance_spot_source(
    application: &mut MarketApplication,
    endpoint: impl Into<String>,
) -> Result<(), String> {
    attach_stream(
        application,
        "binance.public.websocket",
        "binance",
        "spot",
        "crypto",
        [
            crate::ObservationKind::Quote,
            crate::ObservationKind::Trade,
            crate::ObservationKind::Bar,
            crate::ObservationKind::OrderBook,
        ],
        BinanceSpotWebSocketConnection::new(BinanceWebSocketConfig {
            binding_id: "binance.public.websocket".into(),
            environment: "public".into(),
            endpoint: endpoint.into(),
            credential: None,
            event_capacity: 4_096,
        })
        .map_err(|error| error.to_string())?,
    )
}

pub fn attach_binance_spot_rest_source(
    application: &mut MarketApplication,
    endpoint: impl Into<String>,
) -> Result<(), String> {
    attach_binance_snapshot(
        application,
        "binance.public.rest",
        "spot",
        "crypto",
        BinanceSpotRestConnection::new(BinanceRestConfig {
            binding_id: "binance.public.rest".into(),
            environment: "public".into(),
            endpoint: endpoint.into(),
            credential: None,
        })
        .map_err(|error| error.to_string())?,
        std::time::Duration::from_secs(1),
    )
}

pub fn attach_binance_derivatives_source(
    application: &mut MarketApplication,
    product: MarketProduct,
    endpoint: impl Into<String>,
    _path: impl Into<String>,
) -> Result<(), String> {
    let (source_id, market_type) = match product {
        MarketProduct::UsdMFutures => ("binance.public.rest.usd-m-futures", "usd-m-futures"),
        MarketProduct::CoinMFutures => ("binance.public.rest.coin-m-futures", "coin-m-futures"),
        MarketProduct::Options => ("binance.public.rest.options", "options"),
        _ => {
            return Err("Binance derivatives diagnostic requires futures or options product".into())
        }
    };
    let config = BinanceRestConfig {
        binding_id: source_id.into(),
        environment: "public".into(),
        endpoint: endpoint.into(),
        credential: None,
    };
    match product {
        MarketProduct::UsdMFutures => attach_binance_snapshot(
            application,
            source_id,
            market_type,
            "crypto",
            BinanceUsdMRestConnection::new(config).map_err(|e| e.to_string())?,
            std::time::Duration::from_secs(1),
        ),
        MarketProduct::CoinMFutures => attach_binance_snapshot(
            application,
            source_id,
            market_type,
            "crypto",
            BinanceCoinMRestConnection::new(config).map_err(|e| e.to_string())?,
            std::time::Duration::from_secs(1),
        ),
        MarketProduct::Options => attach_binance_snapshot(
            application,
            source_id,
            market_type,
            "crypto",
            BinanceOptionsRestConnection::new(config).map_err(|e| e.to_string())?,
            std::time::Duration::from_secs(1),
        ),
        _ => Err("Binance derivatives diagnostic requires futures or options product".into()),
    }
}
