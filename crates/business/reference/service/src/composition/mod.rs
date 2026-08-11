//! Composition shared by the one-shot CLI and the long-running server.

use std::path::Path;

use crate::domain::ReferenceResult;
use crate::services::providers::{
    BinanceDerivativesSource, BinanceEquitySource, BinanceOptionsSource, BinanceSpotSource,
    CompositeSource, HyperliquidSource, MassiveEquitySource, MassiveOptionsCoverageSource,
    OkxSource, ParticipantAugmentedSource, ReferenceSource,
};
use crate::services::sqlx_storage::{SqlxCatalogStore, SqlxProviderSyncStore};
use crate::ReferenceApplication;

use kairos_integration::application::credential::load_workspace_credential;
use kairos_integration::participants::binance::InstrumentType as BinanceInstrumentType;
use kairos_integration::participants::okx::InstrumentType as OkxInstrumentType;
pub use kairos_reference_contract::transport::ReferenceMmapSnapshotConfig;
use kairos_reference_contract::transport::{
    ReferenceAeronEventWriter as AeronEventWriter,
    ReferenceMmapSnapshotWriter as MmapReferenceSnapshotWriter,
};

impl From<kairos_reference_contract::ContractError> for crate::domain::ReferenceError {
    fn from(error: kairos_reference_contract::ContractError) -> Self {
        Self::Publication(error.to_string())
    }
}

fn to_contract_catalog(
    catalog: &crate::domain::ReferenceCatalog,
) -> ReferenceResult<kairos_reference_contract::ReferenceCatalog> {
    serde_json::to_value(catalog)
        .map_err(|error| crate::domain::ReferenceError::Publication(error.to_string()))
        .and_then(|value| {
            serde_json::from_value(value)
                .map_err(|error| crate::domain::ReferenceError::Publication(error.to_string()))
        })
}

fn to_contract_events(
    events: &[crate::domain::LifecycleEvent],
) -> ReferenceResult<Vec<kairos_reference_contract::LifecycleEvent>> {
    serde_json::to_value(events)
        .map_err(|error| crate::domain::ReferenceError::Publication(error.to_string()))
        .and_then(|value| {
            serde_json::from_value(value)
                .map_err(|error| crate::domain::ReferenceError::Publication(error.to_string()))
        })
}

#[derive(Clone, Debug)]
pub struct ReferenceCompositionConfig {
    pub workspace: Option<std::path::PathBuf>,
    pub database: std::path::PathBuf,
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub reference_changes_stream: i32,
}

/// Build the same business application for both process modes.
///
/// Read-only CLI commands use `publish = false`, while refresh/publish and the
/// server use the real Aeron publisher. The provider and store are always the
/// production implementations; the disabled publisher is only for local
/// catalog inspection where no media driver is required.
pub struct ReferenceComposition {
    pub application: ReferenceApplication,
    pub event_writer: Option<ReferenceEventWriter>,
}

pub struct ReferenceEventWriter {
    inner: AeronEventWriter,
}

#[derive(Clone, Debug)]
pub struct ReferenceEventWriterConfig {
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub reference_changes_stream: i32,
}

pub struct ReferenceMmapSnapshotWriter {
    inner: MmapReferenceSnapshotWriter,
}

impl ReferenceMmapSnapshotWriter {
    pub fn create(config: ReferenceMmapSnapshotConfig) -> ReferenceResult<Self> {
        Ok(Self {
            inner: MmapReferenceSnapshotWriter::create(config)?,
        })
    }

    pub fn publish(&mut self, catalog: &crate::domain::ReferenceCatalog) -> ReferenceResult<()> {
        self.inner.publish(&to_contract_catalog(catalog)?)?;
        Ok(())
    }
}

/// Canonical provider endpoint defaults shared by the one-shot CLI and the
/// long-running Reference server.
pub fn default_endpoint(provider: &str) -> &'static str {
    match provider {
        "hyperliquid" => "https://api.hyperliquid.xyz/info",
        "binance-spot" | "binance-spot-rest" => "https://api.binance.com",
        "binance-equity" | "binance-equity-rest" => "https://api.binance.com",
        "binance-options" | "binance-options-rest" => "https://eapi.binance.com",
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

/// Build the normal Workspace Reference catalog.
///
/// Reference owns the source selection for the global catalog. Public Binance,
/// OKX, and Hyperliquid products are built in; credentialed providers such as
/// Massive are added only when explicitly enabled in
/// `[reference.providers.*]`. Every provider can be explicitly disabled there.
async fn build_default_source(
    config: &ReferenceCompositionConfig,
) -> ReferenceResult<Box<dyn ReferenceSource>> {
    let workspace = config
        .workspace
        .as_ref()
        .map(kairos_workspace::workspace::Workspace::open)
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?;
    let reference = workspace.as_ref().map(|value| value.reference_config());

    let mut sources: Vec<Box<dyn ReferenceSource>> = vec![
        Box::new(BinanceSpotSource::new(default_endpoint("binance-spot"))?),
        Box::new(BinanceDerivativesSource::new(
            BinanceInstrumentType::UsdMFutures,
            default_endpoint("binance-usdm-futures"),
        )?),
        Box::new(BinanceDerivativesSource::new(
            BinanceInstrumentType::CoinMFutures,
            default_endpoint("binance-coinm-futures"),
        )?),
    ];
    if product_enabled_or_default(reference, "binance", "options", true) {
        sources.push(Box::new(BinanceOptionsSource::new(default_endpoint(
            "binance-options",
        ))?));
    }

    let credentials_root = config
        .workspace
        .as_ref()
        .map(|root| root.join("credentials"));
    if !provider_disabled(reference, "okx") {
        sources.push(Box::new(OkxSource::new(
            "okx-spot",
            OkxInstrumentType::Spot,
            default_endpoint("okx-spot"),
        )?));
        sources.push(Box::new(OkxSource::new(
            "okx-swap",
            OkxInstrumentType::Swap,
            default_endpoint("okx-swap"),
        )?));
        sources.push(Box::new(OkxSource::new(
            "okx-futures",
            OkxInstrumentType::Futures,
            default_endpoint("okx-futures"),
        )?));
        sources.push(Box::new(OkxSource::new(
            "okx-options",
            OkxInstrumentType::Option,
            default_endpoint("okx-options"),
        )?));
    }
    if !provider_disabled(reference, "hyperliquid") {
        sources.push(Box::new(HyperliquidSource::new(default_endpoint(
            "hyperliquid",
        ))?));
    }

    let massive_credential = credentials_root.as_deref().and_then(|root| {
        load_workspace_credential(
            root,
            "massive",
            provider_config(reference, "massive").and_then(|value| value.credential_id.as_deref()),
        )
        .ok()
        .flatten()
    });
    let massive_enabled = provider_enabled(reference, "massive");
    if massive_enabled {
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
        let endpoint = provider_endpoint(reference, "massive", default_endpoint("massive"));
        if product_enabled_or_default(reference, "massive", "equity", true) {
            let equity_sync_store = SqlxProviderSyncStore::open(&config.database).await?;
            sources.push(Box::new(
                MassiveEquitySource::new_with_sync_store(
                    credential.api_key.clone(),
                    endpoint.clone(),
                    Box::new(equity_sync_store),
                )
                .await?,
            ));
        }
        if product_enabled_or_default(reference, "massive", "options", true) {
            // Stock-options coverage is explicit and mutable at runtime. Do
            // not make a global options reference scan the default just
            // because Massive can enumerate it.
            let sync_store = SqlxProviderSyncStore::open(&config.database).await?;
            sources.push(Box::new(
                MassiveOptionsCoverageSource::new(
                    credential.api_key,
                    endpoint,
                    Box::new(sync_store),
                )
                .await?,
            ));
        }
    }

    if product_enabled(reference, "binance", "equity") {
        let product = product_config(reference, "binance", "equity");
        let credential_id = product.and_then(|value| value.credential_id.as_deref());
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
        if credential.api_key.trim().is_empty() {
            return Err(crate::domain::ReferenceError::Provider(
                "Reference Binance equity source is enabled but its API key is missing".into(),
            ));
        }
        let endpoint = product
            .and_then(|value| value.endpoint.clone())
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| default_endpoint("binance-equity").to_owned());
        sources.push(Box::new(BinanceEquitySource::new(
            endpoint,
            secrecy::SecretString::new(credential.api_key.into()),
        )?));
    }

    let sync_store = SqlxProviderSyncStore::open(&config.database).await?;
    let source: Box<dyn ReferenceSource> =
        Box::new(CompositeSource::new_with_sync_store(sources, Some(Box::new(sync_store))).await?);
    let mut participants = vec![
        configured_provider("binance", "Binance"),
        configured_provider("hyperliquid", "Hyperliquid"),
    ];
    if !provider_disabled(reference, "okx") {
        participants.push(configured_provider("okx", "OKX"));
    }
    if let Some(reference) = reference {
        for (id, participant) in &reference.participants {
            if participant.enabled != Some(false) {
                if participant.entity_type.trim().is_empty() || participant.name.trim().is_empty() {
                    return Err(crate::domain::ReferenceError::Provider(format!(
                        "reference participant {id} requires type and name"
                    )));
                }
                participants.push(crate::domain::Entity {
                    source_id: None,
                    entity_id: format!("{}:{id}", participant.entity_type),
                    entity_type: participant.entity_type.clone(),
                    name: participant.name.clone(),
                    status: "active".into(),
                });
            }
        }
    }
    Ok(ParticipantAugmentedSource::wrap(source, participants))
}

fn configured_provider(id: &str, name: &str) -> crate::domain::Entity {
    crate::domain::Entity {
        source_id: None,
        entity_id: format!("data_provider:{id}"),
        entity_type: "data_provider".into(),
        name: name.into(),
        status: "active".into(),
    }
}

fn provider_config<'a>(
    reference: Option<&'a kairos_workspace::workspace::WorkspaceReferenceConfig>,
    provider: &str,
) -> Option<&'a kairos_workspace::workspace::WorkspaceReferenceProviderConfig> {
    reference.and_then(|value| value.providers.get(provider))
}

fn provider_enabled(
    reference: Option<&kairos_workspace::workspace::WorkspaceReferenceConfig>,
    provider: &str,
) -> bool {
    provider_config(reference, provider)
        .and_then(|value| value.enabled)
        .unwrap_or(false)
}

fn provider_disabled(
    reference: Option<&kairos_workspace::workspace::WorkspaceReferenceConfig>,
    provider: &str,
) -> bool {
    provider_config(reference, provider).and_then(|value| value.enabled) == Some(false)
}

fn provider_endpoint(
    reference: Option<&kairos_workspace::workspace::WorkspaceReferenceConfig>,
    provider: &str,
    default: &str,
) -> String {
    provider_config(reference, provider)
        .and_then(|value| value.endpoint.clone())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| default.to_owned())
}

fn product_config<'a>(
    reference: Option<&'a kairos_workspace::workspace::WorkspaceReferenceConfig>,
    provider: &str,
    product: &str,
) -> Option<&'a kairos_workspace::workspace::WorkspaceReferenceProductConfig> {
    reference
        .and_then(|value| value.products.get(provider))
        .and_then(|value| value.get(product))
}

fn product_enabled_or_default(
    reference: Option<&kairos_workspace::workspace::WorkspaceReferenceConfig>,
    provider: &str,
    product: &str,
    default: bool,
) -> bool {
    product_config(reference, provider, product)
        .and_then(|value| value.enabled)
        .unwrap_or(default)
}

fn product_enabled(
    reference: Option<&kairos_workspace::workspace::WorkspaceReferenceConfig>,
    provider: &str,
    product: &str,
) -> bool {
    product_config(reference, provider, product)
        .and_then(|value| value.enabled)
        .unwrap_or(false)
}

impl ReferenceEventWriter {
    pub fn connect(config: &ReferenceEventWriterConfig) -> ReferenceResult<Self> {
        Ok(Self {
            inner: AeronEventWriter::connect(
                config.aeron_dir.as_deref(),
                &config.aeron_channel,
                config.reference_changes_stream,
                "reference-actor",
                "reference.lifecycle",
            )?,
        })
    }

    pub fn publish(
        &mut self,
        catalog: &crate::domain::ReferenceCatalog,
        events: &[crate::domain::LifecycleEvent],
    ) -> ReferenceResult<()> {
        // Change encoding needs only the catalog watermarks. Converting the
        // complete catalog for every outbox batch is prohibitively expensive
        // for large reference universes. The batch sequence is its own high
        // watermark, not the latest catalog sequence repeated for old events.
        let event_sequence = events
            .last()
            .and_then(|event| event.event_id.rsplit(':').next())
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or_else(|| catalog.event_sequence.get());
        let contract_catalog = kairos_reference_contract::ReferenceCatalog {
            generation: catalog.generation.get(),
            event_sequence,
            ..Default::default()
        };
        self.inner
            .publish(&contract_catalog, &to_contract_events(events)?)?;
        Ok(())
    }
}

pub async fn build_application(
    config: &ReferenceCompositionConfig,
    publish: bool,
) -> ReferenceResult<ReferenceComposition> {
    if config.reference_changes_stream != kairos_transport::stream_ids::REFERENCE_CHANGES {
        return Err(crate::domain::ReferenceError::Invalid(format!(
            "reference changes stream must be the registered stream {}",
            kairos_transport::stream_ids::REFERENCE_CHANGES
        )));
    }
    let source = build_default_source(config).await?;
    let store = SqlxCatalogStore::open(&config.database).await?;
    let event_writer = if publish {
        Some(ReferenceEventWriter::connect(
            &ReferenceEventWriterConfig {
                aeron_dir: config.aeron_dir.clone(),
                aeron_channel: config.aeron_channel.clone(),
                reference_changes_stream: config.reference_changes_stream,
            },
        )?)
    } else {
        None
    };
    Ok(ReferenceComposition {
        application: ReferenceApplication::new("reference-actor", source, Box::new(store)).await?,
        event_writer,
    })
}

pub fn ensure_database_parent(path: &Path) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(())
}
