use kairos_conflux::{ConnectionCollections, ConnectionKey, HyperliquidRestConfig, OkxRestConfig};

use super::{
    BinanceDerivativesSource, BinanceOptionsSource, BinanceSpotSource, HyperliquidProduct,
    HyperliquidSource, OkxProduct, OkxSource, ProviderFanInSource, ReferenceCredentialResolver,
    binance_config, provider_error,
};
use crate::domain::ReferenceResult;
use crate::services::sources::{
    ConfiguredProviderSource, ConfiguredReferenceSource, ReferenceSource,
};
use crate::services::storage::provider_sync_store::SqlxProviderSyncStore;

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
}

pub(crate) struct ReferenceSourcePlan {
    providers: Vec<ReferenceProviderPlan>,
    sync_store: SqlxProviderSyncStore,
    credential_resolver: ReferenceCredentialResolver,
}

impl ReferenceSourcePlan {
    pub(crate) fn new_with_credential_resolver(
        providers: Vec<ReferenceProviderPlan>,
        sync_store: SqlxProviderSyncStore,
        credential_resolver: ReferenceCredentialResolver,
    ) -> Self {
        Self {
            providers,
            sync_store,
            credential_resolver,
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
                },
                ReferenceProviderPlan::BinanceUsdM { key, endpoint } => {
                    connections
                        .binance_usdm_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            binance_config(endpoint, None),
                        )
                        .map_err(provider_error)?;
                },
                ReferenceProviderPlan::BinanceCoinM { key, endpoint } => {
                    connections
                        .binance_coinm_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            binance_config(endpoint, None),
                        )
                        .map_err(provider_error)?;
                },
                ReferenceProviderPlan::BinanceOptions { key, endpoint } => {
                    connections
                        .binance_options_rest
                        .create(
                            ConnectionKey::new(key.clone()).map_err(provider_error)?,
                            binance_config(endpoint, None),
                        )
                        .map_err(provider_error)?;
                },
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
                },
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
                },
            }
        }
        Ok(())
    }

    pub(crate) async fn activate(
        self,
        connections: &mut ConnectionCollections<'_>,
    ) -> ReferenceResult<ConfiguredReferenceSource> {
        let mut registry_store = self.sync_store.clone();
        let persisted_definitions = registry_store.source_definitions().await?;
        let mut sources = Vec::with_capacity(self.providers.len());
        for provider in self.providers {
            let source = match provider {
                ReferenceProviderPlan::BinanceSpot { key, .. } => {
                    ConfiguredProviderSource::BinanceSpot(BinanceSpotSource::from_key(
                        ConnectionKey::new(key).map_err(provider_error)?,
                    ))
                },
                ReferenceProviderPlan::BinanceUsdM { key, .. } => {
                    ConfiguredProviderSource::BinanceDerivatives(
                        BinanceDerivativesSource::from_usdm_key(
                            ConnectionKey::new(key).map_err(provider_error)?,
                        ),
                    )
                },
                ReferenceProviderPlan::BinanceCoinM { key, .. } => {
                    ConfiguredProviderSource::BinanceDerivatives(
                        BinanceDerivativesSource::from_coinm_key(
                            ConnectionKey::new(key).map_err(provider_error)?,
                        ),
                    )
                },
                ReferenceProviderPlan::BinanceOptions { key, .. } => {
                    ConfiguredProviderSource::BinanceOptions(BinanceOptionsSource::from_key(
                        ConnectionKey::new(key).map_err(provider_error)?,
                    ))
                },
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
                },
            };
            sources.push(source);
        }
        let mut fan_in = ProviderFanInSource::new_with_sync_store_and_credentials(
            sources,
            Some(self.sync_store),
            self.credential_resolver,
        )
        .await?;
        for definition in persisted_definitions {
            if definition.desired_state != crate::domain::SourceDesiredState::Enabled {
                continue;
            }
            // A source-specific blocker must not prevent unrelated public
            // sources or the Reference process itself from becoming healthy.
            let _ = fan_in
                .upsert_source_definition_with_connections(definition, connections)
                .await;
        }
        Ok(ConfiguredReferenceSource::new(fan_in))
    }
}
