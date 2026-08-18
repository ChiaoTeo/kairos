//! Composition shared by the one-shot CLI and the long-running server.

mod config;
mod publication;

pub use config::{
    ReferenceConfig, ReferenceParticipantConfig, ReferenceProductConfig, ReferenceProviderConfig,
};
pub use publication::ReferenceEventPublisherRuntime;

use std::path::Path;

use crate::domain::ReferenceResult;
use crate::services::providers::{
    HyperliquidProduct, OkxProduct, ReferenceProviderPlan, ReferenceSourcePlan,
};
use crate::services::sqlx_storage::{SqlxCatalogStore, SqlxProviderSyncStore};
use crate::ReferenceApplication;

use kairos_conflux::{load_workspace_credential, BinanceCredential};
use kairos_transport::AeronBytePublisher;

impl From<kairos_reference_contract::ContractError> for crate::domain::ReferenceError {
    fn from(error: kairos_reference_contract::ContractError) -> Self {
        Self::Publication(error.to_string())
    }
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
    pub application: ComposedReferenceApplication,
    pub event_writer: Option<ReferenceEventWriter>,
    system: kairos_conflux::ConfluxSystem,
}

impl ReferenceComposition {
    pub async fn activate_sources(&mut self) -> ReferenceResult<()> {
        self.application
            .activate_sources(&mut self.system.connections())
            .await
    }

    pub fn into_conflux(
        self,
    ) -> (
        ReferenceApplication,
        kairos_conflux::ConfluxSystem,
        Option<ReferenceEventWriter>,
    ) {
        (self.application, self.system, self.event_writer)
    }

    pub fn split_mut(
        &mut self,
    ) -> (
        &mut ComposedReferenceApplication,
        &mut kairos_conflux::ConfluxSystem,
        Option<&mut ReferenceEventWriter>,
    ) {
        (
            &mut self.application,
            &mut self.system,
            self.event_writer.as_mut(),
        )
    }
}

pub type ComposedReferenceApplication = ReferenceApplication;

pub struct ReferenceEventWriter {
    publisher: kairos_transport::AeronBytePublisher,
}

pub struct ReferenceEventWriterConfig {
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub reference_changes_stream: i32,
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
        "okx-spot" | "okx-margin" | "okx-equity" | "okx-swap" | "okx-futures" | "okx-options"
        | "okx-spot-rest" | "okx-margin-rest" | "okx-swap-rest" | "okx-futures-rest"
        | "okx-options-rest" => "https://www.okx.com",
        "massive"
        | "massive-equity"
        | "massive-equity-websocket"
        | "massive-options"
        | "massive-options-websocket" => "http://api.massiveprivateserver.site",
        _ => "",
    }
}

/// Build the normal Workspace Reference catalog.
///
/// Reference owns the source selection for the global catalog. Public Binance,
/// OKX, and Hyperliquid products are built in; credentialed providers such as
/// Massive are added only when explicitly enabled in
/// `[reference.providers.*]`. Every provider can be explicitly disabled there.
async fn build_source_plan(
    config: &ReferenceCompositionConfig,
) -> ReferenceResult<ReferenceSourcePlan> {
    let workspace = config
        .workspace
        .as_ref()
        .map(kairos_workspace::workspace::Workspace::open)
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?;
    let reference = workspace
        .as_ref()
        .map(ReferenceConfig::load)
        .transpose()
        .map_err(crate::domain::ReferenceError::Provider)?;
    let reference = reference.as_ref();

    let mut providers = Vec::new();
    if !provider_disabled(reference, "binance")
        && product_enabled_or_default(reference, "binance", "spot", true)
    {
        providers.push(ReferenceProviderPlan::BinanceSpot {
            key: "reference-binance-spot".into(),
            endpoint: product_endpoint(
                reference,
                "binance",
                "spot",
                default_endpoint("binance-spot"),
            ),
        });
    }
    if !provider_disabled(reference, "binance")
        && product_enabled_or_default(reference, "binance", "usd-m-futures", true)
    {
        providers.push(ReferenceProviderPlan::BinanceUsdM {
            key: "reference-binance-usdm".into(),
            endpoint: product_endpoint(
                reference,
                "binance",
                "usd-m-futures",
                default_endpoint("binance-usdm-futures"),
            ),
        });
    }
    if !provider_disabled(reference, "binance")
        && product_enabled_or_default(reference, "binance", "coin-m-futures", true)
    {
        providers.push(ReferenceProviderPlan::BinanceCoinM {
            key: "reference-binance-coinm".into(),
            endpoint: product_endpoint(
                reference,
                "binance",
                "coin-m-futures",
                default_endpoint("binance-coinm-futures"),
            ),
        });
    }
    if !provider_disabled(reference, "binance")
        && product_enabled_or_default(reference, "binance", "options", true)
    {
        providers.push(ReferenceProviderPlan::BinanceOptions {
            key: "reference-binance-options".into(),
            endpoint: product_endpoint(
                reference,
                "binance",
                "options",
                default_endpoint("binance-options"),
            ),
        });
    }

    let credentials_root = workspace
        .as_ref()
        .map(|workspace| workspace.config_root().join("credentials"));
    if !provider_disabled(reference, "okx") {
        for (product, source_id, instrument_type) in [
            ("spot", "okx-spot", OkxProduct::Spot),
            ("margin", "okx-margin", OkxProduct::Margin),
            ("swap", "okx-swap", OkxProduct::Swap),
            ("futures", "okx-futures", OkxProduct::Futures),
            ("options", "okx-options", OkxProduct::Option),
        ] {
            if product_enabled_or_default(reference, "okx", product, true) {
                providers.push(ReferenceProviderPlan::Okx {
                    key: format!("reference-{source_id}"),
                    source_id: source_id.into(),
                    product: instrument_type,
                    endpoint: product_endpoint(
                        reference,
                        "okx",
                        product,
                        default_endpoint(source_id),
                    ),
                });
            }
        }
    }
    if !provider_disabled(reference, "hyperliquid") {
        if product_enabled_or_default(reference, "hyperliquid", "perpetual", true) {
            providers.push(ReferenceProviderPlan::Hyperliquid {
                key: "reference-hyperliquid-perpetual".into(),
                product: HyperliquidProduct::Perpetual,
                endpoint: product_endpoint(
                    reference,
                    "hyperliquid",
                    "perpetual",
                    default_endpoint("hyperliquid"),
                ),
            });
        }
        if product_enabled_or_default(reference, "hyperliquid", "spot", true) {
            providers.push(ReferenceProviderPlan::Hyperliquid {
                key: "reference-hyperliquid-spot".into(),
                product: HyperliquidProduct::Spot,
                endpoint: product_endpoint(
                    reference,
                    "hyperliquid",
                    "spot",
                    default_endpoint("hyperliquid"),
                ),
            });
        }
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
            providers.push(ReferenceProviderPlan::MassiveEquity {
                key: "reference-massive-equity".into(),
                api_key: credential.api_key.clone(),
                endpoint: endpoint.clone(),
                sync_store: equity_sync_store,
            });
        }
        if product_enabled_or_default(reference, "massive", "options", true) {
            // Stock-options coverage is explicit and mutable at runtime. Do
            // not make a global options reference scan the default just
            // because Massive can enumerate it.
            let mut sync_store = SqlxProviderSyncStore::open(&config.database).await?;
            let underlyings = sync_store.option_underlyings("massive-options").await?;
            providers.push(ReferenceProviderPlan::MassiveOptions {
                api_key: credential.api_key,
                endpoint,
                sync_store,
                underlyings,
            });
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
        providers.push(ReferenceProviderPlan::BinanceEquity {
            key: "reference-binance-stocks".into(),
            endpoint,
            credential: BinanceCredential {
                principal_id: credential_id.unwrap_or("reference-binance-stocks").into(),
                api_key: secrecy::SecretString::new(credential.api_key.into()),
                secret: credential.secret,
            },
        });
    }

    let sync_store = SqlxProviderSyncStore::open(&config.database).await?;
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
                let entity_type = crate::domain::EntityKind::from(participant.entity_type.as_str());
                if entity_type == crate::domain::EntityKind::Unknown
                    || participant.name.trim().is_empty()
                {
                    return Err(crate::domain::ReferenceError::Provider(format!(
                        "reference participant {id} requires a supported type and name"
                    )));
                }
                participants.push(crate::domain::Entity {
                    source_id: None,
                    entity_id: format!("{}:{id}", entity_type.as_str()),
                    entity_type,
                    name: participant.name.clone(),
                    status: "active".into(),
                });
            }
        }
    }
    Ok(ReferenceSourcePlan::new(
        providers,
        participants,
        sync_store,
    ))
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
    reference: Option<&'a ReferenceConfig>,
    provider: &str,
) -> Option<&'a ReferenceProviderConfig> {
    reference.and_then(|value| value.providers.get(provider))
}

fn provider_enabled(reference: Option<&ReferenceConfig>, provider: &str) -> bool {
    provider_config(reference, provider)
        .and_then(|value| value.enabled)
        .unwrap_or(false)
}

fn provider_disabled(reference: Option<&ReferenceConfig>, provider: &str) -> bool {
    provider_config(reference, provider).and_then(|value| value.enabled) == Some(false)
}

fn provider_endpoint(reference: Option<&ReferenceConfig>, provider: &str, default: &str) -> String {
    provider_config(reference, provider)
        .and_then(|value| value.endpoint.clone())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| default.to_owned())
}

fn product_endpoint(
    reference: Option<&ReferenceConfig>,
    provider: &str,
    product: &str,
    default: &str,
) -> String {
    product_config(reference, provider, product)
        .and_then(|value| value.endpoint.clone())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| provider_endpoint(reference, provider, default))
}

fn product_config<'a>(
    reference: Option<&'a ReferenceConfig>,
    provider: &str,
    product: &str,
) -> Option<&'a ReferenceProductConfig> {
    reference
        .and_then(|value| value.products.get(provider))
        .and_then(|value| value.get(product))
}

fn product_enabled_or_default(
    reference: Option<&ReferenceConfig>,
    provider: &str,
    product: &str,
    default: bool,
) -> bool {
    product_config(reference, provider, product)
        .and_then(|value| value.enabled)
        .unwrap_or(default)
}

fn product_enabled(reference: Option<&ReferenceConfig>, provider: &str, product: &str) -> bool {
    product_config(reference, provider, product)
        .and_then(|value| value.enabled)
        .unwrap_or(false)
}

impl ReferenceEventWriter {
    pub fn connect(config: &ReferenceEventWriterConfig) -> ReferenceResult<Self> {
        Ok(Self {
            publisher: AeronBytePublisher::connect(
                config.aeron_dir.as_deref(),
                &config.aeron_channel,
                config.reference_changes_stream,
            )
            .map_err(|error| crate::domain::ReferenceError::Publication(error.to_string()))?,
        })
    }

    pub fn publish(&mut self, publications: &[crate::ReferencePublication]) -> ReferenceResult<()> {
        for publication in publications {
            self.publisher
                .publish(publication.payload())
                .map_err(|error| crate::domain::ReferenceError::Publication(error.to_string()))?;
        }
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
    let source_plan = build_source_plan(config).await?;
    let mut system = kairos_conflux::ConfluxSystem::new();
    source_plan.install(&mut system.connections())?;
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
        application: ReferenceApplication::new("reference-actor", source_plan, store).await?,
        event_writer,
        system,
    })
}

pub fn ensure_database_parent(path: &Path) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(())
}
