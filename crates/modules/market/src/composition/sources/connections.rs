use std::path::Path;

use kairos_conflux::{
    BinanceCredential, BinanceRestConfig, BinanceWebSocketConfig, ConfluxSystem, ConnectionKey,
    HyperliquidRestConfig, HyperliquidWebSocketConfig, IbkrMarketDataConfig,
    MassiveWebSocketConfig, OkxRestConfig, OkxWebSocketConfig,
};
use kairos_credentials::CredentialStore;

use super::super::config::{
    BinanceDerivativeProduct, BinanceDerivativeTransport, BinanceSpotTransport,
    HyperliquidMarketType, MarketProviderBinding, MassiveMarketProduct, OkxInstrumentType,
    PublicMarketTransport,
};
use super::{binding_observation_capabilities, default_endpoint, positive_interval};
use crate::ObservationKind;
use crate::application::process::{MarketSourceMode, MarketSourcePlan};
use crate::domain::source::{FeedDescriptor, MarketFeedId};

pub(crate) fn install(
    system: &mut ConfluxSystem,
    credentials_root: &Path,
    connections_root: &Path,
    sources: &std::collections::BTreeMap<String, MarketProviderBinding>,
) -> Result<Vec<MarketSourcePlan>, String> {
    let mut plans = Vec::new();
    for (source_id, binding) in sources.iter().filter(|(_, binding)| binding.enabled()) {
        install_one(
            system,
            credentials_root,
            connections_root,
            source_id,
            binding,
            &mut plans,
        )?;
    }
    Ok(plans)
}

fn install_one(
    system: &mut ConfluxSystem,
    credentials_root: &Path,
    connections_root: &Path,
    source_id: &str,
    binding: &MarketProviderBinding,
    plans: &mut Vec<MarketSourcePlan>,
) -> Result<(), String> {
    let key = source_id.to_owned();
    let capabilities = binding_observation_capabilities(binding);
    let connection = binding
        .connection_id()
        .map(|connection_id| {
            let profile = kairos_integration::composition::ProviderConnectionProfile::load(
                connections_root,
                connection_id,
            )?;
            let (provider, product, purpose) = connection_requirement(binding);
            profile.require(provider, product, purpose)?;
            Ok::<_, String>(profile)
        })
        .transpose()?;
    match binding {
        MarketProviderBinding::BinanceSpot {
            transport,
            endpoint,
            snapshot_interval_ms,
            ..
        } => {
            let descriptor =
                descriptor(source_id, "binance", "spot", "crypto", capabilities.clone())?;
            match transport {
                BinanceSpotTransport::Rest => {
                    system
                        .connections()
                        .binance_spot_rest
                        .create(
                            ConnectionKey::new(key.clone())?,
                            BinanceRestConfig {
                                environment: "public".into(),
                                endpoint: resolved_endpoint(
                                    connection.as_ref(),
                                    endpoint,
                                    default_endpoint("binance-spot-rest"),
                                    "market-query",
                                    Some("spot"),
                                ),
                                credential: None,
                            },
                        )
                        .map_err(|error| error.to_string())?;
                    plans.push(MarketSourcePlan {
                        descriptor,
                        mode: MarketSourceMode::Snapshot(positive_interval(
                            source_id,
                            *snapshot_interval_ms,
                        )?),
                    });
                },
                BinanceSpotTransport::Websocket => {
                    system
                        .connections()
                        .binance_spot_websocket
                        .create(
                            ConnectionKey::new(key.clone())?,
                            BinanceWebSocketConfig {
                                environment: "public".into(),
                                endpoint: resolved_endpoint(
                                    connection.as_ref(),
                                    endpoint,
                                    default_endpoint("binance-spot-websocket"),
                                    "market-stream",
                                    Some("spot"),
                                ),
                                credential: None,
                                event_capacity: 4_096,
                            },
                        )
                        .map_err(|error| error.to_string())?;
                    plans.push(MarketSourcePlan {
                        descriptor,
                        mode: MarketSourceMode::MarketScopedStream,
                    });
                },
            }
        },
        MarketProviderBinding::BinanceEquity {
            credential_id,
            endpoint,
            snapshot_interval_ms,
            ..
        } => {
            let credential_id = connection
                .as_ref()
                .map(|value| value.credential_id.as_str())
                .or(credential_id.as_deref())
                .ok_or_else(|| {
                    format!("Market source {source_id} requires a Binance connection or credential")
                })?;
            let credentials =
                CredentialStore::load(credentials_root).map_err(|error| error.to_string())?;
            let credential = credentials
                .find_provider("binance", Some(credential_id))
                .ok_or_else(|| {
                    format!("Market source {source_id} requires a Binance credential")
                })?;
            let api_key = credential
                .value("api_key")
                .cloned()
                .ok_or_else(|| format!("Market source {source_id} requires a Binance API key"))?;
            let secret = credential.value("api_secret").cloned().unwrap_or_default();
            system
                .connections()
                .binance_stocks_rest
                .create(
                    ConnectionKey::new(key.clone())?,
                    BinanceRestConfig {
                        environment: "public".into(),
                        endpoint: resolved_endpoint(
                            connection.as_ref(),
                            endpoint,
                            default_endpoint("binance-equity"),
                            "market-query",
                            Some("equity"),
                        ),
                        credential: Some(BinanceCredential {
                            principal_id: credential_id.to_owned(),
                            api_key,
                            secret,
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
                    capabilities.clone(),
                )?,
                mode: MarketSourceMode::Snapshot(positive_interval(
                    source_id,
                    *snapshot_interval_ms,
                )?),
            });
        },
        MarketProviderBinding::BinanceDerivatives {
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
            let descriptor = descriptor(
                source_id,
                "binance",
                product_name,
                "crypto",
                capabilities.clone(),
            )?;
            match transport {
                BinanceDerivativeTransport::Rest => {
                    let config = BinanceRestConfig {
                        environment: "public".into(),
                        endpoint: resolved_endpoint(
                            connection.as_ref(),
                            endpoint,
                            default_endpoint(rest_key),
                            "market-query",
                            Some(product_name),
                        ),
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
                    plans.push(MarketSourcePlan {
                        descriptor,
                        mode: MarketSourceMode::Snapshot(positive_interval(
                            source_id,
                            *snapshot_interval_ms,
                        )?),
                    });
                },
                BinanceDerivativeTransport::Websocket => {
                    let config = BinanceWebSocketConfig {
                        environment: "public".into(),
                        endpoint: resolved_endpoint(
                            connection.as_ref(),
                            endpoint,
                            default_endpoint(ws_key),
                            "market-stream",
                            Some(product_name),
                        ),
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
                },
            }
        },
        MarketProviderBinding::Okx {
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
                                endpoint: resolved_endpoint(
                                    connection.as_ref(),
                                    endpoint,
                                    default_endpoint("okx-spot-rest"),
                                    "market-query",
                                    Some(product),
                                ),
                            },
                        )
                        .map_err(|e| e.to_string())?;
                    plans.push(MarketSourcePlan {
                        descriptor: descriptor(
                            source_id,
                            "okx",
                            product,
                            "crypto",
                            capabilities.clone(),
                        )?,
                        mode: MarketSourceMode::Snapshot(positive_interval(
                            source_id,
                            *snapshot_interval_ms,
                        )?),
                    });
                },
                PublicMarketTransport::Websocket => {
                    system
                        .connections()
                        .okx_public_websocket
                        .create(
                            ConnectionKey::new(key.clone())?,
                            OkxWebSocketConfig {
                                environment: "public".into(),
                                endpoint: resolved_endpoint(
                                    connection.as_ref(),
                                    endpoint,
                                    default_endpoint("okx-public-websocket"),
                                    "market-stream",
                                    Some(product),
                                ),
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
                            capabilities.clone(),
                        )?,
                        mode: MarketSourceMode::Stream,
                    });
                },
            }
        },
        MarketProviderBinding::Hyperliquid {
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
                            capabilities.clone(),
                        )?,
                        mode: MarketSourceMode::Snapshot(positive_interval(
                            source_id,
                            *snapshot_interval_ms,
                        )?),
                    });
                },
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
                            capabilities.clone(),
                        )?,
                        mode: MarketSourceMode::Stream,
                    });
                },
            }
        },
        MarketProviderBinding::Massive {
            product,
            credential_id,
            endpoint,
            ..
        } => {
            let credential_id = connection
                .as_ref()
                .map(|value| value.credential_id.as_str())
                .or(credential_id.as_deref())
                .ok_or_else(|| {
                    format!("Market source {source_id} requires a Massive connection or credential")
                })?;
            let credentials =
                CredentialStore::load(credentials_root).map_err(|error| error.to_string())?;
            let api_key = credentials
                .find_provider("massive", Some(credential_id))
                .and_then(|credential| credential.value("api_key").cloned())
                .ok_or_else(|| {
                    format!("Market source {source_id} requires a Massive credential")
                })?;
            let (product_name, endpoint_key) = match product {
                MassiveMarketProduct::Equity => ("equity", "massive-equity-websocket"),
                MassiveMarketProduct::Options => ("options", "massive-options-websocket"),
                MassiveMarketProduct::Futures => ("futures", "massive-futures-websocket"),
                MassiveMarketProduct::Indices => ("indices", "massive-indices-websocket"),
                MassiveMarketProduct::Forex => ("forex", "massive-forex-websocket"),
                MassiveMarketProduct::Crypto => ("crypto", "massive-crypto-websocket"),
            };
            let config = MassiveWebSocketConfig {
                environment: "public".into(),
                endpoint: resolved_endpoint(
                    connection.as_ref(),
                    endpoint,
                    default_endpoint(endpoint_key),
                    "market-stream",
                    Some(product_name),
                ),
                api_key,
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
                MassiveMarketProduct::Futures => system
                    .connections()
                    .massive_futures_websocket
                    .create(connection_key, config),
                MassiveMarketProduct::Indices => system
                    .connections()
                    .massive_indices_websocket
                    .create(connection_key, config),
                MassiveMarketProduct::Forex => system
                    .connections()
                    .massive_forex_websocket
                    .create(connection_key, config),
                MassiveMarketProduct::Crypto => system
                    .connections()
                    .massive_crypto_websocket
                    .create(connection_key, config),
            }
            .map_err(|error| error.to_string())?;
            let mut descriptor = FeedDescriptor::all_routes(MarketFeedId::new(source_id)?);
            descriptor.provider = Some(
                kairos_primitives::market::Provider::new("massive")
                    .expect("code-owned provider identity is valid"),
            );
            descriptor.market_type = Some(crate::domain::market::ProviderSegmentCode::new(
                product_name,
            )?);
            let asset_class = match product {
                MassiveMarketProduct::Forex => "fiat",
                MassiveMarketProduct::Crypto => "crypto",
                MassiveMarketProduct::Equity
                | MassiveMarketProduct::Options
                | MassiveMarketProduct::Futures
                | MassiveMarketProduct::Indices => "equity",
            };
            descriptor.asset_type = Some(
                asset_class
                    .parse::<kairos_primitives::reference::AssetClass>()
                    .map_err(|e| e.to_string())?,
            );
            plans.push(MarketSourcePlan {
                descriptor: descriptor.with_observation_capabilities(capabilities.clone()),
                mode: MarketSourceMode::Stream,
            });
        },
        MarketProviderBinding::Ibkr {
            host,
            port,
            client_id,
            exchange,
            currency,
            market_data_line_limit,
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
                        market_data_line_limit: *market_data_line_limit,
                    },
                )
                .map_err(|error| error.to_string())?;
            let mut descriptor = FeedDescriptor::all_routes(MarketFeedId::new(source_id)?);
            descriptor.provider = Some(
                kairos_primitives::market::Provider::new("ibkr")
                    .expect("code-owned provider identity is valid"),
            );
            descriptor.market_type =
                Some(crate::domain::market::ProviderSegmentCode::new("equity")?);
            descriptor.asset_type = Some(
                "equity"
                    .parse::<kairos_primitives::reference::AssetClass>()
                    .map_err(|error| error.to_string())?,
            );
            plans.push(MarketSourcePlan {
                descriptor: descriptor.with_observation_capabilities(capabilities),
                mode: MarketSourceMode::Stream,
            });
        },
    }
    Ok(())
}

fn descriptor(
    source_id: &str,
    exchange: &str,
    product: &str,
    asset: &str,
    capabilities: impl IntoIterator<Item = ObservationKind>,
) -> Result<FeedDescriptor, String> {
    Ok(FeedDescriptor::for_provider(
        MarketFeedId::new(source_id)?,
        exchange,
        kairos_primitives::reference::ExchangeId::new(exchange).map_err(|e| e.to_string())?,
        product,
        Some(asset.into()),
    )?
    .with_observation_capabilities(capabilities))
}

fn resolved_endpoint(
    connection: Option<&kairos_integration::composition::ProviderConnectionProfile>,
    source_endpoint: &Option<String>,
    default: &str,
    purpose: &str,
    product: Option<&str>,
) -> String {
    source_endpoint
        .clone()
        .or_else(|| {
            connection.and_then(|value| value.endpoint_for(purpose, product).map(str::to_owned))
        })
        .unwrap_or_else(|| default.to_owned())
}

fn connection_requirement(
    binding: &MarketProviderBinding,
) -> (&'static str, Option<&'static str>, &'static str) {
    match binding {
        MarketProviderBinding::BinanceSpot { transport, .. } => (
            "binance",
            Some("spot"),
            match transport {
                BinanceSpotTransport::Rest => "market-query",
                BinanceSpotTransport::Websocket => "market-stream",
            },
        ),
        MarketProviderBinding::BinanceEquity { .. } => ("binance", Some("equity"), "market-query"),
        MarketProviderBinding::BinanceDerivatives {
            product, transport, ..
        } => (
            "binance",
            Some(match product {
                BinanceDerivativeProduct::UsdMFutures => "usd-m-futures",
                BinanceDerivativeProduct::CoinMFutures => "coin-m-futures",
                BinanceDerivativeProduct::Options => "options",
            }),
            match transport {
                BinanceDerivativeTransport::Rest => "market-query",
                BinanceDerivativeTransport::Websocket => "market-stream",
            },
        ),
        MarketProviderBinding::Massive { product, .. } => (
            "massive",
            Some(match product {
                MassiveMarketProduct::Equity => "equity",
                MassiveMarketProduct::Options => "options",
                MassiveMarketProduct::Futures => "futures",
                MassiveMarketProduct::Indices => "indices",
                MassiveMarketProduct::Forex => "forex",
                MassiveMarketProduct::Crypto => "crypto",
            }),
            "market-stream",
        ),
        MarketProviderBinding::Okx {
            instrument_type,
            transport,
            ..
        } => (
            "okx",
            Some(match instrument_type {
                OkxInstrumentType::Spot => "spot",
                OkxInstrumentType::Swap => "swap",
                OkxInstrumentType::Futures => "futures",
                OkxInstrumentType::Options => "options",
            }),
            match transport {
                PublicMarketTransport::Rest => "market-query",
                PublicMarketTransport::Websocket => "market-stream",
            },
        ),
        MarketProviderBinding::Hyperliquid { .. } => ("hyperliquid", None, "market-query"),
        MarketProviderBinding::Ibkr { .. } => ("ibkr", None, "market-stream"),
    }
}

#[cfg(test)]
mod tests {
    use kairos_integration::composition::ProviderConnectionProfile;

    use super::resolved_endpoint;

    fn connection(endpoint: &str) -> ProviderConnectionProfile {
        ProviderConnectionProfile {
            connection_id: "provider-main".into(),
            provider: "provider".into(),
            environment: "production".into(),
            endpoint: endpoint.into(),
            endpoints: Default::default(),
            credential_id: "provider-readonly".into(),
            enabled: true,
            products: vec!["equity".into()],
            purposes: vec!["market-stream".into()],
        }
    }

    #[test]
    fn source_endpoint_overrides_connection_endpoint() {
        let connection = connection("https://api.provider.example");

        assert_eq!(
            resolved_endpoint(
                Some(&connection),
                &Some("wss://stream.provider.example/equity".into()),
                "wss://default.provider.example",
                "market-stream",
                Some("equity"),
            ),
            "wss://stream.provider.example/equity"
        );
    }

    #[test]
    fn connection_endpoint_precedes_provider_default() {
        let connection = connection("https://api.provider.example");

        assert_eq!(
            resolved_endpoint(
                Some(&connection),
                &None,
                "wss://default.provider.example",
                "market-stream",
                Some("equity"),
            ),
            "https://api.provider.example"
        );
    }

    #[test]
    fn provider_default_is_used_without_configured_endpoint() {
        assert_eq!(
            resolved_endpoint(
                None,
                &None,
                "wss://default.provider.example",
                "market-stream",
                Some("equity"),
            ),
            "wss://default.provider.example"
        );
    }

    #[test]
    fn version_two_profile_does_not_reuse_rest_endpoint_for_streaming() {
        let mut connection = connection("https://api.provider.example");
        connection.endpoints.insert(
            "market-query".into(),
            "https://query.provider.example".into(),
        );

        assert_eq!(
            resolved_endpoint(
                Some(&connection),
                &None,
                "wss://default.provider.example",
                "market-stream",
                Some("equity"),
            ),
            "wss://default.provider.example"
        );
    }
}
