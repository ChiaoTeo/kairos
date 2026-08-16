use std::path::Path;

use super::super::config::{
    BinanceDerivativeProduct, BinanceDerivativeTransport, BinanceSpotTransport, MarketSourceBinding,
};
use kairos_integration::application::credential::load_workspace_credential;
use kairos_integration::participants::binance::{
    self, ConnectionDomain as BinanceConnectionDomain,
};
use kairos_integration::participants::binance::{
    BinanceQuotaAllocation, BinanceSpotConnection, BinanceSpotConnectionConfig,
};
use secrecy::SecretString;

use crate::MarketApplication;

use super::super::{attach_binance_snapshot, attach_binance_stream, default_endpoint};
use super::positive_interval;

pub(super) fn attach(
    application: &mut MarketApplication,
    credentials_root: &Path,
    source_id: &str,
    binding: &MarketSourceBinding,
) -> Result<(), String> {
    match binding {
        MarketSourceBinding::BinanceEquity {
            credential_id,
            endpoint,
            snapshot_interval_ms,
            ..
        } => {
            let credential =
                load_workspace_credential(credentials_root, "binance", Some(credential_id))?
                    .ok_or_else(|| {
                        format!("Market source {source_id} requires a Binance credential")
                    })?;
            let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
                environment: "public".into(),
                rest_base_url: endpoint
                    .clone()
                    .unwrap_or_else(|| default_endpoint("binance-equity").to_owned()),
                quota: BinanceQuotaAllocation {
                    request_weight_per_minute: 1_200,
                    cancel_reserve_weight: 0,
                },
                shared_quota: None,
            })
            .map_err(|error| error.to_string())?;
            return super::super::attach_binance_snapshot(
                application,
                source_id,
                "equity",
                "equity",
                provider.equity_market_quote(SecretString::new(credential.api_key.into())),
                positive_interval(source_id, *snapshot_interval_ms)?,
            );
        }
        MarketSourceBinding::BinanceSpot {
            transport,
            endpoint,
            snapshot_interval_ms,
            ..
        } => {
            let endpoint = endpoint.clone().unwrap_or_else(|| {
                default_endpoint(match transport {
                    BinanceSpotTransport::Rest => "binance-spot-rest",
                    BinanceSpotTransport::Websocket => "binance-spot-websocket",
                })
                .to_owned()
            });
            if *transport == BinanceSpotTransport::Rest {
                let connection =
                    binance::spot_snapshot(endpoint).map_err(|error| error.to_string())?;
                return attach_binance_snapshot(
                    application,
                    source_id,
                    "spot",
                    "crypto",
                    connection,
                    positive_interval(source_id, *snapshot_interval_ms)?,
                );
            }
            let connection = match transport {
                BinanceSpotTransport::Rest => unreachable!("handled above"),
                BinanceSpotTransport::Websocket => binance::spot_websocket_market(endpoint),
            }
            .map_err(|error| error.to_string())?;
            attach_binance_stream(application, source_id, "spot", "crypto", connection)
        }
        MarketSourceBinding::BinanceDerivatives {
            product,
            transport,
            endpoint,
            snapshot_interval_ms,
            ..
        } => {
            let (market_type, domain, rest_path, rest_endpoint, websocket_endpoint) = match product
            {
                BinanceDerivativeProduct::UsdMFutures => (
                    "usd-m-futures",
                    BinanceConnectionDomain::UsdMFutures,
                    "/fapi/v1/ticker/bookTicker",
                    "binance-usdm-futures-rest",
                    "binance-usdm-futures-websocket",
                ),
                BinanceDerivativeProduct::CoinMFutures => (
                    "coin-m-futures",
                    BinanceConnectionDomain::CoinMFutures,
                    "/dapi/v1/ticker/bookTicker",
                    "binance-coinm-futures-rest",
                    "binance-coinm-futures-websocket",
                ),
                BinanceDerivativeProduct::Options => (
                    "options",
                    BinanceConnectionDomain::Options,
                    "/eapi/v1/ticker",
                    "binance-options-rest",
                    "binance-options-websocket",
                ),
            };
            let endpoint = endpoint.clone().unwrap_or_else(|| {
                default_endpoint(match transport {
                    BinanceDerivativeTransport::Websocket => websocket_endpoint,
                    BinanceDerivativeTransport::Rest => rest_endpoint,
                })
                .to_owned()
            });
            if *transport == BinanceDerivativeTransport::Rest {
                let connection = binance::derivatives_snapshot(domain, endpoint, rest_path)
                    .map_err(|error| error.to_string())?;
                return attach_binance_snapshot(
                    application,
                    source_id,
                    market_type,
                    "crypto",
                    connection,
                    positive_interval(source_id, *snapshot_interval_ms)?,
                );
            }
            let connection = match transport {
                BinanceDerivativeTransport::Rest => unreachable!("handled above"),
                BinanceDerivativeTransport::Websocket
                    if *product == BinanceDerivativeProduct::Options =>
                {
                    binance::options_websocket_market(endpoint)
                }
                BinanceDerivativeTransport::Websocket => {
                    binance::futures_websocket_market(domain, endpoint)
                }
            }
            .map_err(|error| error.to_string())?;
            attach_binance_stream(application, source_id, market_type, "crypto", connection)
        }
        _ => Err(format!(
            "Market source {source_id} is not a Binance binding"
        )),
    }
}
