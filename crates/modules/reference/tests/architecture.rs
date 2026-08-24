use std::path::PathBuf;

fn source(path: &str) -> String {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    std::fs::read_to_string(root.join(path)).expect("read Reference source")
}

fn reference_src_files() -> Vec<PathBuf> {
    fn collect(path: PathBuf, files: &mut Vec<PathBuf>) {
        if path.is_dir() {
            for entry in std::fs::read_dir(path).expect("read Reference source directory") {
                collect(entry.expect("read Reference source entry").path(), files);
            }
            return;
        }
        if path.extension().and_then(|extension| extension.to_str()) == Some("rs") {
            files.push(path);
        }
    }

    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    collect(root, &mut files);
    files
}

#[test]
fn reference_application_layout_separates_use_cases_cli_and_process_facades() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let application = source("src/application/mod.rs");
    let services = source("src/services/mod.rs");

    assert!(application.contains("mod cli;"));
    assert!(application.contains("mod process;"));
    assert!(application.contains("mod commands;"));
    assert!(application.contains("mod queries;"));
    assert!(root.join("src/application/cli/local.rs").is_file());
    assert!(root.join("src/application/cli/remote.rs").is_file());
    assert!(
        root.join("src/application/process/control/conflux.rs")
            .is_file()
    );
    assert!(
        root.join("src/application/process/runtime/tick.rs")
            .is_file()
    );
    assert!(
        root.join("src/application/process/control/status.rs")
            .is_file()
    );
    assert!(root.join("src/application/queries/model.rs").is_file());
    assert!(services.contains("pub(crate) mod diagnostics;"));

    for obsolete in [
        "src/application/app.rs",
        "src/application/cli.rs",
        "src/application/connected.rs",
        "src/application/conflux.rs",
        "src/application/diagnostics.rs",
        "src/application/runtime.rs",
        "src/application/runtime_status.rs",
        "src/application/read_model.rs",
        "src/application/source_control.rs",
        "src/application/startup.rs",
        "src/application/tick.rs",
    ] {
        assert!(
            !root.join(obsolete).exists(),
            "obsolete flat application file remains: {obsolete}"
        );
    }
}

#[test]
fn reference_control_transport_is_framework_owned() {
    let manifest = source("Cargo.toml");
    let server = source("src/bin/kairos-reference-server.rs");
    let contract = source("contract/src/control/mod.rs");
    let service = source("contract/src/control/service.rs");
    assert!(!manifest.contains("axum.workspace"));
    assert!(!manifest.contains("kairos-transport"));
    assert!(!server.contains("with_http_control"));
    assert!(!server.contains("ReferenceHttpControl"));
    assert!(
        !PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("contract/src/control/http.rs")
            .exists()
    );
    assert!(contract.contains("ReferenceControlRpcClient"));
    assert!(contract.contains("ReferenceControlRpcServer"));
    assert!(service.contains("#[conflux_rpc(namespace = \"reference\")]"));
    for forbidden in ["axum::", "UnixListener", "TcpListener"] {
        assert!(!server.contains(forbidden));
    }
}

#[test]
fn reference_application_enters_sources_through_workflow_language() {
    for path in [
        "src/application/mod.rs",
        "src/application/process/runtime/tick.rs",
        "src/application/process/control/conflux.rs",
        "src/application/process/control/source.rs",
        "src/services/actor.rs",
    ] {
        let text = source(path);
        assert!(
            !text.contains("fetch_catalog"),
            "Application/actor layer must not use legacy catalog-fetch source entrypoints: {path}"
        );
    }

    let actor = source("src/services/actor.rs");
    assert!(actor.contains("advance_workflow_with_budget"));
    assert!(actor.contains("advance_source_with_budget"));

    let workflow = source("src/services/sources/workflow.rs");
    assert!(workflow.contains("async fn advance_workflow"));
    assert!(workflow.contains("async fn advance_workflow_step"));
    assert!(workflow.contains("async fn fetch_catalog"));
    assert!(workflow.contains("Provider implementations may still fetch"));

    for path in reference_src_files() {
        let text = std::fs::read_to_string(&path).expect("read Reference source file");
        assert!(
            !text.contains("ProviderUpdate"),
            "source workflow output must be named SourceUpdate, not ProviderUpdate: {}",
            path.display()
        );
        assert!(
            !text.contains("CompositeSource"),
            "provider fan-in source must not regress to the old CompositeSource name: {}",
            path.display()
        );
        assert!(
            !text.contains("activate_public_source_definition")
                && !text.contains("deactivate_public_source_definition")
                && !text.contains("public_source_connection_key")
                && !text.contains("dynamic_public_source_definition"),
            "dynamic source adapter activation must not be named public-only: {}",
            path.display()
        );
        assert!(
            !text.contains("services::source::") && !text.contains("services/source.rs"),
            "internal callers must use services::sources, not old services::source: {}",
            path.display()
        );
        for forbidden in [
            "ProviderFactory",
            "SourceFactory",
            "ConnectionFactory",
            "FactoryRegistry",
            "ProviderAdapterRegistry",
            "provider_factory",
            "source_factory",
            "connection_factory",
            "factory_registry",
        ] {
            assert!(
                !text.contains(forbidden),
                "Reference provider activation must stay workflow-specific until a real lower-level boundary exists: {} contains {forbidden}",
                path.display()
            );
        }
    }
    assert!(
        !PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("src/services/source.rs")
            .exists()
    );
}

#[test]
fn reference_connections_enter_through_exact_conflux_collections() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let plan = std::fs::read_to_string(root.join("src/services/providers/plan.rs"))
        .expect("read Reference provider plan");
    for collection in [
        ".binance_spot_rest",
        ".binance_usdm_rest",
        ".binance_coinm_rest",
        ".binance_options_rest",
        ".binance_stocks_rest",
        ".okx_public_rest",
        ".hyperliquid_info_rest",
        ".massive_rest",
    ] {
        assert!(plan.contains(collection), "missing {collection}");
    }
    assert!(plan.contains("ConnectionCollections<'_>"));
    assert!(!plan.contains("ConfluxSystem"));

    for path in [
        "src/application/mod.rs",
        "src/application/process/control/conflux.rs",
        "src/services/actor.rs",
        "src/services/sources/mod.rs",
        "src/services/providers/fan_in.rs",
        "src/services/providers/binance.rs",
        "src/services/providers/hyperliquid.rs",
        "src/services/providers/massive.rs",
        "src/services/providers/okx.rs",
    ] {
        let text = source(path);
        assert!(
            !text.contains("&mut kairos_conflux::ConfluxSystem"),
            "Reference runtime must receive typed connection collections, not ConfluxSystem: {path}"
        );
        assert!(
            !text.contains("with_system"),
            "legacy whole-System operation remains in {path}"
        );
    }
    let actor =
        std::fs::read_to_string(root.join("src/services/actor.rs")).expect("read Reference actor");
    assert!(actor.contains("type ActorReferenceSource = ConfiguredReferenceSource"));
    assert!(actor.contains("#[cfg(test)]\ntype ActorReferenceSource = Box<dyn ReferenceSource>"));
    assert!(actor.contains("activate_sources"));
}

#[test]
fn reference_provider_and_storage_paths_are_async_first() {
    let providers = [
        "src/services/providers/binance.rs",
        "src/services/providers/hyperliquid.rs",
        "src/services/providers/massive.rs",
        "src/services/providers/okx.rs",
    ]
    .into_iter()
    .map(source)
    .collect::<String>();
    assert!(!providers.contains("::blocking"));
    assert!(!providers.contains("blocking_instrument_catalog"));
    assert!(!providers.contains("std::thread::Builder"));

    let services = source("src/services/mod.rs");
    assert!(
        services.contains("mod providers"),
        "provider adapters are private Reference services"
    );

    let storage = source("src/services/sqlx_storage.rs");
    assert!(!storage.contains("tokio::runtime::Runtime"));
    assert!(!storage.contains("block_on("));

    let server = source("src/bin/kairos-reference-server.rs");
    assert!(!server.contains("block_in_place"));
}

#[test]
fn concrete_provider_adapters_and_fan_in_remain_separate_service_units() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let providers = root.join("src/services/providers");
    for unit in [
        "activation.rs",
        "binance.rs",
        "credentials.rs",
        "fan_in.rs",
        "hyperliquid.rs",
        "massive.rs",
        "okx.rs",
        "plan.rs",
        "tests.rs",
    ] {
        assert!(
            providers.join(unit).is_file(),
            "missing provider unit: {unit}"
        );
    }
    let module = source("src/services/providers/mod.rs");
    assert!(!module.contains("impl ReferenceSource for Binance"));
    assert!(!module.contains("impl ReferenceSource for Okx"));
    assert!(!module.contains("impl ReferenceSource for Hyperliquid"));
    assert!(!module.contains("impl ReferenceSource for Massive"));
    assert!(!module.contains("impl<S> ReferenceSource for ProviderFanInSource"));

    let activation = source("src/services/providers/activation.rs");
    assert!(activation.contains("activate_runtime_source_definition"));
    assert!(activation.contains("deactivate_runtime_source_definition"));
    assert!(activation.contains("is_scoped_massive_options_definition"));
    let credentials = source("src/services/providers/credentials.rs");
    assert!(credentials.contains("struct ReferenceCredentialResolver"));
    let plan = source("src/services/providers/plan.rs");
    assert!(plan.contains("struct ReferenceSourcePlan"));
    assert!(!plan.contains("activate_runtime_source_definition"));
}

#[test]
fn reference_domain_classification_is_not_unconstrained_text() {
    let entities = source("src/domain/entities.rs");
    for forbidden in [
        "asset_class: String",
        "instrument_type: String",
        "pub product_family: Option<String>",
        "pub provider_segment: Option<String>",
        "market_type: String",
        "asset_type: Option<String>",
        "pub provider_id: String",
        "pub product_family: String",
    ] {
        assert!(
            !entities.contains(forbidden),
            "Reference domain classification regressed to raw text: {forbidden}"
        );
    }
    assert!(entities.contains("asset_class: AssetClass"));
    assert!(entities.contains("instrument_type: InstrumentKind"));
    assert!(entities.contains("instrument_kind: InstrumentKind"));
    assert!(entities.contains("pub exchange_id: ExchangeId"));
    assert!(!entities.contains("pub struct Entity"));
    assert!(!entities.contains("EntityKind"));
    assert!(!entities.contains("exchange_type"));
    let source_definition = entities
        .split("pub struct ReferenceSourceDefinition")
        .nth(1)
        .expect("Reference source definition")
        .split("}\n")
        .next()
        .expect("Reference source definition body");
    assert!(!source_definition.contains("pub source_id: String"));
    assert!(source_definition.contains("pub source_id: ReferenceSourceId"));
    assert!(source_definition.contains("pub provider_id: Provider"));
    assert!(!source_definition.contains("provider_segment"));
    assert!(source_definition.contains("pub credential_binding: Option<SourceCredentialBinding>"));
    assert!(!source_definition.contains("pub credential_binding: Option<String>"));
}

#[test]
fn reference_control_is_jsonrpc_service_first() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let server = source("src/bin/kairos-reference-server.rs");
    let actor = source("src/application/process/control/conflux.rs");
    let application = source("src/application/mod.rs");
    let service = source("contract/src/control/service.rs");
    let contract_manifest = source("contract/Cargo.toml");
    let types = source("contract/src/control/types.rs");
    assert!(!root.join("src/application/rpc.rs").exists());
    assert!(!server.contains("ReferenceHttpControl"));
    assert!(!server.contains("with_http_control"));
    assert!(server.contains("with_json_rpc"));
    assert!(!types.contains("ReferenceRestRequest"));
    assert!(!types.contains("ReferenceRestResponse"));
    assert!(service.contains("async fn health"));
    assert!(service.contains("async fn refresh"));
    assert!(service.contains("async fn publish"));
    assert!(service.contains("async fn upsert_asset"));
    assert!(!service.contains("kairos_conflux"));
    assert!(service.contains("#[conflux_rpc(namespace = \"reference\")]"));
    assert!(!contract_manifest.contains("kairos-conflux"));
    assert!(!server.contains("mpsc::channel"));
    assert!(!server.contains("oneshot::channel"));
    assert!(!application.contains("conflux_json_rpc_actor_for"));
    assert!(application.contains("reference_control_rpc_conflux_actor"));
    assert!(application.contains("pub trait ReferenceRpcActor"));
    assert!(application.contains("service ReferenceRpcService"));
    assert!(!actor.contains("impl ReferenceControlRpcActor for ReferenceApplication"));
    assert!(actor.contains("impl ConfluxActor for ReferenceApplication"));
    assert!(actor.contains("impl ReferenceRpcActor for ReferenceApplication"));
    assert!(actor.contains("async fn health"));
    assert!(actor.contains("async fn refresh"));
    assert!(!actor.contains("async fn rpc_health"));
    assert!(!application.contains("trait ReferenceControlRpcActor"));
    assert!(!actor.contains("pub struct ReferenceRpcService"));
    assert!(!actor.contains("impl ReferenceControlRpcServer"));
    let contract = source("contract/src/control/types.rs");
    let health = contract
        .split("pub struct ReferenceHealthResponse")
        .nth(1)
        .expect("Reference health response")
        .split("pub struct ReferenceRuntimeStatusResponse")
        .next()
        .expect("Reference health response body");
    for forbidden in [
        "actor_id",
        "generation",
        "event_sequence",
        "market_count",
        "outbox_depth",
        "control_queue_depth",
        "last_attempt_unix_nanos",
        "last_success_unix_nanos",
        "consecutive_failures",
    ] {
        assert!(
            !health.contains(forbidden),
            "Reference health leaks {forbidden}"
        );
    }
}

#[test]
fn conflux_adapter_does_not_own_publication_loop_details() {
    let conflux = source("src/application/process/control/conflux.rs");
    let publication = source("src/application/process/runtime/delivery.rs");
    assert!(conflux.contains("self.publish_pending_to_outputs(context).await"));
    for forbidden in [
        "DEFAULT_PUBLICATION_BATCH_LIMIT",
        "REFERENCE_OUTPUT_STREAM",
        ".outputs()",
        "pending_publication_count()",
        "pending_publications(batch_limit)",
        "acknowledge_publications(&event_ids)",
    ] {
        assert!(
            !conflux.contains(forbidden),
            "Conflux adapter must delegate publication loop details to application/publication.rs: {forbidden}"
        );
        assert!(
            publication.contains(forbidden),
            "publication loop detail should live in application/publication.rs: {forbidden}"
        );
    }
    assert!(publication.contains(".aeron"));
    assert!(publication.contains(".publish(REFERENCE_OUTPUT_STREAM, publication.payload())"));
}

#[test]
fn conflux_adapter_does_not_own_startup_sequence_details() {
    let conflux = source("src/application/process/control/conflux.rs");
    let startup = source("src/application/process/runtime/startup.rs");
    assert!(conflux.contains("self.start_runtime(context).await"));
    for (forbidden, required) in [
        ("activate_sources", "activate_sources"),
        ("initial_refresh", "initial_refresh"),
        (
            "stage = \"publish_pending\"",
            "log_runtime_stage_started(\"publish_pending\")",
        ),
        ("spawn_timer", "spawn_timer"),
        (
            "reference_runtime_stage_started",
            "reference_runtime_stage_started",
        ),
        (
            "reference_runtime_stage_completed",
            "reference_runtime_stage_completed",
        ),
        (
            "reference_initial_refresh_deferred",
            "reference_initial_refresh_deferred",
        ),
    ] {
        assert!(
            !conflux.contains(forbidden),
            "Conflux started hook must delegate startup sequence details to application/startup.rs: {forbidden}"
        );
        assert!(
            startup.contains(required),
            "startup sequence detail should live in application/startup.rs: {required}"
        );
    }
}

#[test]
fn conflux_adapter_does_not_own_timer_tick_sequence_details() {
    let conflux = source("src/application/process/control/conflux.rs");
    let tick = source("src/application/process/runtime/tick.rs");
    assert!(conflux.contains("self.advance_timer_tick(context)"));
    assert!(conflux.contains("ConfluxEvent::System(SystemEvent::Timer"));
    for forbidden in [
        "ReferenceTickTrigger::Timer",
        "reference_refresh_failed",
        "Reference retains its last durable catalog",
        ".advance_sources_with_trigger(&mut context.connections(), ReferenceTickTrigger::Timer)",
    ] {
        assert!(
            !conflux.contains(forbidden),
            "Conflux timer hook must delegate timer tick details to application/tick.rs: {forbidden}"
        );
        assert!(
            tick.contains(forbidden),
            "timer tick detail should live in application/tick.rs: {forbidden}"
        );
    }
}

#[test]
fn conflux_adapter_does_not_own_source_control_mapping_details() {
    let conflux = source("src/application/process/control/conflux.rs");
    let source_control = source("src/application/process/control/source.rs");
    for forbidden in [
        "domain_source_desired_state",
        "domain_source_definition",
        "domain_source_scope",
        "source_scope_subject",
        "ReferenceSourceDesiredState",
        "SourceCredentialBinding",
        "MassiveOptionsCoverageSource",
    ] {
        assert!(
            !conflux.contains(forbidden),
            "Conflux RPC adapter must delegate source control mapping/workflow details to application/source_control.rs: {forbidden}"
        );
        assert!(
            source_control.contains(forbidden),
            "source control mapping/workflow detail should live in application/source_control.rs: {forbidden}"
        );
    }
}

#[test]
fn workspace_configuration_is_provider_first_and_hides_source_bindings() {
    let config = source("src/composition/config.rs");
    assert!(config.contains("pub struct ReferenceProviders"));
    assert!(config.contains("pub massive: CredentialedReferenceProvider"));
    assert!(config.contains("pub refresh_interval_seconds: Option<u64>"));
    for forbidden in [
        "BTreeMap",
        "ReferenceSourceBinding",
        "source_id",
        "sync_policy",
        "provider_product",
        "provider_segment",
    ] {
        assert!(
            !config.contains(forbidden),
            "workspace config must not expose advanced source detail: {forbidden}"
        );
    }

    let binding = source("src/services/providers/binding.rs");
    assert!(binding.contains("pub(crate) enum ReferenceSourceBinding"));
    assert!(!binding.contains("pub enum ReferenceSourceBinding"));
    for hierarchy in [
        "Binance(BinanceReferenceSource)",
        "Okx(OkxProduct)",
        "Hyperliquid(HyperliquidProduct)",
        "Massive(MassiveReferenceSource)",
        "pub(crate) enum BinanceReferenceSource",
        "pub(crate) enum MassiveReferenceSource",
    ] {
        assert!(
            binding.contains(hierarchy),
            "production source binding must preserve provider hierarchy: {hierarchy}"
        );
    }

    let contract = source("contract/src/control/types.rs");
    assert!(contract.contains("tag = \"provider\", content = \"source\""));
    assert!(!contract.contains("MassiveOptions,"));

    let cli = source("src/bin/kairos-reference-cli.rs");
    let parser = cli
        .split("fn parse_source_binding")
        .nth(1)
        .expect("advanced source binding parser")
        .split("fn parse_source_desired_state")
        .next()
        .expect("advanced source binding parser body");
    assert!(parser.contains("(\"massive\", \"options\")"));
    assert!(!parser.contains("\"massive-options\" =>"));
}

#[test]
fn conflux_adapter_does_not_own_health_response_mapping_details() {
    let conflux = source("src/application/process/control/conflux.rs");
    let runtime_status = source("src/application/process/control/status.rs");
    assert!(conflux.contains("Ok(self.contract_health().await)"));
    for forbidden in [
        "ReferenceProviderHealth",
        "ReferenceHealthStatus",
        "ReferenceProviderStatus",
        "SourceRuntimePhase",
        "contract_provider_health_status",
    ] {
        assert!(
            !conflux.contains(forbidden),
            "Conflux health RPC must delegate health response mapping to application/runtime_status.rs: {forbidden}"
        );
        assert!(
            runtime_status.contains(forbidden),
            "health response mapping detail should live in application/runtime_status.rs: {forbidden}"
        );
    }
}

#[test]
fn reference_uses_sqlite_as_its_only_current_fact_store() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let composition = source("src/composition/mod.rs");
    let server = source("src/bin/kairos-reference-server.rs");
    let actor = source("src/services/actor.rs");
    assert!(!composition.contains("MmapReferenceViewPublisher"));
    assert!(!composition.contains("ReferenceViewKey"));
    assert!(!server.contains("ReferenceCurrentViewPublisher"));
    assert!(!server.contains("current_view_publisher"));
    assert!(!server.contains("snapshot_publish_failed"));
    assert!(actor.contains("SqlxCatalogStore"));
    assert!(!actor.contains("dyn CatalogStore"));
    assert!(!root.join("src/services/store.rs").exists());

    let schemas = root.join("../../../schemas/v2/reference");
    assert!(!schemas.join("views/reference_latest.fbs").exists());
    assert!(!schemas.join("views/reference_current_view.fbs").exists());
    assert!(!schemas.join("types/financial_product.fbs").exists());
    assert!(!schemas.join("types/provider.fbs").exists());
    assert!(!schemas.join("types/broker.fbs").exists());
    assert!(schemas.join("types/exchange.fbs").exists());
    assert!(!schemas.join("types/entity.fbs").exists());
}

#[test]
fn administrative_writes_enter_through_application_commands() {
    let application = source("src/application/mod.rs");
    let commands = source("src/application/commands.rs");
    let contract = source("contract/src/control/types.rs");
    let server = source("src/bin/kairos-reference-server.rs");
    assert!(!application.contains("command: UpsertAssetCommand"));
    assert!(!application.contains("command: UpsertInstrumentCommand"));
    assert!(!application.contains("command: UpsertListingCommand"));
    assert!(commands.contains("command: UpsertAssetCommand"));
    assert!(commands.contains("command: UpsertInstrumentCommand"));
    assert!(commands.contains("command: UpsertListingCommand"));
    assert!(commands.contains("pub use kairos_reference_contract"));
    assert!(contract.contains("pub struct UpsertAssetRequest"));
    assert!(contract.contains("pub struct UpsertInstrumentRequest"));
    assert!(contract.contains("pub struct UpsertListingRequest"));
    for domain_payload in [
        "from_str::<Asset>",
        "from_str::<Instrument>",
        "from_str::<Listing>",
    ] {
        assert!(
            !server.contains(domain_payload),
            "transport must not deserialize a domain exchange: {domain_payload}"
        );
    }
}

#[test]
fn broker_and_data_provider_products_do_not_invent_canonical_venues() {
    let binance = source("src/services/providers/binance.rs");
    let equity_mapping = binance
        .split("pub(super) fn binance_equity_provider_catalog")
        .nth(1)
        .expect("Binance Equity mapping")
        .split("pub(super) fn binance_provider_catalog")
        .next()
        .expect("Binance Equity mapping body");
    for forbidden in [
        "exchange:binance",
        "listing:binance:equity",
        "market:binance:equity",
        "catalog.listings.push",
        "catalog.markets.push",
    ] {
        assert!(
            !equity_mapping.contains(forbidden),
            "Binance Equity broker facts must not create canonical venue facts: {forbidden}"
        );
    }

    let massive = source("src/services/providers/massive.rs");
    for forbidden in ["listing:massive", "market:massive"] {
        assert!(
            !massive.contains(forbidden),
            "Massive data-provider identity must not become canonical identity: {forbidden}"
        );
    }
    assert!(massive.contains("\"OPRA\" => None"));
    assert!(massive.contains("\"BATO\" => Some(\"exchange:cboe-bzx-options\".into())"));
}
