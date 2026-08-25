//! Runtime composition for the long-running Reference server.

mod config;

use std::path::Path;

pub use config::{
    BinanceReferenceEndpoints, BinanceReferenceProvider, CredentialedReferenceProvider,
    PublicReferenceProvider, ReferenceConfig, ReferenceProviders, ReferenceRuntimeConfig,
    ReferenceTickBudgetConfig,
};
use kairos_conflux::{AeronOutputDeclaration, BinanceCredential};
use kairos_credentials::CredentialStore;

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

/// Build the normal Workspace Reference catalog from typed, owner-defined
/// source bindings. Runtime ids and synchronization policies are never read
/// from user configuration.
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
        .map_err(crate::domain::ReferenceError::Provider)?
        .unwrap_or_default();
    let credentials_root = workspace
        .as_ref()
        .map(|workspace| workspace.existing_credentials_root())
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?;
    let credential_resolver = credentials_root
        .as_ref()
        .map(|root| CredentialStore::load(root).map(ReferenceCredentialResolver::from_store))
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?
        .unwrap_or_default();

    let mut providers = Vec::new();
    let configured = reference.providers;
    if configured.binance.enabled {
        let credential_id = configured.binance.credential_id;
        let endpoints = configured.binance.endpoints;
        providers.push(ReferenceProviderPlan::BinanceSpot {
            key: "reference-binance-spot".into(),
            endpoint: configured_endpoint(endpoints.spot, default_endpoint("binance-spot")),
        });
        providers.push(ReferenceProviderPlan::BinanceUsdM {
            key: "reference-binance-usdm".into(),
            endpoint: configured_endpoint(
                endpoints.usd_m_futures,
                default_endpoint("binance-usdm-futures"),
            ),
        });
        providers.push(ReferenceProviderPlan::BinanceCoinM {
            key: "reference-binance-coinm".into(),
            endpoint: configured_endpoint(
                endpoints.coin_m_futures,
                default_endpoint("binance-coinm-futures"),
            ),
        });
        providers.push(ReferenceProviderPlan::BinanceOptions {
            key: "reference-binance-options".into(),
            endpoint: configured_endpoint(endpoints.options, default_endpoint("binance-options")),
        });
        if credential_id.is_some() {
            let credential = load_required_credential(
                credentials_root.as_deref(),
                "binance",
                credential_id.as_deref(),
                "Binance equity",
            )?;
            providers.push(ReferenceProviderPlan::BinanceEquity {
                key: "reference-binance-stocks".into(),
                endpoint: configured_endpoint(endpoints.equity, default_endpoint("binance-equity")),
                credential: BinanceCredential {
                    principal_id: credential_id
                        .as_deref()
                        .expect("credential id was present")
                        .into(),
                    api_key: secrecy::SecretString::new(credential.0.into()),
                    secret: credential.1,
                },
            });
        }
    }
    if configured.okx.enabled {
        let endpoint = configured.okx.endpoint;
        for product in [
            OkxProduct::Spot,
            OkxProduct::Margin,
            OkxProduct::Swap,
            OkxProduct::Futures,
            OkxProduct::Option,
        ] {
            let source_id = product.source_id();
            providers.push(ReferenceProviderPlan::Okx {
                key: format!("reference-{source_id}"),
                source_id: source_id.into(),
                product,
                endpoint: configured_endpoint(endpoint.clone(), default_endpoint(source_id)),
            });
        }
    }
    if configured.hyperliquid.enabled {
        let endpoint = configured.hyperliquid.endpoint;
        for product in [HyperliquidProduct::Spot, HyperliquidProduct::Perpetual] {
            providers.push(ReferenceProviderPlan::Hyperliquid {
                key: format!("reference-{}", product.source_id()),
                product,
                endpoint: configured_endpoint(endpoint.clone(), default_endpoint("hyperliquid")),
            });
        }
    }
    if configured.massive.enabled {
        let mut credential_id = configured.massive.credential_id;
        let mut endpoint = configured.massive.endpoint;
        if let Some(connection_id) = configured.massive.connection_id.as_deref() {
            let workspace = workspace.as_ref().ok_or_else(|| {
                crate::domain::ReferenceError::Provider(
                    "Reference connection binding requires a Workspace".into(),
                )
            })?;
            let root = kairos_integration::composition::ProviderConnectionProfile::canonical_root(
                workspace.root(),
            );
            let connection = kairos_integration::composition::ProviderConnectionProfile::load(
                &root,
                connection_id,
            )
            .map_err(crate::domain::ReferenceError::Provider)?;
            connection
                .require("massive", Some("reference"), "reference-catalog")
                .map_err(crate::domain::ReferenceError::Provider)?;
            credential_id = Some(connection.credential_id);
            endpoint = Some(connection.endpoint);
        }
        let credential = load_required_credential(
            credentials_root.as_deref(),
            "massive",
            credential_id.as_deref(),
            "Massive",
        )?;
        let endpoint = configured_endpoint(endpoint, default_endpoint("massive"));
        providers.push(ReferenceProviderPlan::MassiveEquity {
            key: "reference-massive-equity".into(),
            api_key: credential.0.clone(),
            endpoint: endpoint.clone(),
            sync_store: SqlxProviderSyncStore::open(&config.database).await?,
        });
        let mut sync_store = SqlxProviderSyncStore::open(&config.database).await?;
        let underlyings = sync_store.option_underlyings("massive-options").await?;
        providers.push(ReferenceProviderPlan::MassiveOptions {
            api_key: credential.0,
            endpoint,
            sync_store,
            underlyings,
        });
    }

    let sync_store = SqlxProviderSyncStore::open(&config.database).await?;
    Ok(ReferenceSourcePlan::new_with_credential_resolver(
        providers,
        sync_store,
        credential_resolver,
    ))
}

fn configured_endpoint(endpoint: Option<String>, default: &str) -> String {
    endpoint
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| default.to_owned())
}

fn load_required_credential(
    credentials_root: Option<&Path>,
    provider: &str,
    credential_id: Option<&str>,
    label: &str,
) -> ReferenceResult<(String, secrecy::SecretString)> {
    let credential = credentials_root
        .map(CredentialStore::load)
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?
        .and_then(|store| store.find_provider(provider, credential_id).cloned())
        .ok_or_else(|| {
            crate::domain::ReferenceError::Provider(format!(
                "Reference {label} source is enabled but its credential is missing"
            ))
        })?;
    let api_key = credential.api_key_value().ok_or_else(|| {
        crate::domain::ReferenceError::Provider(format!(
            "Reference {label} source is enabled but its API key is missing"
        ))
    })?;
    let secret = credential.value("api_secret").cloned().unwrap_or_default();
    Ok((api_key, secret))
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
    application.configure_conflux(runtime_config.refresh_interval(), true);
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
