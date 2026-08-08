//! Composition shared by the one-shot CLI and the long-running server.

use std::path::Path;

use crate::application::protocol::ReferenceSource;
use crate::domain::ReferenceResult;
use crate::services::providers::{
    BinanceEquitySource, BinanceOptionsSource, BinanceSpotSource, CompositeSource,
    HyperliquidSource, MassiveEquitySource, MassiveSource, PublicSource,
};
use crate::services::storage::SqliteCatalogStore;
use crate::ReferenceApplication;

use crate::services::publication::{AeronSnapshotWriter, MmapReferenceSnapshotWriter};
use kairos_integration::credentials::load_workspace_credential;
use kairos_integration::domain::{AssetType, IntegrationRoute, ProductFamily};
use kairos_integration::Integration;
use kairos_protocol::InstanceIdentity;

#[derive(Clone, Debug)]
pub struct ReferenceCompositionConfig {
    pub workspace: Option<std::path::PathBuf>,
    pub provider: String,
    pub endpoint: String,
    pub database: std::path::PathBuf,
    pub api_key: String,
    pub binance_api_key: String,
    pub secret: String,
    pub aeron_dir: Option<String>,
    pub channel: String,
    pub catalog_stream: i32,
    pub markets_stream: i32,
    pub lifecycle_stream: i32,
    pub changes_stream: i32,
}

/// Build the same business application for both process modes.
///
/// Read-only CLI commands use `publish = false`, while refresh/publish and the
/// server use the real Aeron publisher. The provider and store are always the
/// production implementations; the disabled publisher is only for local
/// catalog inspection where no media driver is required.
pub struct ReferenceComposition {
    pub application: ReferenceApplication,
    pub snapshot_writer: Option<ReferenceSnapshotWriter>,
}

pub struct ReferenceSnapshotWriter {
    inner: AeronSnapshotWriter,
}

pub struct ReferenceMmapSnapshotWriter {
    inner: MmapReferenceSnapshotWriter,
}

impl ReferenceMmapSnapshotWriter {
    pub fn create(
        catalog_path: impl AsRef<Path>,
        markets_path: impl AsRef<Path>,
        lifecycle_path: impl AsRef<Path>,
        slot_size: usize,
        identity: InstanceIdentity,
    ) -> ReferenceResult<Self> {
        Ok(Self {
            inner: MmapReferenceSnapshotWriter::create(
                catalog_path,
                markets_path,
                lifecycle_path,
                slot_size,
                "reference-actor",
                "reference.lifecycle",
                identity,
            )?,
        })
    }

    pub fn publish(&mut self, catalog: &crate::domain::ReferenceCatalog) -> ReferenceResult<()> {
        self.inner.publish(catalog)
    }
}

/// Canonical provider endpoint defaults shared by the one-shot CLI and the
/// long-running Reference server.
pub fn default_endpoint(provider: &str) -> &'static str {
    match provider {
        "hyperliquid" => "https://api.hyperliquid.xyz/info",
        "binance-options" | "binance-options-rest" => {
            "https://eapi.binance.com/eapi/v1/exchangeInfo"
        }
        "binance-usdm-futures" | "binance-usdm-futures-rest" => {
            "https://fapi.binance.com/fapi/v1/exchangeInfo"
        }
        "binance-coinm-futures" | "binance-coinm-futures-rest" => {
            "https://dapi.binance.com/dapi/v1/exchangeInfo"
        }
        "okx-spot" | "okx-equity" | "okx-swap" | "okx-futures" | "okx-options"
        | "okx-spot-rest" | "okx-swap-rest" | "okx-futures-rest" | "okx-options-rest" => {
            "https://www.okx.com"
        }
        "massive"
        | "massive-equity"
        | "massive-equity-websocket"
        | "massive-options"
        | "massive-options-websocket" => "http://api.massiveprivateserver.site",
        _ => "https://api.binance.com/api/v3/exchangeInfo",
    }
}

/// Return the configured Massive REST endpoint, falling back to the bundled
/// private proxy. The CLI `--endpoint` remains the highest-precedence option.
pub fn massive_rest_endpoint(workspace: Option<&Path>) -> String {
    let configured = workspace
        .and_then(|root| kairos_workspace::workspace::Workspace::open(root).ok())
        .and_then(|workspace| workspace.market_config().massive.rest_base_url.clone())
        .filter(|value| !value.trim().is_empty());
    configured.unwrap_or_else(|| default_endpoint("massive").to_owned())
}

fn massive_option_underlying(workspace: Option<&Path>) -> Option<String> {
    workspace
        .and_then(|root| kairos_workspace::workspace::Workspace::open(root).ok())
        .and_then(|workspace| workspace.market_config().massive.option_underlying.clone())
        .filter(|value| !value.trim().is_empty())
}

/// Build the normal Workspace Reference catalog.
///
/// Reference owns the source selection for the global catalog. Public Binance
/// products are built in; credentialed providers are enabled by their
/// Workspace credential and can be explicitly controlled through the
/// `[reference.providers.*]` tables.
fn build_default_source(
    config: &ReferenceCompositionConfig,
) -> ReferenceResult<Box<dyn ReferenceSource>> {
    let manifest = config
        .workspace
        .as_ref()
        .and_then(|root| std::fs::read_to_string(root.join("kairos.toml")).ok())
        .or_else(|| {
            config
                .workspace
                .as_ref()
                .and_then(|root| std::fs::read_to_string(root.join("workspace.toml")).ok())
        })
        .map(|text| toml::from_str::<toml::Value>(&text))
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?;

    let mut sources: Vec<Box<dyn ReferenceSource>> = vec![
        Box::new(BinanceSpotSource::new(default_endpoint("binance-spot"))?),
        Box::new(public_source(
            "binance-usdm-futures",
            ProductFamily::UsdMFutures,
            Some(AssetType::Crypto),
            default_endpoint("binance-usdm-futures"),
        )?),
        Box::new(public_source(
            "binance-coinm-futures",
            ProductFamily::CoinMFutures,
            Some(AssetType::Crypto),
            default_endpoint("binance-coinm-futures"),
        )?),
        Box::new(BinanceOptionsSource::new(default_endpoint(
            "binance-options",
        ))?),
    ];

    let credentials_root = config
        .workspace
        .as_ref()
        .map(|root| root.join("credentials"));
    let reference = manifest
        .as_ref()
        .and_then(|value| value.get("reference"))
        .and_then(toml::Value::as_table);

    let massive_credential = credentials_root.as_deref().and_then(|root| {
        load_workspace_credential(
            root,
            "massive",
            reference_provider_credential(reference, "massive"),
        )
        .ok()
        .flatten()
    });
    let massive_enabled = reference_provider_enabled(reference, "massive");
    let massive_ready = massive_credential
        .as_ref()
        .is_some_and(|credential| !credential.api_key.trim().is_empty());
    if !reference_provider_disabled(reference, "massive") && (massive_enabled || massive_ready) {
        let credential = massive_credential.ok_or_else(|| {
            crate::domain::ReferenceError::Provider(
                "Reference Massive source is enabled but its credential is missing".into(),
            )
        })?;
        if credential.api_key.trim().is_empty() {
            return Err(crate::domain::ReferenceError::Provider(
                "Reference Massive source is enabled but its API key is missing".into(),
            ));
        }
        sources.push(Box::new(MassiveEquitySource::new(
            credential.api_key.clone(),
            massive_rest_endpoint(config.workspace.as_deref()),
        )?));
        sources.push(Box::new(MassiveSource::new_with_underlying(
            credential.api_key,
            massive_rest_endpoint(config.workspace.as_deref()),
            massive_option_underlying(config.workspace.as_deref()),
        )?));
    }

    if reference_product_enabled(reference, "binance", "equity") {
        let credential_id = reference_product_credential(reference, "binance", "equity");
        let credential = credentials_root
            .as_deref()
            .and_then(|root| {
                load_workspace_credential(root, "binance", credential_id)
                    .ok()
                    .flatten()
            })
            .ok_or_else(|| {
                crate::domain::ReferenceError::Provider(
                    "Reference Binance equity source is enabled but its credential is missing"
                        .into(),
                )
            })?;
        sources.push(Box::new(BinanceEquitySource::new(
            credential.api_key,
            credential.secret,
        )?));
    }

    Ok(Box::new(CompositeSource::new(sources)?))
}

fn reference_provider_enabled(
    reference: Option<&toml::map::Map<String, toml::Value>>,
    provider: &str,
) -> bool {
    let Some(table) = reference
        .and_then(|value| value.get("providers"))
        .and_then(toml::Value::as_table)
        .and_then(|providers| providers.get(provider))
        .and_then(toml::Value::as_table)
    else {
        return false;
    };
    table
        .get("enabled")
        .and_then(toml::Value::as_bool)
        .unwrap_or(true)
}

fn reference_provider_disabled(
    reference: Option<&toml::map::Map<String, toml::Value>>,
    provider: &str,
) -> bool {
    reference
        .and_then(|value| value.get("providers"))
        .and_then(toml::Value::as_table)
        .and_then(|providers| providers.get(provider))
        .and_then(toml::Value::as_table)
        .and_then(|table| table.get("enabled"))
        .and_then(toml::Value::as_bool)
        .is_some_and(|enabled| !enabled)
}

fn reference_provider_credential<'a>(
    reference: Option<&'a toml::map::Map<String, toml::Value>>,
    provider: &str,
) -> Option<&'a str> {
    reference
        .and_then(|value| value.get("providers"))
        .and_then(toml::Value::as_table)
        .and_then(|providers| providers.get(provider))
        .and_then(toml::Value::as_table)
        .and_then(|table| table.get("credential_id"))
        .and_then(toml::Value::as_str)
}

fn reference_product_enabled(
    reference: Option<&toml::map::Map<String, toml::Value>>,
    provider: &str,
    product: &str,
) -> bool {
    reference
        .and_then(|value| value.get("products"))
        .and_then(toml::Value::as_table)
        .and_then(|products| products.get(provider))
        .and_then(toml::Value::as_table)
        .and_then(|provider| provider.get(product))
        .and_then(toml::Value::as_table)
        .and_then(|table| table.get("enabled"))
        .and_then(toml::Value::as_bool)
        .unwrap_or(false)
}

fn reference_product_credential<'a>(
    reference: Option<&'a toml::map::Map<String, toml::Value>>,
    provider: &str,
    product: &str,
) -> Option<&'a str> {
    reference
        .and_then(|value| value.get("products"))
        .and_then(toml::Value::as_table)
        .and_then(|products| products.get(provider))
        .and_then(toml::Value::as_table)
        .and_then(|provider| provider.get(product))
        .and_then(toml::Value::as_table)
        .and_then(|table| table.get("credential_id"))
        .and_then(toml::Value::as_str)
}

impl ReferenceSnapshotWriter {
    pub fn publish(
        &mut self,
        catalog: &crate::domain::ReferenceCatalog,
    ) -> ReferenceResult<crate::application::protocol::PublishedSnapshots> {
        self.inner.publish(catalog)
    }

    pub fn publish_change(
        &mut self,
        catalog: &crate::domain::ReferenceCatalog,
        events: &[crate::domain::LifecycleEvent],
    ) -> ReferenceResult<()> {
        self.inner.publish_change(catalog, events)
    }
}

pub fn build_application(
    config: &ReferenceCompositionConfig,
    publish: bool,
) -> ReferenceResult<ReferenceComposition> {
    let source: Box<dyn ReferenceSource> = match config.provider.as_str() {
        "default" => build_default_source(config)?,
        "binance-spot" => Box::new(BinanceSpotSource::new(config.endpoint.clone())?),
        "binance-options" => Box::new(BinanceOptionsSource::new(config.endpoint.clone())?),
        "binance-usdm-futures" => {
            let integration = Integration::new()
                .with_binance_derivatives_reference(
                    kairos_integration::domain::ProductFamily::UsdMFutures,
                    config.endpoint.clone(),
                )
                .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?;
            Box::new(PublicSource::new(
                "binance-usdm-futures",
                integration
                    .connect_reference(&crate::services::providers::reference_spec_for_public(
                        IntegrationRoute::exchange("binance"),
                        kairos_integration::domain::ProductFamily::UsdMFutures,
                        Some(kairos_integration::domain::AssetType::Crypto),
                    ))
                    .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?,
            ))
        }
        "binance-coinm-futures" => {
            let integration = Integration::new()
                .with_binance_derivatives_reference(
                    kairos_integration::domain::ProductFamily::CoinMFutures,
                    config.endpoint.clone(),
                )
                .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?;
            Box::new(PublicSource::new(
                "binance-coinm-futures",
                integration
                    .connect_reference(&crate::services::providers::reference_spec_for_public(
                        IntegrationRoute::exchange("binance"),
                        kairos_integration::domain::ProductFamily::CoinMFutures,
                        Some(kairos_integration::domain::AssetType::Crypto),
                    ))
                    .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?,
            ))
        }
        "okx-spot" | "okx-equity" | "okx-swap" | "okx-futures" | "okx-options" => {
            let (product, asset_type) = match config.provider.as_str() {
                "okx-spot" => (
                    kairos_integration::domain::ProductFamily::Spot,
                    Some(kairos_integration::domain::AssetType::Crypto),
                ),
                "okx-equity" => (
                    kairos_integration::domain::ProductFamily::Spot,
                    Some(kairos_integration::domain::AssetType::Equity),
                ),
                "okx-swap" => (
                    kairos_integration::domain::ProductFamily::UsdMFutures,
                    Some(kairos_integration::domain::AssetType::Crypto),
                ),
                "okx-futures" => (
                    kairos_integration::domain::ProductFamily::CoinMFutures,
                    Some(kairos_integration::domain::AssetType::Crypto),
                ),
                _ => (
                    kairos_integration::domain::ProductFamily::Options,
                    Some(kairos_integration::domain::AssetType::Crypto),
                ),
            };
            let integration = Integration::new()
                .with_okx_reference(product, asset_type, config.endpoint.clone())
                .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?;
            Box::new(PublicSource::new(
                config.provider.clone(),
                integration
                    .connect_reference(&crate::services::providers::reference_spec_for_public(
                        IntegrationRoute::exchange("okx"),
                        product,
                        asset_type,
                    ))
                    .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?,
            ))
        }
        "binance-equity" => Box::new(BinanceEquitySource::new(
            config.binance_api_key.clone(),
            config.secret.clone(),
        )?),
        "massive-options" => Box::new(MassiveSource::new(
            config.api_key.clone(),
            config.endpoint.clone(),
        )?),
        "massive" | "massive-equity" => Box::new(MassiveEquitySource::new(
            config.api_key.clone(),
            config.endpoint.clone(),
        )?),
        "hyperliquid" => Box::new(HyperliquidSource::new(config.endpoint.clone())?),
        value => {
            return Err(crate::domain::ReferenceError::Provider(format!(
                "unsupported reference provider: {value}"
            )))
        }
    };
    let store = SqliteCatalogStore::open(&config.database)?;
    let snapshot_writer = if publish {
        Some(ReferenceSnapshotWriter {
            inner: AeronSnapshotWriter::connect(
                config.aeron_dir.as_deref(),
                &config.channel,
                config.catalog_stream,
                config.markets_stream,
                config.lifecycle_stream,
                config.changes_stream,
                "reference-actor",
                "reference.lifecycle",
            )?,
        })
    } else {
        None
    };
    Ok(ReferenceComposition {
        application: ReferenceApplication::new("reference-actor", source, Box::new(store))?,
        snapshot_writer,
    })
}

fn public_source(
    id: &str,
    product: ProductFamily,
    asset_type: Option<AssetType>,
    endpoint: &str,
) -> ReferenceResult<PublicSource> {
    let integration = match product {
        ProductFamily::UsdMFutures | ProductFamily::CoinMFutures => Integration::new()
            .with_binance_derivatives_reference(product, endpoint)
            .map_err(|e| crate::domain::ReferenceError::Provider(e.to_string()))?,
        ProductFamily::Spot | ProductFamily::Options => Integration::new()
            .with_okx_reference(product, asset_type, endpoint)
            .map_err(|e| crate::domain::ReferenceError::Provider(e.to_string()))?,
        _ => {
            return Err(crate::domain::ReferenceError::Provider(
                "unsupported public reference product".into(),
            ))
        }
    };
    let provider = if matches!(
        product,
        ProductFamily::UsdMFutures | ProductFamily::CoinMFutures
    ) {
        "binance"
    } else {
        "okx"
    };
    let route = if provider == "binance" {
        IntegrationRoute::exchange("binance")
    } else {
        IntegrationRoute::exchange("okx")
    };
    let spec = crate::services::providers::reference_spec_for_public(route, product, asset_type);
    let connection = integration
        .connect_reference(&spec)
        .map_err(|e| crate::domain::ReferenceError::Provider(e.to_string()))?;
    Ok(PublicSource::new(id, connection))
}

pub fn ensure_database_parent(path: &Path) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(())
}
