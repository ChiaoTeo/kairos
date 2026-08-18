use std::path::Path;

use kairos_conflux::{
    load_workspace_credential, BinanceCredential, BinanceRestConfig, BinanceWebSocketConfig,
    ConfluxSystem, ConnectionKey, HyperliquidRestConfig, HyperliquidWebSocketConfig,
    IbkrMarketDataConfig, MassiveWebSocketConfig, OkxRestConfig, OkxWebSocketConfig,
};

use crate::application::conflux::{MarketSourceMode, MarketSourcePlan};
use crate::domain::source::{SourceDescriptor, SourceId};
use crate::ObservationKind;

use super::super::config::{
    BinanceDerivativeProduct, BinanceDerivativeTransport, BinanceSpotTransport,
    HyperliquidMarketType, MarketSourceBinding, MassiveMarketProduct, OkxInstrumentType,
    PublicMarketTransport,
};
use super::{default_endpoint, positive_interval};

pub(crate) fn install(
    system: &mut ConfluxSystem,
    credentials_root: &Path,
    sources: &std::collections::BTreeMap<String, MarketSourceBinding>,
) -> Result<Vec<MarketSourcePlan>, String> {
    let mut plans = Vec::new();
    for (source_id, binding) in sources.iter().filter(|(_, binding)| binding.enabled()) {
        install_one(system, credentials_root, source_id, binding, &mut plans)?;
    }
    if !sources
        .values()
        .any(|binding| matches!(binding, MarketSourceBinding::BinanceSpot { .. }))
    {
        install_one(
            system,
            credentials_root,
            "binance-spot",
            &MarketSourceBinding::BinanceSpot {
                enabled: true,
                transport: BinanceSpotTransport::Websocket,
                endpoint: None,
                snapshot_interval_ms: 1_000,
            },
            &mut plans,
        )?;
    }
    Ok(plans)
}

fn install_one(
    system: &mut ConfluxSystem,
    credentials_root: &Path,
    source_id: &str,
    binding: &MarketSourceBinding,
    plans: &mut Vec<MarketSourcePlan>,
) -> Result<(), String> {
    let key = source_id.to_owned();
    match binding {
        MarketSourceBinding::BinanceSpot {
            transport,
            endpoint,
            snapshot_interval_ms,
            ..
        } => {
            let descriptor = descriptor(source_id, "binance", "spot", "crypto", stream_kinds())?;
            match transport {
                BinanceSpotTransport::Rest => {
                    system
                        .connections()
                        .binance_spot_rest
                        .create(
                            ConnectionKey::new(key.clone())?,
                            BinanceRestConfig {
                                environment: "public".into(),
                                endpoint: endpoint.clone().unwrap_or_else(|| {
                                    default_endpoint("binance-spot-rest").into()
                                }),
                                credential: None,
                            },
                        )
                        .map_err(|error| error.to_string())?;
                    plans.push(MarketSourcePlan {
                        descriptor: descriptor
                            .with_observation_capabilities([ObservationKind::Quote]),
                        mode: MarketSourceMode::Snapshot(positive_interval(
                            source_id,
                            *snapshot_interval_ms,
                        )?),
                    });
                }
                BinanceSpotTransport::Websocket => {
                    system
                        .connections()
                        .binance_spot_websocket
                        .create(
                            ConnectionKey::new(key.clone())?,
                            BinanceWebSocketConfig {
                                environment: "public".into(),
                                endpoint: endpoint.clone().unwrap_or_else(|| {
                                    default_endpoint("binance-spot-websocket").into()
                                }),
                                credential: None,
                                event_capacity: 4_096,
                            },
                        )
                        .map_err(|error| error.to_string())?;
                    plans.push(MarketSourcePlan {
                        descriptor,
                        mode: MarketSourceMode::MarketScopedStream,
                    });
                }
            }
        }
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
            system
                .connections()
                .binance_stocks_rest
                .create(
                    ConnectionKey::new(key.clone())?,
                    BinanceRestConfig {
                        environment: "public".into(),
                        endpoint: endpoint
                            .clone()
                            .unwrap_or_else(|| default_endpoint("binance-equity").into()),
                        credential: Some(BinanceCredential {
                            principal_id: credential_id.clone(),
                            api_key: secrecy::SecretString::from(credential.api_key),
                            secret: credential.secret,
                        }),
                    },
                )
                .map_err(|error| error.to_string())?;
            plans.push(MarketSourcePlan {
                descriptor: descriptor(
                    source_id,
                    "binance",
                    "equity",
                    "equity",
                    [ObservationKind::Quote],
                )?,
                mode: MarketSourceMode::Snapshot(positive_interval(
                    source_id,
                    *snapshot_interval_ms,
                )?),
            });
        }
        MarketSourceBinding::BinanceDerivatives {
            product,
            transport,
            endpoint,
            snapshot_interval_ms,
            ..
        } => {
            let (product_name, rest_key, ws_key) = match product {
                BinanceDerivativeProduct::UsdMFutures => (
                    "usd-m-futures",
                    "binance-usdm-futures-rest",
                    "binance-usdm-futures-websocket",
                ),
                BinanceDerivativeProduct::CoinMFutures => (
                    "coin-m-futures",
                    "binance-coinm-futures-rest",
                    "binance-coinm-futures-websocket",
                ),
                BinanceDerivativeProduct::Options => (
                    "options",
                    "binance-options-rest",
                    "binance-options-websocket",
                ),
            };
            let descriptor =
                descriptor(source_id, "binance", product_name, "crypto", stream_kinds())?;
            match transport {
                BinanceDerivativeTransport::Rest => {
                    let config = BinanceRestConfig {
                        environment: "public".into(),
                        endpoint: endpoint
                            .clone()
                            .unwrap_or_else(|| default_endpoint(rest_key).into()),
                        credential: None,
                    };
                    match product {
                        BinanceDerivativeProduct::UsdMFutures => system
                            .connections()
                            .binance_usdm_rest
                            .create(ConnectionKey::new(key.clone())?, config),
                        BinanceDerivativeProduct::CoinMFutures => system
                            .connections()
                            .binance_coinm_rest
                            .create(ConnectionKey::new(key.clone())?, config),
                        BinanceDerivativeProduct::Options => system
                            .connections()
                            .binance_options_rest
                            .create(ConnectionKey::new(key.clone())?, config),
                    }
                    .map_err(|error| error.to_string())?;
                    let kinds = if matches!(product, BinanceDerivativeProduct::Options) {
                        vec![ObservationKind::Quote, ObservationKind::OptionGreeks]
                    } else {
                        vec![ObservationKind::Quote]
                    };
                    plans.push(MarketSourcePlan {
                        descriptor: descriptor.with_observation_capabilities(kinds),
                        mode: MarketSourceMode::Snapshot(positive_interval(
                            source_id,
                            *snapshot_interval_ms,
                        )?),
                    });
                }
                BinanceDerivativeTransport::Websocket => {
                    let config = BinanceWebSocketConfig {
                        environment: "public".into(),
                        endpoint: endpoint
                            .clone()
                            .unwrap_or_else(|| default_endpoint(ws_key).into()),
                        credential: None,
                        event_capacity: 4_096,
                    };
                    match product {
                        BinanceDerivativeProduct::UsdMFutures => system
                            .connections()
                            .binance_usdm_websocket
                            .create(ConnectionKey::new(key.clone())?, config),
                        BinanceDerivativeProduct::CoinMFutures => system
                            .connections()
                            .binance_coinm_websocket
                            .create(ConnectionKey::new(key.clone())?, config),
                        BinanceDerivativeProduct::Options => system
                            .connections()
                            .binance_options_websocket
                            .create(ConnectionKey::new(key.clone())?, config),
                    }
                    .map_err(|error| error.to_string())?;
                    plans.push(MarketSourcePlan {
                        descriptor,
                        mode: MarketSourceMode::MarketScopedStream,
                    });
                }
            }
        }
        MarketSourceBinding::Okx {
            instrument_type,
            transport,
            endpoint,
            snapshot_interval_ms,
            ..
        } => {
            let product = match instrument_type {
                OkxInstrumentType::Spot => "spot",
                OkxInstrumentType::Swap => "swap",
                OkxInstrumentType::Futures => "futures",
                OkxInstrumentType::Options => "options",
            };
            match transport {
                PublicMarketTransport::Rest => {
                    system
                        .connections()
                        .okx_public_rest
                        .create(
                            ConnectionKey::new(key.clone())?,
                            OkxRestConfig {
                                environment: "public".into(),
                                endpoint: endpoint
                                    .clone()
                                    .unwrap_or_else(|| default_endpoint("okx-spot-rest").into()),
                            },
                        )
                        .map_err(|e| e.to_string())?;
                    plans.push(MarketSourcePlan {
                        descriptor: descriptor(
                            source_id,
                            "okx",
                            product,
                            "crypto",
                            [ObservationKind::Quote],
                        )?,
                        mode: MarketSourceMode::Snapshot(positive_interval(
                            source_id,
                            *snapshot_interval_ms,
                        )?),
                    });
                }
                PublicMarketTransport::Websocket => {
                    system
                        .connections()
                        .okx_public_websocket
                        .create(
                            ConnectionKey::new(key.clone())?,
                            OkxWebSocketConfig {
                                environment: "public".into(),
                                endpoint: endpoint.clone().unwrap_or_else(|| {
                                    default_endpoint("okx-public-websocket").into()
                                }),
                                event_capacity: 4_096,
                            },
                        )
                        .map_err(|e| e.to_string())?;
                    plans.push(MarketSourcePlan {
                        descriptor: descriptor(
                            source_id,
                            "okx",
                            product,
                            "crypto",
                            [ObservationKind::Trade, ObservationKind::OrderBook],
                        )?,
                        mode: MarketSourceMode::Stream,
                    });
                }
            }
        }
        MarketSourceBinding::Hyperliquid {
            market_type,
            transport,
            endpoint,
            snapshot_interval_ms,
            ..
        } => {
            let product = match market_type {
                HyperliquidMarketType::Spot => "spot",
                HyperliquidMarketType::Perpetual => "perpetual",
            };
            match transport {
                PublicMarketTransport::Rest => {
                    system
                        .connections()
                        .hyperliquid_info_rest
                        .create(
                            ConnectionKey::new(key.clone())?,
                            HyperliquidRestConfig {
                                environment: "public".into(),
                                endpoint: endpoint
                                    .clone()
                                    .unwrap_or_else(|| default_endpoint("hyperliquid-info").into()),
                            },
                        )
                        .map_err(|e| e.to_string())?;
                    plans.push(MarketSourcePlan {
                        descriptor: descriptor(
                            source_id,
                            "hyperliquid",
                            product,
                            "crypto",
                            [ObservationKind::Quote],
                        )?,
                        mode: MarketSourceMode::Snapshot(positive_interval(
                            source_id,
                            *snapshot_interval_ms,
                        )?),
                    });
                }
                PublicMarketTransport::Websocket => {
                    system
                        .connections()
                        .hyperliquid_websocket
                        .create(
                            ConnectionKey::new(key.clone())?,
                            HyperliquidWebSocketConfig {
                                environment: "public".into(),
                                endpoint: endpoint.clone().unwrap_or_else(|| {
                                    default_endpoint("hyperliquid-websocket").into()
                                }),
                                event_capacity: 4_096,
                                user: None,
                            },
                        )
                        .map_err(|e| e.to_string())?;
                    plans.push(MarketSourcePlan {
                        descriptor: descriptor(
                            source_id,
                            "hyperliquid",
                            product,
                            "crypto",
                            [ObservationKind::Trade, ObservationKind::OrderBook],
                        )?,
                        mode: MarketSourceMode::Stream,
                    });
                }
            }
        }
        MarketSourceBinding::Massive {
            product,
            credential_id,
            endpoint,
            ..
        } => {
            let credential =
                load_workspace_credential(credentials_root, "massive", Some(credential_id))?
                    .ok_or_else(|| {
                        format!("Market source {source_id} requires a Massive credential")
                    })?;
            let (product_name, endpoint_key) = match product {
                MassiveMarketProduct::Equity => ("equity", "massive-equity-websocket"),
                MassiveMarketProduct::Options => ("options", "massive-options-websocket"),
            };
            let config = MassiveWebSocketConfig {
                environment: "public".into(),
                endpoint: endpoint
                    .clone()
                    .unwrap_or_else(|| default_endpoint(endpoint_key).into()),
                api_key: secrecy::SecretString::new(credential.api_key.into()),
                event_capacity: 4_096,
            };
            let connection_key = ConnectionKey::new(key.clone())?;
            match product {
                MassiveMarketProduct::Equity => system
                    .connections()
                    .massive_stocks_websocket
                    .create(connection_key, config),
                MassiveMarketProduct::Options => system
                    .connections()
                    .massive_options_websocket
                    .create(connection_key, config),
            }
            .map_err(|error| error.to_string())?;
            let mut descriptor = SourceDescriptor::all_routes(SourceId::new(source_id)?);
            descriptor.market_type = Some(
                kairos_primitives::ProviderProductCode::new(product_name)
                    .map_err(|e| e.to_string())?,
            );
            descriptor.asset_type = Some(
                "equity"
                    .parse::<kairos_primitives::AssetClass>()
                    .map_err(|e| e.to_string())?,
            );
            plans.push(MarketSourcePlan {
                descriptor: descriptor.with_observation_capabilities([
                    ObservationKind::Quote,
                    ObservationKind::Trade,
                ]),
                mode: MarketSourceMode::Stream,
            });
        }
        MarketSourceBinding::Ibkr {
            host,
            port,
            client_id,
            exchange,
            currency,
            snapshot_interval_ms,
            ..
        } => {
            system
                .connections()
                .ibkr_market_data
                .create(
                    ConnectionKey::new(key.clone())?,
                    IbkrMarketDataConfig {
                        environment: "tws".into(),
                        host: host.clone(),
                        port: *port,
                        client_id: *client_id,
                        exchange: exchange.clone(),
                        currency: currency.clone(),
                    },
                )
                .map_err(|error| error.to_string())?;
            let mut descriptor = SourceDescriptor::all_routes(SourceId::new(source_id)?);
            descriptor.market_type = Some(
                kairos_primitives::ProviderProductCode::new("equity")
                    .map_err(|error| error.to_string())?,
            );
            descriptor.asset_type = Some(
                "equity"
                    .parse::<kairos_primitives::AssetClass>()
                    .map_err(|error| error.to_string())?,
            );
            plans.push(MarketSourcePlan {
                descriptor: descriptor.with_observation_capabilities([ObservationKind::Quote]),
                mode: MarketSourceMode::Snapshot(positive_interval(
                    source_id,
                    *snapshot_interval_ms,
                )?),
            });
        }
    }
    Ok(())
}

fn descriptor(
    source_id: &str,
    exchange: &str,
    product: &str,
    asset: &str,
    capabilities: impl IntoIterator<Item = ObservationKind>,
) -> Result<SourceDescriptor, String> {
    Ok(SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_primitives::Exchange::new(exchange).map_err(|e| e.to_string())?,
        product,
        Some(asset.into()),
    )?
    .with_observation_capabilities(capabilities))
}

fn stream_kinds() -> [ObservationKind; 4] {
    [
        ObservationKind::Quote,
        ObservationKind::Trade,
        ObservationKind::Bar,
        ObservationKind::OrderBook,
    ]
}
