//! Runtime composition for the long-running Reference server.

mod config;

use std::path::Path;

pub use config::{ReferenceConfig, ReferenceRuntimeConfig, ReferenceTickBudgetConfig};
use kairos_conflux::AeronOutputDeclaration;
use kairos_credentials::CredentialStore;

use crate::ReferenceApplication;
use crate::domain::{ReferenceResult, SourceConnectionId, SourceDesiredState, SourceScope};
use crate::logging::events as log_events;
use crate::services::providers::{
    BinanceReferenceSource, HyperliquidProduct, MassiveReferenceSource, OkxProduct,
    ReferenceCredentialResolver, ReferenceProviderPlan, ReferenceSourceBinding,
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
    if let Some(workspace) = workspace.as_ref() {
        let reference_config = ReferenceConfig::load(workspace)
            .map_err(crate::domain::ReferenceError::Configuration)?;
        migrate_legacy_provider_configuration(workspace, &config.database, &reference_config)
            .await?;
    }
    let credentials_root = workspace
        .as_ref()
        .map(|workspace| workspace.existing_credentials_root())
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?;
    let mut credential_resolver = credentials_root
        .as_ref()
        .map(|root| CredentialStore::load(root).map(ReferenceCredentialResolver::from_store))
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?
        .unwrap_or_default();
    if let Some(workspace) = workspace.as_ref() {
        credential_resolver = credential_resolver.load_connection_profiles(
            &kairos_integration::composition::ProviderConnectionProfile::canonical_root(
                workspace.root(),
            ),
        )?;
    }

    // Public, credential-free catalog sources are first-start seeds. Each
    // product is an independent persisted source definition; there is no
    // provider-wide Reference switch that implicitly enables other products.
    let mut providers = vec![
        ReferenceProviderPlan::BinanceSpot {
            key: "reference-binance-spot".into(),
            endpoint: default_endpoint("binance-spot").into(),
        },
        ReferenceProviderPlan::BinanceUsdM {
            key: "reference-binance-usdm".into(),
            endpoint: default_endpoint("binance-usdm-futures").into(),
        },
        ReferenceProviderPlan::BinanceCoinM {
            key: "reference-binance-coinm".into(),
            endpoint: default_endpoint("binance-coinm-futures").into(),
        },
        ReferenceProviderPlan::BinanceOptions {
            key: "reference-binance-options".into(),
            endpoint: default_endpoint("binance-options").into(),
        },
    ];
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
            endpoint: default_endpoint(source_id).into(),
        });
    }
    for product in [HyperliquidProduct::Spot, HyperliquidProduct::Perpetual] {
        providers.push(ReferenceProviderPlan::Hyperliquid {
            key: format!("reference-{}", product.source_id()),
            product,
            endpoint: default_endpoint("hyperliquid").into(),
        });
    }

    let sync_store = SqlxProviderSyncStore::open(&config.database).await?;
    Ok(ReferenceSourcePlan::new_with_credential_resolver(
        providers,
        sync_store,
        credential_resolver,
    ))
}

async fn migrate_legacy_provider_configuration(
    workspace: &kairos_workspace::Workspace,
    database: &Path,
    config: &ReferenceConfig,
) -> ReferenceResult<()> {
    if config.legacy_providers().is_empty() {
        return Ok(());
    }
    let mut store = SqlxProviderSyncStore::open(database).await?;
    let existing = store
        .source_definitions()
        .await?
        .into_iter()
        .map(|definition| definition.source_id.to_string())
        .collect::<std::collections::BTreeSet<_>>();
    let connection_root =
        kairos_integration::composition::ProviderConnectionProfile::canonical_root(
            workspace.root(),
        );
    std::fs::create_dir_all(&connection_root)
        .map_err(|error| crate::domain::ReferenceError::Configuration(error.to_string()))?;

    for (provider, legacy) in config.legacy_providers() {
        let bindings = legacy_bindings(provider, legacy)?;
        let connection_path = connection_root.join(format!("{provider}.toml"));
        let wants_profile = connection_path.is_file()
            || legacy.endpoint.is_some()
            || legacy.credential_id.is_some();
        if wants_profile && !connection_path.exists() {
            if bindings.iter().any(|binding| binding.requires_credential())
                && legacy.credential_id.as_deref().is_none_or(str::is_empty)
                && legacy.enabled
            {
                return Err(crate::domain::ReferenceError::Configuration(format!(
                    "legacy Reference provider {provider} requires credential_id before it can migrate to an Integration connection profile"
                )));
            }
            let endpoint = legacy
                .endpoint
                .as_deref()
                .unwrap_or_else(|| legacy_default_endpoint(provider));
            if !endpoint.starts_with("https://") {
                return Err(crate::domain::ReferenceError::Configuration(format!(
                    "legacy Reference provider {provider} endpoint must use HTTPS before migration"
                )));
            }
            let products = bindings
                .iter()
                .map(|binding| binding.product())
                .collect::<Vec<_>>();
            write_legacy_connection_profile(
                &connection_path,
                provider,
                endpoint,
                legacy.credential_id.as_deref().unwrap_or("public"),
                legacy.enabled,
                &products,
            )?;
        }

        let connection_id = wants_profile
            .then(|| SourceConnectionId::new(provider))
            .transpose()?;
        for binding in bindings {
            if existing.contains(binding.source_id()) {
                continue;
            }
            let definition = binding.definition(
                SourceScope::global(),
                if legacy.enabled {
                    SourceDesiredState::Enabled
                } else {
                    SourceDesiredState::Disabled
                },
                connection_id.clone(),
            )?;
            store.upsert_source_definition(definition).await?;
        }
    }
    Ok(())
}

fn legacy_bindings(
    provider: &str,
    legacy: &config::LegacyReferenceProviderConfig,
) -> ReferenceResult<Vec<ReferenceSourceBinding>> {
    let selected = legacy
        .product
        .iter()
        .chain(legacy.products.iter())
        .map(String::as_str)
        .collect::<Vec<_>>();
    let selected = if selected.is_empty() {
        match provider {
            "binance" | "okx" => vec!["spot"],
            "hyperliquid" => vec!["perpetual"],
            "massive" => vec!["equity"],
            _ => Vec::new(),
        }
    } else {
        selected
    };
    selected
        .into_iter()
        .map(|product| match (provider, product) {
            ("binance", "spot") => Ok(ReferenceSourceBinding::Binance(
                BinanceReferenceSource::Spot,
            )),
            ("binance", "usd-m-futures") => Ok(ReferenceSourceBinding::Binance(
                BinanceReferenceSource::UsdMFutures,
            )),
            ("binance", "coin-m-futures") => Ok(ReferenceSourceBinding::Binance(
                BinanceReferenceSource::CoinMFutures,
            )),
            ("binance", "options") => Ok(ReferenceSourceBinding::Binance(
                BinanceReferenceSource::Options,
            )),
            ("binance", "equity") => Ok(ReferenceSourceBinding::Binance(
                BinanceReferenceSource::Equity,
            )),
            ("okx", "spot") => Ok(ReferenceSourceBinding::Okx(OkxProduct::Spot)),
            ("okx", "margin") => Ok(ReferenceSourceBinding::Okx(OkxProduct::Margin)),
            ("okx", "swap") => Ok(ReferenceSourceBinding::Okx(OkxProduct::Swap)),
            ("okx", "futures") => Ok(ReferenceSourceBinding::Okx(OkxProduct::Futures)),
            ("okx", "options") => Ok(ReferenceSourceBinding::Okx(OkxProduct::Option)),
            ("hyperliquid", "spot") => Ok(ReferenceSourceBinding::Hyperliquid(
                HyperliquidProduct::Spot,
            )),
            ("hyperliquid", "perpetual") => Ok(ReferenceSourceBinding::Hyperliquid(
                HyperliquidProduct::Perpetual,
            )),
            ("massive", "equity") => Ok(ReferenceSourceBinding::Massive(
                MassiveReferenceSource::Equity,
            )),
            ("massive", "options") => Ok(ReferenceSourceBinding::Massive(
                MassiveReferenceSource::Options,
            )),
            _ => Err(crate::domain::ReferenceError::Configuration(format!(
                "unsupported legacy Reference provider product {provider}/{product}"
            ))),
        })
        .collect()
}

fn legacy_default_endpoint(provider: &str) -> &'static str {
    match provider {
        "binance" => "https://api.binance.com",
        "okx" => "https://www.okx.com",
        "hyperliquid" => "https://api.hyperliquid.xyz/info",
        "massive" => "https://api.massive.com",
        _ => "",
    }
}

fn write_legacy_connection_profile(
    path: &Path,
    provider: &str,
    endpoint: &str,
    credential_id: &str,
    enabled: bool,
    products: &[&str],
) -> ReferenceResult<()> {
    fn quoted(value: &str) -> String {
        serde_json::to_string(value).expect("JSON string encoding is infallible")
    }
    let products = products
        .iter()
        .map(|value| quoted(value))
        .collect::<Vec<_>>()
        .join(", ");
    let content = format!(
        "version = 2\n\n[connection]\nconnection_id = {}\nprovider = {}\nenvironment = \"production\"\nendpoint = {}\ncredential_id = {}\nenabled = {enabled}\nproducts = [{products}]\npurposes = [\"reference-catalog\"]\n",
        quoted(provider),
        quoted(provider),
        quoted(endpoint),
        quoted(credential_id),
    );
    let temporary = path.with_extension(format!("toml.migrate-{}", std::process::id()));
    std::fs::write(&temporary, content)
        .and_then(|_| std::fs::rename(&temporary, path))
        .map_err(|error| {
            crate::domain::ReferenceError::Configuration(format!(
                "failed to migrate legacy Reference provider profile {}: {error}",
                path.display()
            ))
        })
}

#[cfg(test)]
mod legacy_migration_tests {
    use super::{ReferenceConfig, migrate_legacy_provider_configuration};
    use crate::services::storage::provider_sync_store::SqlxProviderSyncStore;

    #[tokio::test]
    async fn legacy_provider_migration_materializes_one_selected_product_without_overwrite() {
        let directory = tempfile::tempdir().unwrap();
        let workspace = kairos_workspace::Workspace::init(directory.path(), "legacy").unwrap();
        let config: ReferenceConfig = toml::from_str(
            r#"
            [providers.massive]
            enabled = true
            credential_id = "massive-readonly"
            endpoint = "https://reference.example.test"
            product = "equity"
            "#,
        )
        .unwrap();
        config.validate().unwrap();
        let database = directory.path().join("data/reference.sqlite");

        migrate_legacy_provider_configuration(&workspace, &database, &config)
            .await
            .unwrap();

        let profile = kairos_integration::composition::ProviderConnectionProfile::load(
            &kairos_integration::composition::ProviderConnectionProfile::canonical_root(
                workspace.root(),
            ),
            "massive",
        )
        .unwrap();
        assert_eq!(profile.products, ["equity"]);
        assert_eq!(profile.purposes, ["reference-catalog"]);
        let mut store = SqlxProviderSyncStore::open(&database).await.unwrap();
        let definitions = store.source_definitions().await.unwrap();
        assert_eq!(definitions.len(), 1);
        assert_eq!(definitions[0].source_id.as_str(), "massive-equity");
        assert_eq!(definitions[0].connection_id.as_deref(), Some("massive"));

        std::fs::write(
            kairos_integration::composition::ProviderConnectionProfile::canonical_root(
                workspace.root(),
            )
            .join("massive.toml"),
            "preserved",
        )
        .unwrap();
        migrate_legacy_provider_configuration(&workspace, &database, &config)
            .await
            .unwrap();
        assert_eq!(
            std::fs::read_to_string(
                kairos_integration::composition::ProviderConnectionProfile::canonical_root(
                    workspace.root(),
                )
                .join("massive.toml")
            )
            .unwrap(),
            "preserved"
        );
        assert_eq!(store.source_definitions().await.unwrap().len(), 1);
    }
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
    let workspace = config
        .workspace
        .as_ref()
        .map(kairos_workspace::workspace::Workspace::open)
        .transpose()
        .map_err(|error| crate::domain::ReferenceError::Provider(error.to_string()))?;
    let runtime_config = workspace
        .as_ref()
        .map(ReferenceConfig::load)
        .transpose()
        .map_err(crate::domain::ReferenceError::Configuration)?
        .map(|reference| reference.runtime)
        .unwrap_or_default();
    let tick_budget = runtime_config.tick_budget.to_domain()?;
    let workspace_id = workspace
        .as_ref()
        .map(kairos_workspace::workspace::Workspace::id)
        .unwrap_or("workspace:standalone");
    let mut application =
        ReferenceApplication::new("reference-actor", workspace_id, source_plan, store).await?;
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
