use kairos_conflux::{
    BinanceCredential, BinanceRestConfig, ConnectionCollections, ConnectionKey,
    HyperliquidRestConfig, MassiveInstrumentQuery, MassiveRestConfig, OkxRestConfig,
};

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
        credential: BinanceCredential,
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

    pub(crate) fn install(
        &self,
        connections: &mut ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        for provider in &self.providers {
            match provider {
                ReferenceProviderPlan::BinanceSpot { key, endpoint } => {
                    connections
                        .binance_spot_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            binance_config(endpoint, None),
                        )
                        .map_err(provider_error)?;
                }
                ReferenceProviderPlan::BinanceUsdM { key, endpoint } => {
                    connections
                        .binance_usdm_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            binance_config(endpoint, None),
                        )
                        .map_err(provider_error)?;
                }
                ReferenceProviderPlan::BinanceCoinM { key, endpoint } => {
                    connections
                        .binance_coinm_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            binance_config(endpoint, None),
                        )
                        .map_err(provider_error)?;
                }
                ReferenceProviderPlan::BinanceOptions { key, endpoint } => {
                    connections
                        .binance_options_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            binance_config(endpoint, None),
                        )
                        .map_err(provider_error)?;
                }
                ReferenceProviderPlan::BinanceEquity {
                    key,
                    endpoint,
                    credential,
                } => {
                    connections
                        .binance_stocks_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            binance_config(endpoint, Some(credential.clone())),
                        )
                        .map_err(provider_error)?;
                }
                ReferenceProviderPlan::Okx { key, endpoint, .. } => {
                    connections
                        .okx_public_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            OkxRestConfig {
                                environment: "public".into(),
                                endpoint: endpoint.clone(),
                            },
                        )
                        .map_err(provider_error)?;
                }
                ReferenceProviderPlan::Hyperliquid { key, endpoint, .. } => {
                    connections
                        .hyperliquid_info_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            HyperliquidRestConfig {
                                environment: "public".into(),
                                endpoint: endpoint.clone(),
                            },
                        )
                        .map_err(provider_error)?;
                }
                ReferenceProviderPlan::MassiveEquity {
                    key,
                    api_key,
                    endpoint,
                    ..
                } => {
                    connections
                        .massive_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            MassiveRestConfig {
                                environment: "public".into(),
                                endpoint: endpoint.clone(),
                                api_key: secrecy::SecretString::new(api_key.clone().into()),
                                instrument_query: MassiveInstrumentQuery::equities(),
                            },
                        )
                        .map_err(provider_error)?;
                }
                ReferenceProviderPlan::MassiveOptions {
                    api_key,
                    endpoint,
                    underlyings,
                    ..
                } => {
                    for underlying in underlyings {
                        let key = MassiveOptionsCoverageSource::connection_key(underlying)?;
                        connections
                            .massive_rest
                            .create(
                                ConnectionKey::new(key.clone()).map_err(provider_error)?,
                                MassiveRestConfig {
                                    environment: "public".into(),
                                    endpoint: endpoint.clone(),
                                    api_key: secrecy::SecretString::new(api_key.clone().into()),
                                    instrument_query: MassiveInstrumentQuery::options(Some(
                                        underlying.clone(),
                                    )),
                                },
                            )
                            .map_err(provider_error)?;
                    }
                }
            }
        }
        Ok(())
    }

    pub(crate) async fn activate(
        self,
        _connections: &mut ConnectionCollections<'_>,
    ) -> ReferenceResult<ConfiguredReferenceSource> {
        let mut sources = Vec::with_capacity(self.providers.len());
        for provider in self.providers {
            let source = match provider {
                ReferenceProviderPlan::BinanceSpot { key, .. } => {
                    ConfiguredProviderSource::BinanceSpot(BinanceSpotSource::from_key(
                        ConnectionKey::new(key).map_err(provider_error)?,
                    ))
                }
                ReferenceProviderPlan::BinanceUsdM { key, .. } => {
                    ConfiguredProviderSource::BinanceDerivatives(
                        BinanceDerivativesSource::from_usdm_key(
                            ConnectionKey::new(key).map_err(provider_error)?,
                        ),
                    )
                }
                ReferenceProviderPlan::BinanceCoinM { key, .. } => {
                    ConfiguredProviderSource::BinanceDerivatives(
                        BinanceDerivativesSource::from_coinm_key(
                            ConnectionKey::new(key).map_err(provider_error)?,
                        ),
                    )
                }
                ReferenceProviderPlan::BinanceOptions { key, .. } => {
                    ConfiguredProviderSource::BinanceOptions(BinanceOptionsSource::from_key(
                        ConnectionKey::new(key).map_err(provider_error)?,
                    ))
                }
                ReferenceProviderPlan::BinanceEquity { key, .. } => {
                    ConfiguredProviderSource::BinanceEquity(BinanceEquitySource::from_key(
                        ConnectionKey::new(key).map_err(provider_error)?,
                    ))
                }
                ReferenceProviderPlan::Okx {
                    key,
                    source_id,
                    product,
                    ..
                } => ConfiguredProviderSource::Okx(OkxSource::from_key(
                    source_id,
                    product,
                    ConnectionKey::new(key).map_err(provider_error)?,
                )),
                ReferenceProviderPlan::Hyperliquid { key, product, .. } => {
                    ConfiguredProviderSource::Hyperliquid(HyperliquidSource::from_key(
                        product,
                        ConnectionKey::new(key).map_err(provider_error)?,
                    ))
                }
                ReferenceProviderPlan::MassiveEquity {
                    key, sync_store, ..
                } => ConfiguredProviderSource::MassiveEquity(
                    MassiveEquitySource::from_key(
                        ConnectionKey::new(key).map_err(provider_error)?,
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
                    MassiveOptionsCoverageSource::from_keys(
                        api_key,
                        endpoint,
                        sync_store,
                        underlyings
                            .into_iter()
                            .map(|underlying| {
                                let key =
                                    MassiveOptionsCoverageSource::connection_key(&underlying)?;
                                ConnectionKey::new(key)
                                    .map(|connection_key| (underlying, connection_key))
                                    .map_err(provider_error)
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

fn binance_config(endpoint: &str, credential: Option<BinanceCredential>) -> BinanceRestConfig {
    BinanceRestConfig {
        environment: "public".into(),
        endpoint: endpoint.into(),
        credential,
    }
}

fn provider_error(error: impl ToString) -> ReferenceError {
    ReferenceError::Provider(error.to_string())
}
