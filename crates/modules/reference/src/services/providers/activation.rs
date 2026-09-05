use kairos_conflux::{
    BinanceRestConfig, ConnectionCollections, ConnectionKey, HyperliquidRestConfig,
    MassiveInstrumentQuery, MassiveRestConfig, OkxRestConfig,
};

use super::{
    BinanceDerivativesSource, BinanceEquitySource, BinanceOptionsSource, BinanceReferenceSource,
    BinanceSpotSource, HyperliquidProduct, HyperliquidSource, MassiveEquitySource,
    MassiveOptionsCoverageSource, MassiveReferenceSource, OkxProduct, OkxSource,
    ReferenceCredentialResolver, ReferenceSourceBinding, binance_config, default_endpoint,
    massive_options_underlying_from_scope, provider_error,
};
use crate::domain::{
    ReferenceError, ReferenceResult, ReferenceSourceDefinition, SourceScopeKind, SourceSyncPolicy,
};
use crate::services::sources::ConfiguredProviderSource;
use crate::services::storage::provider_sync_store::SqlxProviderSyncStore;

pub(crate) async fn activate_runtime_source_definition(
    definition: &ReferenceSourceDefinition,
    connections: &mut ConnectionCollections<'_>,
    credentials: &ReferenceCredentialResolver,
    sync_store: Option<SqlxProviderSyncStore>,
) -> ReferenceResult<Option<ConfiguredProviderSource>> {
    let Some(binding) = ReferenceSourceBinding::from_source_id(definition.source_id.as_str())
    else {
        return Ok(None);
    };
    if definition.provider_id.as_str() != binding.provider()
        || definition.sync_policy != binding.sync_policy()
    {
        return Ok(None);
    }
    let scoped_massive_options = is_scoped_massive_options_definition(definition);
    if !scoped_massive_options && definition.scope.kind != SourceScopeKind::Global {
        return Ok(None);
    }
    let key = runtime_source_connection_key(binding, definition)?;
    let environment = credentials.environment(
        definition.connection_id.as_deref(),
        binding.provider(),
        binding.product(),
    )?;
    match binding {
        ReferenceSourceBinding::Massive(MassiveReferenceSource::Options)
            if scoped_massive_options =>
        {
            let Some(api_key) =
                credentials.massive(definition.connection_id.as_deref(), "options")?
            else {
                return Ok(None);
            };
            let endpoint = credentials.endpoint(
                definition.connection_id.as_deref(),
                "massive",
                "options",
                default_endpoint("massive"),
            )?;
            let Some(sync_store) = sync_store else {
                return Err(ReferenceError::Persistence(
                    "dynamic Massive options source activation requires provider sync store".into(),
                ));
            };
            let underlying = massive_options_underlying_from_scope(definition.scope.clone())?;
            connections
                .massive_rest
                .create(
                    key.clone(),
                    MassiveRestConfig {
                        environment: environment.clone(),
                        endpoint: endpoint.clone(),
                        api_key: secrecy::SecretString::new(api_key.clone().into()),
                        instrument_query: MassiveInstrumentQuery::options(Some(underlying.clone())),
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::MassiveOptions(
                MassiveOptionsCoverageSource::from_keys(
                    api_key,
                    endpoint,
                    environment,
                    sync_store,
                    vec![(underlying, key)],
                )
                .await?,
            )))
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::Spot) => {
            let endpoint = credentials.endpoint(
                definition.connection_id.as_deref(),
                "binance",
                "spot",
                default_endpoint("binance-spot"),
            )?;
            connections
                .binance_spot_rest
                .create(
                    key.clone(),
                    BinanceRestConfig {
                        environment,
                        ..binance_config(&endpoint, None)
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::BinanceSpot(
                BinanceSpotSource::from_key(key),
            )))
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::UsdMFutures) => {
            let endpoint = credentials.endpoint(
                definition.connection_id.as_deref(),
                "binance",
                "usd-m-futures",
                default_endpoint("binance-usdm-futures"),
            )?;
            connections
                .binance_usdm_rest
                .create(
                    key.clone(),
                    BinanceRestConfig {
                        environment,
                        ..binance_config(&endpoint, None)
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::BinanceDerivatives(
                BinanceDerivativesSource::from_usdm_key(key),
            )))
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::CoinMFutures) => {
            let endpoint = credentials.endpoint(
                definition.connection_id.as_deref(),
                "binance",
                "coin-m-futures",
                default_endpoint("binance-coinm-futures"),
            )?;
            connections
                .binance_coinm_rest
                .create(
                    key.clone(),
                    BinanceRestConfig {
                        environment,
                        ..binance_config(&endpoint, None)
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::BinanceDerivatives(
                BinanceDerivativesSource::from_coinm_key(key),
            )))
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::Options) => {
            let endpoint = credentials.endpoint(
                definition.connection_id.as_deref(),
                "binance",
                "options",
                default_endpoint("binance-options"),
            )?;
            connections
                .binance_options_rest
                .create(
                    key.clone(),
                    BinanceRestConfig {
                        environment,
                        ..binance_config(&endpoint, None)
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::BinanceOptions(
                BinanceOptionsSource::from_key(key),
            )))
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::Equity) => {
            let Some(credential) =
                credentials.binance(definition.connection_id.as_deref(), "equity")?
            else {
                return Ok(None);
            };
            let endpoint = credentials.endpoint(
                definition.connection_id.as_deref(),
                "binance",
                "equity",
                default_endpoint("binance-equity"),
            )?;
            connections
                .binance_stocks_rest
                .create(
                    key.clone(),
                    BinanceRestConfig {
                        environment,
                        ..binance_config(&endpoint, Some(credential))
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::BinanceEquity(
                BinanceEquitySource::from_key(key),
            )))
        },
        ReferenceSourceBinding::Massive(MassiveReferenceSource::Equity) => {
            let Some(api_key) =
                credentials.massive(definition.connection_id.as_deref(), "equity")?
            else {
                return Ok(None);
            };
            let endpoint = credentials.endpoint(
                definition.connection_id.as_deref(),
                "massive",
                "equity",
                default_endpoint("massive"),
            )?;
            let Some(sync_store) = sync_store else {
                return Err(ReferenceError::Persistence(
                    "dynamic Massive equity source activation requires provider sync store".into(),
                ));
            };
            connections
                .massive_rest
                .create(
                    key.clone(),
                    MassiveRestConfig {
                        environment,
                        endpoint,
                        api_key: secrecy::SecretString::new(api_key.into()),
                        instrument_query: MassiveInstrumentQuery::equities(),
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::MassiveEquity(
                MassiveEquitySource::from_key(key, sync_store).await?,
            )))
        },
        ReferenceSourceBinding::Okx(product) => {
            let endpoint = credentials.endpoint(
                definition.connection_id.as_deref(),
                "okx",
                product.profile_product(),
                default_endpoint(binding.source_id()),
            )?;
            connections
                .okx_public_rest
                .create(
                    key.clone(),
                    OkxRestConfig {
                        environment,
                        endpoint,
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::Okx(OkxSource::from_key(
                binding.source_id(),
                product,
                key,
            ))))
        },
        ReferenceSourceBinding::Hyperliquid(HyperliquidProduct::Perpetual) => {
            let endpoint = credentials.endpoint(
                definition.connection_id.as_deref(),
                "hyperliquid",
                "perpetual",
                default_endpoint("hyperliquid"),
            )?;
            connections
                .hyperliquid_info_rest
                .create(
                    key.clone(),
                    HyperliquidRestConfig {
                        environment,
                        endpoint,
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::Hyperliquid(
                HyperliquidSource::from_key(HyperliquidProduct::Perpetual, key),
            )))
        },
        ReferenceSourceBinding::Hyperliquid(HyperliquidProduct::Spot) => {
            let endpoint = credentials.endpoint(
                definition.connection_id.as_deref(),
                "hyperliquid",
                "spot",
                default_endpoint("hyperliquid"),
            )?;
            connections
                .hyperliquid_info_rest
                .create(
                    key.clone(),
                    HyperliquidRestConfig {
                        environment,
                        endpoint,
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::Hyperliquid(
                HyperliquidSource::from_key(HyperliquidProduct::Spot, key),
            )))
        },
        ReferenceSourceBinding::Massive(MassiveReferenceSource::Options) => Ok(None),
    }
}

pub(crate) fn deactivate_runtime_source_definition(
    definition: &ReferenceSourceDefinition,
    connections: &mut ConnectionCollections<'_>,
) -> ReferenceResult<bool> {
    let Some(binding) = ReferenceSourceBinding::from_source_id(definition.source_id.as_str())
    else {
        return Ok(false);
    };
    let key = runtime_source_connection_key(binding, definition)?;
    match binding {
        ReferenceSourceBinding::Binance(BinanceReferenceSource::Spot) => {
            connections.binance_spot_rest.remove(&key).map(drop)
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::UsdMFutures) => {
            connections.binance_usdm_rest.remove(&key).map(drop)
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::CoinMFutures) => {
            connections.binance_coinm_rest.remove(&key).map(drop)
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::Options) => {
            connections.binance_options_rest.remove(&key).map(drop)
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::Equity) => {
            connections.binance_stocks_rest.remove(&key).map(drop)
        },
        ReferenceSourceBinding::Massive(_) => connections.massive_rest.remove(&key).map(drop),
        ReferenceSourceBinding::Okx(_) => connections.okx_public_rest.remove(&key).map(drop),
        ReferenceSourceBinding::Hyperliquid(_) => {
            connections.hyperliquid_info_rest.remove(&key).map(drop)
        },
    }
    .map_err(provider_error)?;
    Ok(true)
}

fn runtime_source_connection_key(
    binding: ReferenceSourceBinding,
    definition: &ReferenceSourceDefinition,
) -> ReferenceResult<ConnectionKey> {
    let key = match binding {
        ReferenceSourceBinding::Binance(BinanceReferenceSource::Spot) => "reference-binance-spot",
        ReferenceSourceBinding::Binance(BinanceReferenceSource::UsdMFutures) => {
            "reference-binance-usdm"
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::CoinMFutures) => {
            "reference-binance-coinm"
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::Options) => {
            "reference-binance-options"
        },
        ReferenceSourceBinding::Binance(BinanceReferenceSource::Equity) => {
            "reference-binance-stocks"
        },
        ReferenceSourceBinding::Massive(MassiveReferenceSource::Equity) => {
            "reference-massive-equity"
        },
        ReferenceSourceBinding::Massive(MassiveReferenceSource::Options) => {
            let underlying = massive_options_underlying_from_scope(definition.scope.clone())?;
            return ConnectionKey::new(MassiveOptionsCoverageSource::connection_key(&underlying)?)
                .map_err(provider_error);
        },
        ReferenceSourceBinding::Okx(OkxProduct::Spot) => "reference-okx-spot",
        ReferenceSourceBinding::Okx(OkxProduct::Margin) => "reference-okx-margin",
        ReferenceSourceBinding::Okx(OkxProduct::Swap) => "reference-okx-swap",
        ReferenceSourceBinding::Okx(OkxProduct::Futures) => "reference-okx-futures",
        ReferenceSourceBinding::Okx(OkxProduct::Option) => "reference-okx-options",
        ReferenceSourceBinding::Hyperliquid(HyperliquidProduct::Spot) => {
            "reference-hyperliquid-spot"
        },
        ReferenceSourceBinding::Hyperliquid(HyperliquidProduct::Perpetual) => {
            "reference-hyperliquid-perpetual"
        },
    };
    ConnectionKey::new(key).map_err(provider_error)
}

pub(crate) fn is_scoped_massive_options_definition(definition: &ReferenceSourceDefinition) -> bool {
    ReferenceSourceBinding::from_source_id(definition.source_id.as_str())
        == Some(ReferenceSourceBinding::Massive(
            MassiveReferenceSource::Options,
        ))
        && definition.provider_id.as_str()
            == ReferenceSourceBinding::Massive(MassiveReferenceSource::Options).provider()
        && definition.sync_policy == SourceSyncPolicy::ScopedSnapshot
        && matches!(
            definition.scope.kind,
            SourceScopeKind::UnderlyingInstrument | SourceScopeKind::Coverage
        )
}
