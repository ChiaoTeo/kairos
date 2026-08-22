//! Runtime composition for the long-running Reference server.

mod config;

use std::path::Path;

pub use config::{
    ReferenceConfig, ReferenceParticipantConfig, ReferenceProductConfig, ReferenceProviderConfig,
    ReferenceRuntimeConfig, ReferenceTickBudgetConfig,
};
use kairos_conflux::{
    AeronOutputDeclaration, BinanceCredential, CredentialStore, load_workspace_credential,
};

use crate::ReferenceApplication;
use crate::domain::ReferenceResult;
use crate::logging::events as log_events;
use crate::services::providers::{
    HyperliquidProduct, OkxProduct, ReferenceCredentialResolver, ReferenceProviderPlan,
    ReferenceSourcePlan, default_endpoint,
};
use crate::services::storage::catalog_store::SqlxCatalogStore;
use crate::services::storage::provider_sync_store::SqlxProviderSyncStore;

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

/// Concrete runtime assembly for the Reference daemon.
///
/// Composition owns static startup choices: workspace configuration, durable
/// stores, source plans, Conflux connections, and publication transport. It
/// does not process CLI commands or become a second application facade.
pub struct ReferenceComposition {
    pub application: ComposedReferenceApplication,
    system: kairos_conflux::ConfluxSystem,
}

impl ReferenceComposition {
    pub async fn activate_sources(&mut self) -> ReferenceResult<()> {
        let log_event = log_events::APP_PHASE_STARTED;
        tracing::info!(
            event = log_event.event,
            component = log_event.component,
            area = log_event.area,
            action = log_event.action,
            outcome = log_event.outcome,
            legacy_event = "reference_runtime_stage_started",
            stage = "activate_sources",
            "reference runtime stage started"
        );
        let result = self
            .application
            .activate_sources(&mut self.system.connections())
            .await;
        if result.is_ok() {
            let log_event = log_events::APP_PHASE_COMPLETED;
            tracing::info!(
                event = log_event.event,
                component = log_event.component,
                area = log_event.area,
                action = log_event.action,
                outcome = log_event.outcome,
                legacy_event = "reference_runtime_stage_completed",
                stage = "activate_sources",
                "reference runtime stage completed"
            );
        }
        result
    }

    pub fn into_conflux(self) -> (ReferenceApplication, kairos_conflux::ConfluxSystem) {
        (self.application, self.system)
    }

    pub fn split_mut(
        &mut self,
    ) -> (
        &mut ComposedReferenceApplication,
        &mut kairos_conflux::ConfluxSystem,
    ) {
        (&mut self.application, &mut self.system)
    }
}

pub type ComposedReferenceApplication = ReferenceApplication;

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
    let credential_resolver = credentials_root
        .as_ref()
        .map(|root| {
            CredentialStore::load(root.join("credentials.toml"))
                .map(ReferenceCredentialResolver::from_store)
        })
        .transpose()
        .map_err(crate::domain::ReferenceError::Provider)?
        .unwrap_or_default();
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
    Ok(ReferenceSourcePlan::new_with_credential_resolver(
        providers,
        participants,
        sync_store,
        credential_resolver,
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

pub fn declare_reference_changes_output(
    config: &ReferenceCompositionConfig,
    system: &mut kairos_conflux::ConfluxSystem,
) -> ReferenceResult<()> {
    let endpoint = kairos_reference_contract::AeronEndpoint::from_parts(
        config.aeron_dir.as_deref(),
        config.aeron_channel.clone(),
        config.reference_changes_stream,
    )
    .map_err(|error| crate::domain::ReferenceError::Publication(error.to_string()))?;
    system
        .outputs()
        .aeron
        .declare(
            "reference-changes".to_owned(),
            AeronOutputDeclaration {
                endpoint,
                revision: 1,
            },
        )
        .map(|_| ())
        .map_err(|error| crate::domain::ReferenceError::Publication(error.to_string()))
}

pub async fn build_application(
    config: &ReferenceCompositionConfig,
    publish: bool,
) -> ReferenceResult<ReferenceComposition> {
    let log_event = log_events::STARTUP_STAGE_STARTED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_startup_stage_started",
        stage = "validate_configuration",
        "reference startup stage started"
    );
    if config.reference_changes_stream != kairos_conflux::output_stream_ids::REFERENCE_CHANGES {
        return Err(crate::domain::ReferenceError::Invalid(format!(
            "reference changes stream must be the registered stream {}",
            kairos_conflux::output_stream_ids::REFERENCE_CHANGES
        )));
    }
    let log_event = log_events::STARTUP_STAGE_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_startup_stage_completed",
        stage = "validate_configuration",
        "reference startup stage completed"
    );

    let log_event = log_events::STARTUP_STAGE_STARTED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_startup_stage_started",
        stage = "open_database",
        database = %config.database.display(),
        "reference startup stage started"
    );
    let mut store = SqlxCatalogStore::open(&config.database).await?;
    let log_event = log_events::STARTUP_STAGE_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_startup_stage_completed",
        stage = "open_database",
        "reference startup stage completed"
    );

    let log_event = log_events::STARTUP_STAGE_STARTED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_startup_stage_started",
        stage = "integrity_audit",
        "reference startup stage started"
    );
    let startup_audit = store.audit_and_prepare_startup_repair().await?;
    if !startup_audit.missing_current_equity_markets.is_empty()
        || !startup_audit.missing_provider_equity_markets.is_empty()
    {
        let log_event = log_events::STARTUP_STAGE_DEGRADED;
        tracing::warn!(
            event = log_event.event,
            component = log_event.component,
            area = log_event.area,
            action = log_event.action,
            outcome = log_event.outcome,
            legacy_event = "reference_startup_integrity_repair",
            missing_current_equity_market_count =
                startup_audit.missing_current_equity_markets.len(),
            missing_provider_equity_market_count =
                startup_audit.missing_provider_equity_markets.len(),
            reset_providers = ?startup_audit.reset_providers,
            "reference startup integrity audit found missing equity markets"
        );
    }
    let log_event = log_events::STARTUP_STAGE_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_startup_stage_completed",
        stage = "integrity_audit",
        missing_current_equity_market_count = startup_audit.missing_current_equity_markets.len(),
        missing_provider_equity_market_count = startup_audit.missing_provider_equity_markets.len(),
        reset_provider_count = startup_audit.reset_providers.len(),
        reset_providers = ?startup_audit.reset_providers,
        "reference startup stage completed"
    );

    let log_event = log_events::STARTUP_STAGE_STARTED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_startup_stage_started",
        stage = "build_source_plan",
        "reference startup stage started"
    );
    let source_plan = build_source_plan(config).await?;
    let log_event = log_events::STARTUP_STAGE_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_startup_stage_completed",
        stage = "build_source_plan",
        "reference startup stage completed"
    );

    let log_event = log_events::STARTUP_STAGE_STARTED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_startup_stage_started",
        stage = "install_connections",
        "reference startup stage started"
    );
    let mut system = kairos_conflux::ConfluxSystem::new();
    source_plan.install(&mut system.connections())?;
    let log_event = log_events::STARTUP_STAGE_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_startup_stage_completed",
        stage = "install_connections",
        "reference startup stage completed"
    );
    if publish {
        let log_event = log_events::STARTUP_STAGE_STARTED;
        tracing::info!(
            event = log_event.event,
            component = log_event.component,
            area = log_event.area,
            action = log_event.action,
            outcome = log_event.outcome,
            legacy_event = "reference_startup_stage_started",
            stage = "declare_publication",
            "reference startup stage started"
        );
        declare_reference_changes_output(config, &mut system)?;
        let log_event = log_events::STARTUP_STAGE_COMPLETED;
        tracing::info!(
            event = log_event.event,
            component = log_event.component,
            area = log_event.area,
            action = log_event.action,
            outcome = log_event.outcome,
            legacy_event = "reference_startup_stage_completed",
            stage = "declare_publication",
            "reference startup stage completed"
        );
    }
    let runtime_config = config
        .workspace
        .as_ref()
        .map(kairos_workspace::workspace::Workspace::open)
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?
        .as_ref()
        .map(ReferenceConfig::load)
        .transpose()
        .map_err(crate::domain::ReferenceError::Provider)?
        .map(|reference| reference.runtime)
        .unwrap_or_default();
    let tick_budget = runtime_config.tick_budget.to_domain()?;
    let mut application = ReferenceApplication::new("reference-actor", source_plan, store).await?;
    application.configure_tick_budget(tick_budget);
    Ok(ReferenceComposition {
        application,
        system,
    })
}

pub fn ensure_database_parent(path: &Path) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(())
}
