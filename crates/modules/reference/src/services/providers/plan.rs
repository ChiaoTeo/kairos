use kairos_conflux::ConfluxSystem;
use kairos_integration::participants::{binance, hyperliquid, massive, okx};

use super::{
    BinanceDerivativesSource, BinanceEquitySource, BinanceOptionsSource, BinanceSpotSource,
    CompositeSource, HyperliquidProduct, HyperliquidSource, MassiveEquitySource,
    MassiveOptionsCoverageSource, OkxProduct, OkxSource, ParticipantAugmentedSource,
};
use crate::domain::{Entity, ReferenceError, ReferenceResult};
use crate::services::source::{ConfiguredProviderSource, ConfiguredReferenceSource};
use crate::services::sqlx_storage::SqlxProviderSyncStore;

pub(crate) enum ReferenceProviderPlan {
    BinanceSpot {
        key: String,
        endpoint: String,
    },
    BinanceUsdM {
        key: String,
        endpoint: String,
    },
    BinanceCoinM {
        key: String,
        endpoint: String,
    },
    BinanceOptions {
        key: String,
        endpoint: String,
    },
    BinanceEquity {
        key: String,
        endpoint: String,
        credential: binance::BinanceCredential,
    },
    Okx {
        key: String,
        source_id: String,
        product: OkxProduct,
        endpoint: String,
    },
    Hyperliquid {
        key: String,
        product: HyperliquidProduct,
        endpoint: String,
    },
    MassiveEquity {
        key: String,
        api_key: String,
        endpoint: String,
        sync_store: SqlxProviderSyncStore,
    },
    MassiveOptions {
        api_key: String,
        endpoint: String,
        sync_store: SqlxProviderSyncStore,
        underlyings: Vec<String>,
    },
}

pub(crate) struct ReferenceSourcePlan {
    providers: Vec<ReferenceProviderPlan>,
    participants: Vec<Entity>,
    sync_store: SqlxProviderSyncStore,
}

impl ReferenceSourcePlan {
    pub(crate) fn new(
        providers: Vec<ReferenceProviderPlan>,
        participants: Vec<Entity>,
        sync_store: SqlxProviderSyncStore,
    ) -> Self {
        Self {
            providers,
            participants,
            sync_store,
        }
    }

    pub(crate) fn install(&self, system: &mut ConfluxSystem) -> ReferenceResult<()> {
        for provider in &self.providers {
            match provider {
                ReferenceProviderPlan::BinanceSpot { key, endpoint } => {
                    let connection = binance::spot::BinanceSpotRestConnection::new(binance_config(
                        key, endpoint, None,
                    ))
                    .map_err(provider_error)?;
                    ensure(&mut system.binance_spot_rest_connections, key, connection)?;
                }
                ReferenceProviderPlan::BinanceUsdM { key, endpoint } => {
                    let connection = binance::usdm::BinanceUsdMRestConnection::new(binance_config(
                        key, endpoint, None,
                    ))
                    .map_err(provider_error)?;
                    ensure(&mut system.binance_usdm_rest_connections, key, connection)?;
                }
                ReferenceProviderPlan::BinanceCoinM { key, endpoint } => {
                    let connection = binance::coinm::BinanceCoinMRestConnection::new(
                        binance_config(key, endpoint, None),
                    )
                    .map_err(provider_error)?;
                    ensure(&mut system.binance_coinm_rest_connections, key, connection)?;
                }
                ReferenceProviderPlan::BinanceOptions { key, endpoint } => {
                    let connection = binance::options::BinanceOptionsRestConnection::new(
                        binance_config(key, endpoint, None),
                    )
                    .map_err(provider_error)?;
                    ensure(
                        &mut system.binance_options_rest_connections,
                        key,
                        connection,
                    )?;
                }
                ReferenceProviderPlan::BinanceEquity {
                    key,
                    endpoint,
                    credential,
                } => {
                    let connection = binance::advanced::stocks::BinanceStocksRestConnection::new(
                        binance_config(key, endpoint, Some(credential.clone())),
                    )
                    .map_err(provider_error)?;
                    ensure(&mut system.binance_stocks_rest_connections, key, connection)?;
                }
                ReferenceProviderPlan::Okx { key, endpoint, .. } => {
                    let connection =
                        okx::public::OkxPublicRestConnection::new(okx::OkxRestConfig {
                            binding_id: key.clone(),
                            environment: "public".into(),
                            endpoint: endpoint.clone(),
                        })
                        .map_err(provider_error)?;
                    ensure(&mut system.okx_public_rest_connections, key, connection)?;
                }
                ReferenceProviderPlan::Hyperliquid { key, endpoint, .. } => {
                    let connection = hyperliquid::info::HyperliquidInfoRestConnection::new(
                        hyperliquid::HyperliquidRestConfig {
                            binding_id: key.clone(),
                            environment: "public".into(),
                            endpoint: endpoint.clone(),
                        },
                    )
                    .map_err(provider_error)?;
                    ensure(
                        &mut system.hyperliquid_info_rest_connections,
                        key,
                        connection,
                    )?;
                }
                ReferenceProviderPlan::MassiveEquity {
                    key,
                    api_key,
                    endpoint,
                    ..
                } => {
                    let connection =
                        massive::MassiveRestConnection::new(massive::MassiveRestConfig {
                            binding_id: key.clone(),
                            environment: "public".into(),
                            endpoint: endpoint.clone(),
                            api_key: secrecy::SecretString::new(api_key.clone().into()),
                            instrument_query: massive::InstrumentQuery::equities(),
                        })
                        .map_err(provider_error)?;
                    ensure(&mut system.massive_rest_connections, key, connection)?;
                }
                ReferenceProviderPlan::MassiveOptions {
                    api_key,
                    endpoint,
                    underlyings,
                    ..
                } => {
                    for underlying in underlyings {
                        let key = MassiveOptionsCoverageSource::connection_key(underlying)?;
                        let connection =
                            massive::MassiveRestConnection::new(massive::MassiveRestConfig {
                                binding_id: key.clone(),
                                environment: "public".into(),
                                endpoint: endpoint.clone(),
                                api_key: secrecy::SecretString::new(api_key.clone().into()),
                                instrument_query: massive::InstrumentQuery::options(Some(
                                    underlying.clone(),
                                )),
                            })
                            .map_err(provider_error)?;
                        ensure(&mut system.massive_rest_connections, &key, connection)?;
                    }
                }
            }
        }
        Ok(())
    }

    pub(crate) async fn activate(
        self,
        system: &mut ConfluxSystem,
    ) -> ReferenceResult<ConfiguredReferenceSource> {
        let mut sources = Vec::with_capacity(self.providers.len());
        for provider in self.providers {
            let source = match provider {
                ReferenceProviderPlan::BinanceSpot { key, .. } => {
                    ConfiguredProviderSource::BinanceSpot(BinanceSpotSource::from_connection(take(
                        &mut system.binance_spot_rest_connections,
                        &key,
                    )?))
                }
                ReferenceProviderPlan::BinanceUsdM { key, .. } => {
                    ConfiguredProviderSource::BinanceDerivatives(
                        BinanceDerivativesSource::from_usdm(take(
                            &mut system.binance_usdm_rest_connections,
                            &key,
                        )?),
                    )
                }
                ReferenceProviderPlan::BinanceCoinM { key, .. } => {
                    ConfiguredProviderSource::BinanceDerivatives(
                        BinanceDerivativesSource::from_coinm(take(
                            &mut system.binance_coinm_rest_connections,
                            &key,
                        )?),
                    )
                }
                ReferenceProviderPlan::BinanceOptions { key, .. } => {
                    ConfiguredProviderSource::BinanceOptions(BinanceOptionsSource::from_connection(
                        take(&mut system.binance_options_rest_connections, &key)?,
                    ))
                }
                ReferenceProviderPlan::BinanceEquity { key, .. } => {
                    ConfiguredProviderSource::BinanceEquity(BinanceEquitySource::from_connection(
                        take(&mut system.binance_stocks_rest_connections, &key)?,
                    ))
                }
                ReferenceProviderPlan::Okx {
                    key,
                    source_id,
                    product,
                    ..
                } => ConfiguredProviderSource::Okx(OkxSource::from_connection(
                    source_id,
                    product,
                    take(&mut system.okx_public_rest_connections, &key)?,
                )),
                ReferenceProviderPlan::Hyperliquid { key, product, .. } => {
                    ConfiguredProviderSource::Hyperliquid(HyperliquidSource::from_connection(
                        product,
                        take(&mut system.hyperliquid_info_rest_connections, &key)?,
                    ))
                }
                ReferenceProviderPlan::MassiveEquity {
                    key, sync_store, ..
                } => ConfiguredProviderSource::MassiveEquity(
                    MassiveEquitySource::from_connection(
                        take(&mut system.massive_rest_connections, &key)?,
                        sync_store,
                    )
                    .await?,
                ),
                ReferenceProviderPlan::MassiveOptions {
                    api_key,
                    endpoint,
                    sync_store,
                    underlyings,
                } => ConfiguredProviderSource::MassiveOptions(
                    MassiveOptionsCoverageSource::from_connections(
                        api_key,
                        endpoint,
                        sync_store,
                        underlyings
                            .into_iter()
                            .map(|underlying| {
                                let key =
                                    MassiveOptionsCoverageSource::connection_key(&underlying)?;
                                take(&mut system.massive_rest_connections, &key)
                                    .map(|connection| (underlying, connection))
                            })
                            .collect::<ReferenceResult<Vec<_>>>()?,
                    )
                    .await?,
                ),
            };
            sources.push(source);
        }
        let composite =
            CompositeSource::new_with_sync_store(sources, Some(self.sync_store)).await?;
        Ok(ConfiguredReferenceSource::new(
            ParticipantAugmentedSource::wrap(composite, self.participants),
        ))
    }
}

fn binance_config(
    key: &str,
    endpoint: &str,
    credential: Option<binance::BinanceCredential>,
) -> binance::BinanceRestConfig {
    binance::BinanceRestConfig {
        binding_id: key.into(),
        environment: "public".into(),
        endpoint: endpoint.into(),
        credential,
    }
}

fn ensure<K, C>(
    connections: &mut kairos_conflux::ManagedConnections<K, C>,
    key: &K,
    connection: C,
) -> ReferenceResult<()>
where
    K: Clone + std::hash::Hash + Eq,
{
    connections
        .ensure_with(key.clone(), 1, || connection)
        .map(|_| ())
        .map_err(|error| ReferenceError::Provider(error.to_string()))
}

fn take<K, C>(
    connections: &mut kairos_conflux::ManagedConnections<K, C>,
    key: &K,
) -> ReferenceResult<C>
where
    K: std::hash::Hash + Eq + std::fmt::Display,
{
    connections
        .remove(key)
        .map(|managed| managed.into_connection())
        .ok_or_else(|| {
            ReferenceError::Provider(format!("missing managed Reference connection: {key}"))
        })
}

fn provider_error(error: impl ToString) -> ReferenceError {
    ReferenceError::Provider(error.to_string())
}
