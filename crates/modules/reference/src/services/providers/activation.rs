use kairos_conflux::{
    ConnectionCollections, ConnectionKey, HyperliquidRestConfig, MassiveInstrumentQuery,
    MassiveRestConfig, OkxRestConfig,
};

use super::{
    BinanceDerivativesSource, BinanceEquitySource, BinanceOptionsSource, BinanceSpotSource,
    HyperliquidProduct, HyperliquidSource, MassiveEquitySource, MassiveOptionsCoverageSource,
    OkxProduct, OkxSource, ReferenceCredentialResolver, binance_config, default_endpoint,
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
    let scoped_massive_options = is_scoped_massive_options_definition(definition);
    if !scoped_massive_options
        && (definition.scope.kind != SourceScopeKind::Global
            || definition.sync_policy != SourceSyncPolicy::FullSnapshot)
    {
        return Ok(None);
    }
    let source_id = definition.source_id.as_str();
    let product = definition.provider_product.as_deref();
    let Some(key) = runtime_source_connection_key(definition)? else {
        return Ok(None);
    };
    let credentialed_binance_equity = matches!(
        (definition.provider_id.as_str(), product, source_id),
        (
            "binance",
            Some("equity"),
            "binance-equity" | "binance-stocks"
        )
    );
    let credentialed_massive_equity = matches!(
        (definition.provider_id.as_str(), product, source_id),
        ("massive", Some("equity"), "massive-equity")
    );
    if definition.credential_binding.is_some()
        && !credentialed_binance_equity
        && !credentialed_massive_equity
        && !scoped_massive_options
    {
        return Ok(None);
    }
    match (definition.provider_id.as_str(), product, source_id) {
        ("massive", Some("options"), "massive-options") if scoped_massive_options => {
            let Some(api_key) = credentials.massive(definition.credential_binding.as_deref())?
            else {
                return Ok(None);
            };
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
                        environment: "public".into(),
                        endpoint: default_endpoint("massive").into(),
                        api_key: secrecy::SecretString::new(api_key.clone().into()),
                        instrument_query: MassiveInstrumentQuery::options(Some(underlying.clone())),
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::MassiveOptions(
                MassiveOptionsCoverageSource::from_keys(
                    api_key,
                    default_endpoint("massive"),
                    sync_store,
                    vec![(underlying, key)],
                )
                .await?,
            )))
        },
        ("binance", Some("spot"), "binance-spot") => {
            connections
                .binance_spot_rest
                .create(
                    key.clone(),
                    binance_config(default_endpoint("binance-spot"), None),
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::BinanceSpot(
                BinanceSpotSource::from_key(key),
            )))
        },
        ("binance", Some("usdm"), "binance-usdm-futures") => {
            connections
                .binance_usdm_rest
                .create(
                    key.clone(),
                    binance_config(default_endpoint("binance-usdm-futures"), None),
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::BinanceDerivatives(
                BinanceDerivativesSource::from_usdm_key(key),
            )))
        },
        ("binance", Some("coinm"), "binance-coinm-futures") => {
            connections
                .binance_coinm_rest
                .create(
                    key.clone(),
                    binance_config(default_endpoint("binance-coinm-futures"), None),
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::BinanceDerivatives(
                BinanceDerivativesSource::from_coinm_key(key),
            )))
        },
        ("binance", Some("options"), "binance-options") => {
            connections
                .binance_options_rest
                .create(
                    key.clone(),
                    binance_config(default_endpoint("binance-options"), None),
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::BinanceOptions(
                BinanceOptionsSource::from_key(key),
            )))
        },
        ("binance", Some("equity"), "binance-equity" | "binance-stocks") => {
            let Some(credential) = credentials.binance(definition.credential_binding.as_deref())?
            else {
                return Ok(None);
            };
            connections
                .binance_stocks_rest
                .create(
                    key.clone(),
                    binance_config(default_endpoint("binance-equity"), Some(credential)),
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::BinanceEquity(
                BinanceEquitySource::from_key(key),
            )))
        },
        ("massive", Some("equity"), "massive-equity") => {
            let Some(api_key) = credentials.massive(definition.credential_binding.as_deref())?
            else {
                return Ok(None);
            };
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
                        environment: "public".into(),
                        endpoint: default_endpoint("massive").into(),
                        api_key: secrecy::SecretString::new(api_key.into()),
                        instrument_query: MassiveInstrumentQuery::equities(),
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::MassiveEquity(
                MassiveEquitySource::from_key(key, sync_store).await?,
            )))
        },
        ("okx", Some(product), source_id)
            if matches!(product, "spot" | "margin" | "swap" | "futures" | "options") =>
        {
            let product = match product {
                "spot" => OkxProduct::Spot,
                "margin" => OkxProduct::Margin,
                "swap" => OkxProduct::Swap,
                "futures" => OkxProduct::Futures,
                "options" => OkxProduct::Option,
                _ => unreachable!("product was matched"),
            };
            connections
                .okx_public_rest
                .create(
                    key.clone(),
                    OkxRestConfig {
                        environment: "public".into(),
                        endpoint: default_endpoint(source_id).into(),
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::Okx(OkxSource::from_key(
                source_id, product, key,
            ))))
        },
        ("hyperliquid", Some("perpetual"), "hyperliquid-perpetual") => {
            connections
                .hyperliquid_info_rest
                .create(
                    key.clone(),
                    HyperliquidRestConfig {
                        environment: "public".into(),
                        endpoint: default_endpoint("hyperliquid").into(),
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::Hyperliquid(
                HyperliquidSource::from_key(HyperliquidProduct::Perpetual, key),
            )))
        },
        ("hyperliquid", Some("spot"), "hyperliquid-spot") => {
            connections
                .hyperliquid_info_rest
                .create(
                    key.clone(),
                    HyperliquidRestConfig {
                        environment: "public".into(),
                        endpoint: default_endpoint("hyperliquid").into(),
                    },
                )
                .map_err(provider_error)?;
            Ok(Some(ConfiguredProviderSource::Hyperliquid(
                HyperliquidSource::from_key(HyperliquidProduct::Spot, key),
            )))
        },
        _ => Ok(None),
    }
}

pub(crate) fn deactivate_runtime_source_definition(
    definition: &ReferenceSourceDefinition,
    connections: &mut ConnectionCollections<'_>,
) -> ReferenceResult<bool> {
    let Some(key) = runtime_source_connection_key(definition)? else {
        return Ok(false);
    };
    match definition.source_id.as_str() {
        "binance-spot" => connections.binance_spot_rest.remove(&key).map(drop),
        "binance-usdm-futures" => connections.binance_usdm_rest.remove(&key).map(drop),
        "binance-coinm-futures" => connections.binance_coinm_rest.remove(&key).map(drop),
        "binance-options" => connections.binance_options_rest.remove(&key).map(drop),
        "binance-equity" | "binance-stocks" => {
            connections.binance_stocks_rest.remove(&key).map(drop)
        },
        "massive-equity" => connections.massive_rest.remove(&key).map(drop),
        "massive-options" => connections.massive_rest.remove(&key).map(drop),
        "okx-spot" | "okx-margin" | "okx-swap" | "okx-futures" | "okx-options" => {
            connections.okx_public_rest.remove(&key).map(drop)
        },
        "hyperliquid-spot" | "hyperliquid-perpetual" => {
            connections.hyperliquid_info_rest.remove(&key).map(drop)
        },
        _ => return Ok(false),
    }
    .map_err(provider_error)?;
    Ok(true)
}

fn runtime_source_connection_key(
    definition: &ReferenceSourceDefinition,
) -> ReferenceResult<Option<ConnectionKey>> {
    let key = match definition.source_id.as_str() {
        "binance-spot" => "reference-binance-spot",
        "binance-usdm-futures" => "reference-binance-usdm",
        "binance-coinm-futures" => "reference-binance-coinm",
        "binance-options" => "reference-binance-options",
        "binance-equity" | "binance-stocks" => "reference-binance-stocks",
        "massive-equity" => "reference-massive-equity",
        "massive-options" => {
            let underlying = massive_options_underlying_from_scope(definition.scope.clone())?;
            return ConnectionKey::new(MassiveOptionsCoverageSource::connection_key(&underlying)?)
                .map(Some)
                .map_err(provider_error);
        },
        "okx-spot" => "reference-okx-spot",
        "okx-margin" => "reference-okx-margin",
        "okx-swap" => "reference-okx-swap",
        "okx-futures" => "reference-okx-futures",
        "okx-options" => "reference-okx-options",
        "hyperliquid-spot" => "reference-hyperliquid-spot",
        "hyperliquid-perpetual" => "reference-hyperliquid-perpetual",
        _ => return Ok(None),
    };
    ConnectionKey::new(key).map(Some).map_err(provider_error)
}

pub(crate) fn is_scoped_massive_options_definition(definition: &ReferenceSourceDefinition) -> bool {
    definition.source_id.as_str() == "massive-options"
        && definition.provider_id.as_str() == "massive"
        && definition.provider_product.as_deref() == Some("options")
        && definition.sync_policy == SourceSyncPolicy::ScopedSnapshot
        && matches!(
            definition.scope.kind,
            SourceScopeKind::UnderlyingInstrument | SourceScopeKind::Coverage
        )
}
