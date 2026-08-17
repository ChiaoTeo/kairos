//! Explicit one-shot diagnostic composition used only by `kairos-market-cli`.
//! Production topology is selected exclusively from Workspace profiles.

use kairos_integration::participants::binance::{self, ConnectionDomain};

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
        binance::spot_websocket_market(endpoint).map_err(|error| error.to_string())?,
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
        binance::spot_snapshot(endpoint).map_err(|error| error.to_string())?,
        std::time::Duration::from_secs(1),
    )
}

pub fn attach_binance_derivatives_source(
    application: &mut MarketApplication,
    product: MarketProduct,
    endpoint: impl Into<String>,
    path: impl Into<String>,
) -> Result<(), String> {
    let (source_id, market_type, domain) = match product {
        MarketProduct::UsdMFutures => (
            "binance.public.rest.usd-m-futures",
            "usd-m-futures",
            ConnectionDomain::UsdMFutures,
        ),
        MarketProduct::CoinMFutures => (
            "binance.public.rest.coin-m-futures",
            "coin-m-futures",
            ConnectionDomain::CoinMFutures,
        ),
        MarketProduct::Options => (
            "binance.public.rest.options",
            "options",
            ConnectionDomain::Options,
        ),
        _ => {
            return Err("Binance derivatives diagnostic requires futures or options product".into())
        }
    };
    attach_binance_snapshot(
        application,
        source_id,
        market_type,
        "crypto",
        binance::derivatives_snapshot(domain, endpoint, path).map_err(|error| error.to_string())?,
        std::time::Duration::from_secs(1),
    )
}
