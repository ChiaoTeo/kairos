use std::fs;
use std::path::{Path, PathBuf};

fn rust_files(root: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    for entry in fs::read_dir(root).expect("read source directory") {
        let path = entry.expect("read source entry").path();
        if path.is_dir() {
            files.extend(rust_files(&path));
        } else if path.extension().and_then(|value| value.to_str()) == Some("rs") {
            files.push(path);
        }
    }
    files
}

fn rust_source(root: &Path) -> String {
    let mut paths = rust_files(root);
    paths.sort();
    paths
        .into_iter()
        .map(|path| fs::read_to_string(path).expect("read Rust source"))
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn execution_public_boundaries_do_not_expose_decimal_storage_parts() {
    let service_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let roots = [
        service_root.join("src/application"),
        service_root.join("src/bin"),
        service_root.join("contract/src"),
    ];

    for root in roots {
        for path in rust_files(&root) {
            let source = fs::read_to_string(&path).expect("read boundary source");
            for forbidden in [
                "pub quantity_mantissa",
                "pub quantity_scale",
                "pub price_mantissa",
                "pub price_scale",
            ] {
                assert!(
                    !source.contains(forbidden),
                    "public decimal storage part {forbidden} leaked through {}",
                    path.display()
                );
            }
        }
    }
}

#[test]
fn execution_cli_accepts_semantic_decimal_arguments() {
    let cli = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/bin/kairos-execution-cli.rs"),
    )
    .expect("read execution CLI");
    for expected in ["quantity: String", "limit_price: Option<String>"] {
        assert!(cli.contains(expected), "CLI is missing {expected}");
    }
    for forbidden in [
        "quantity_mantissa",
        "quantity_scale",
        "price_mantissa",
        "price_scale",
    ] {
        assert!(!cli.contains(forbidden), "CLI exposes {forbidden}");
    }
}

#[test]
fn execution_does_not_reintroduce_cross_execution_channel_aliases() {
    let source_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    for path in rust_files(&source_root) {
        let source = fs::read_to_string(&path).expect("read Execution source");
        for forbidden in [
            "RouteProduct",
            "product_matches",
            "\"usd-m-futures\" | \"swap\"",
            "\"swap\" | \"usd-m-futures\"",
            "\"coin-m-futures\" | \"futures\"",
            "\"futures\" | \"coin-m-futures\"",
        ] {
            assert!(
                !source.contains(forbidden),
                "cross-provider execution-channel alias {forbidden} leaked through {}",
                path.display()
            );
        }
    }
}

#[test]
fn execution_public_models_have_no_serde_compatibility_aliases() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    for source_root in [root.join("src"), root.join("contract/src")] {
        for path in rust_files(&source_root) {
            let source = fs::read_to_string(&path).expect("read Execution source");
            assert!(
                !source.contains("serde(alias"),
                "Execution compatibility alias leaked through {}",
                path.display()
            );
            assert!(
                !source.contains("alias ="),
                "Execution compatibility alias leaked through {}",
                path.display()
            );
        }
    }
}

#[test]
fn execution_does_not_own_treasury_money_operations_without_a_business_caller() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut production = String::new();
    production.push_str(&rust_source(&root.join("application")));
    production.push_str(&rust_source(&root.join("composition")));
    production.push_str(&rust_source(&root.join("bin")));
    for forbidden in [
        "FundingAllocation",
        "MoneyOperation",
        "capabilities::transfer",
        "capabilities::earn",
        "compose_binance_transfer",
        "AssetTransferRequest",
        "EarnSubscribeRequest",
        "EarnRedeemRequest",
        "WithdrawRequest",
        "RepayRequest",
    ] {
        assert!(
            !production.contains(forbidden),
            "Execution has taken ownership of Treasury operation {forbidden}"
        );
    }
}

#[test]
fn execution_server_uses_only_normalized_multi_route_configuration() {
    let server = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/bin/kairos-execution-server.rs"),
    )
    .expect("read Execution server");
    assert!(server.contains("normalized_config()"));
    assert!(!server.contains("normalized-config.json"));
    assert!(server.contains("broker_id: String"));
    for forbidden in [
        "routes_json:",
        "#[arg(long, default_value = \"main\")]",
        "#[arg(long, default_value = \"simulated\")]",
        "pub provider: String",
    ] {
        assert!(
            !server.contains(forbidden),
            "Execution server retains obsolete single-route surface: {forbidden}"
        );
    }
}

#[test]
fn execution_reads_account_business_state_from_the_typed_indexed_view() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/services");
    let dependencies = fs::read_to_string(root.join("dependencies/mod.rs"))
        .expect("read Execution dependency adapter");
    let access = fs::read_to_string(root.join("dependencies/access/mod.rs"))
        .expect("read Execution dependency access");
    let intent_planning = fs::read_to_string(root.join("dependencies/planning/mod.rs"))
        .expect("read Execution intent planning context");
    let order_admission = fs::read_to_string(root.join("dependencies/order_admission/mod.rs"))
        .expect("read Execution order admission context");
    let admission_policy = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("src/application/core/orders/admission/mod.rs"),
    )
    .expect("read Execution-owned admission policy");
    let dependency_state = fs::read_to_string(root.join("dependencies/state/mod.rs"))
        .expect("read Execution typed dependency states");
    assert!(dependency_state.contains("AccountClient"));
    assert!(dependency_state.contains("indexed_current("));
    assert!(dependency_state.contains(".observed_orders()"));
    assert!(dependency_state.contains("metadata.applied_event_sequence"));
    assert!(dependency_state.contains("reader.snapshot()"));
    assert!(dependency_state.contains("FreshnessState::FRESH"));
    assert!(dependency_state.contains("struct DependencyStateRuntime"));
    assert!(dependency_state.contains("impl Drop for DependencyStateRuntime"));
    assert!(!dependencies.contains("SocketExecutionDependencyContext"));
    assert!(!dependencies.contains("struct IntentPlanningContext"));
    assert!(intent_planning.contains("struct IntentPlanningContext"));
    assert!(intent_planning.contains("impl IntentPlanningContext"));
    assert!(!intent_planning.contains("ExecutionOrderAdmission"));
    assert!(!dependencies.contains("struct OrderAdmissionContext"));
    assert!(order_admission.contains("struct OrderAdmissionContext"));
    assert!(order_admission.contains("impl OrderAdmissionContext"));
    assert!(!order_admission.contains("ExecutionIntentPlanner"));
    assert!(!dependencies.contains("struct ExecutionDependencyAccess"));
    assert!(access.contains("struct ExecutionDependencyAccess"));
    assert!(!access.contains("impl ExecutionIntentPlanner for ExecutionDependencyAccess"));
    assert!(!access.contains("impl ExecutionOrderAdmission for ExecutionDependencyAccess"));
    assert!(!dependencies.contains("client.balances("));
    assert!(!dependencies.contains("client.positions("));
    assert!(!dependencies.contains(".positions(None)"));
    assert!(!dependencies.contains("strip_suffix(\"USDT\")"));
    assert!(!access.contains("strip_suffix(\"USDT\")"));
    assert!(order_admission.contains("market.quote_asset_id"));
    assert!(order_admission.contains("market.base_asset_id"));
    assert!(admission_policy.contains("fn validate_reference_rules"));
    assert!(admission_policy.contains("fn validate_pair_constraints"));
    assert!(!root.join("dependencies/admission").exists());
}

#[test]
fn execution_does_not_create_reference_contract_clients_inside_the_module() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let dependency_state =
        fs::read_to_string(root.join("src/services/dependencies/state/mod.rs")).unwrap();
    let module = rust_source(&root.join("src/composition/connections"));
    assert!(!dependency_state.contains("ReferenceClient::connect"));
    assert!(!module.contains("ReferenceClient::connect"));
    assert!(!dependency_state.contains("read_execution_snapshot"));
    assert!(!module.contains("read_execution_snapshot"));
    assert!(dependency_state.contains("reference_dependency_state"));
    assert!(!dependency_state.contains("ReferenceViewReader"));
    assert!(!module.contains("ReferenceViewReader"));
    assert!(!dependency_state.contains("ReferenceSqliteReader"));
    assert!(!module.contains("ReferenceSqliteReader"));
    assert!(!dependency_state.contains("reference_markets_current"));
    assert!(!module.contains("reference_execution_accesses_current"));
}

#[test]
fn execution_connected_facade_reads_indexed_current_view_and_routes_from_control() {
    let cli = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/bin/kairos-execution-cli.rs"),
    )
    .expect("read Execution CLI");
    let server = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/application/connected.rs"),
    )
    .expect("read Execution server facade");
    assert!(cli.contains("ConnectedExecutionApplication"));
    assert!(cli.contains("connected_execution_app("));
    assert!(!cli.contains("ExecutionControlRpcClient"));
    assert!(!cli.contains("client.current_execution("));
    assert!(!cli.contains("install_execution_connection("));
    assert!(server.contains("client.indexed_current(&self.identity)"));
    assert!(server.contains("current.ensure_ready()?"));
    assert!(server.contains(".with_order(order_id"));
    assert!(server.contains("ConfluxSystem::new()"));
    assert!(server.contains("install_execution_connection("));
    assert!(server.contains("execution_client("));
    assert!(server.contains("ExecutionControlRpcClient::routes"));
    assert!(server.contains("ExecutionControlRpcClient::order_audit"));
    assert!(!server.contains("ExecutionConnection::control_only"));
    assert!(!server.contains("ExecutionClient::connect"));
    assert!(!server.contains("\"execution_routes\""));
    assert!(!server.contains("RestControlClient::new"));
    assert!(!server.contains("/v1/routes"));
    assert!(!server.contains("compose_direct_execution_connections"));
    assert!(!server.contains("ExecutionApplication::with_dependencies"));
    assert!(!cli.contains("Command::RemoteOpenOrders"));
    assert!(!cli.contains("Command::RemoteHistory"));
    assert!(!cli.contains("Command::RemoteInspect"));
    assert!(!cli.contains("Command::StreamNext"));
    for removed in [
        "Self::Orders",
        "Self::OpenOrders",
        "Self::History",
        "Self::Status",
        "Self::Inspect",
        "Self::Events",
        "Self::Trace",
        "Self::Journal",
        "Self::Fills",
    ] {
        assert!(
            !cli.contains(removed),
            "removed connected path remains: {removed}"
        );
    }

    let schemas =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../schemas/v2/execution/views");
    for required in [
        "order_current.fbs",
        "intent_current.fbs",
        "algorithm_run_current.fbs",
        "commitment_current.fbs",
        "risk_reservation_current.fbs",
        "unknown_remote_order_current.fbs",
    ] {
        assert!(
            schemas.join(required).is_file(),
            "missing indexed entity schema: {required}"
        );
    }
    assert!(!schemas.join("current_execution.fbs").exists());
}

#[test]
fn execution_has_one_instance_partitioned_current_view_without_compatibility_roots() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let contract = fs::read_to_string(root.join("contract/src/view/indexed.rs"))
        .expect("read Execution indexed view contract");
    let launch = fs::read_to_string(root.join("src/composition/launch.rs"))
        .expect("read Execution composition");
    let publisher = fs::read_to_string(root.join("src/application/conflux.rs"))
        .expect("read Execution publisher");
    let schemas = root.join("../../../schemas/v2/execution/views");

    assert!(contract.contains("execution_indexed_schema_set"));
    assert!(contract.contains("EXECUTION_RESOURCE_EPOCH"));
    assert!(contract.contains("IndexedViewReader"));
    assert!(launch.contains("outputs().indexed.declare"));
    assert!(publisher.contains(".indexed"));
    assert!(!schemas.join("active_orders.fbs").exists());
    assert!(!schemas.join("active_intents.fbs").exists());
    assert!(!schemas.join("current_execution.fbs").exists());
}

#[test]
fn execution_dependencies_follow_their_concrete_owner_modules() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/services");
    let dependencies = root.join("dependencies");

    for required in [
        "access/mod.rs",
        "order_admission/mod.rs",
        "planning/mod.rs",
        "state/mod.rs",
        "workers/mod.rs",
    ] {
        assert!(
            dependencies.join(required).is_file(),
            "missing concrete dependency module: {required}"
        );
    }
    assert!(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("src/application/core/orders/admission/mod.rs")
            .is_file()
    );
    assert!(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("src/services/simulation/account_settlement.rs")
            .is_file()
    );
    let services = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/services");
    for required in ["risk/mod.rs", "risk/adapter.rs", "risk/worker.rs"] {
        assert!(
            services.join(required).is_file(),
            "missing concrete Risk service module: {required}"
        );
    }

    for obsolete in [
        "account_facts",
        "admission_rules",
        "dependency_worker",
        "risk_reservations",
        "risk_worker",
    ] {
        assert!(
            !root.join(obsolete).exists(),
            "obsolete sibling dependency module remains: {obsolete}"
        );
    }
}

#[test]
fn simulated_account_settlement_uses_the_durable_order_settlement_asset() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let account_settlement =
        fs::read_to_string(root.join("src/services/simulation/account_settlement.rs"))
            .expect("read simulated Account settlement service");
    let application = rust_source(&root.join("src/application"));
    let publisher = rust_source(&root.join("src/services/publication"));
    let schema = fs::read_to_string(root.join("../../../schemas/v2/execution/types/order.fbs"))
        .expect("read Execution order schema");

    assert!(!account_settlement.contains("settlement_asset: \"USDT\""));
    assert!(account_settlement.contains(".settlement_asset"));
    assert!(application.contains("commitment.settlement_asset = request"));
    assert!(publisher.contains("let settlement_asset = commitment"));
    assert!(schema.contains("settlement_asset:string"));
}

#[test]
fn execution_uses_one_contract_actor_and_conflux_owned_control() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let application = rust_source(&root.join("src/application"));
    let composition = rust_source(&root.join("src/composition"));
    assert!(application.contains("execution_control_rpc_conflux_actor"));
    assert!(application.contains("impl ConfluxActor for ExecutionApplication"));
    assert!(application.contains("async fn handle("));
    assert!(!root.join("src/application/process").exists());
    assert!(!root.join("src/services/control").exists());
    assert!(!root.join("src/composition/host.rs").exists());
    assert!(application.contains("ExecutionRpcActor"));
    assert!(application.contains("ExecutionRpcService"));
    assert!(composition.contains("with_json_rpc"));
    assert!(composition.contains("ExecutionControlRpcServer"));
    assert!(!composition.contains("with_http_control"));
    assert!(!composition.contains("ExecutionHttpControl"));
    let manifest = fs::read_to_string(root.join("Cargo.toml")).unwrap();
    assert!(!manifest.contains("axum.workspace"));
    assert!(!manifest.contains("kairos-transport"));
    for forbidden in ["axum::", "UnixListener", "TcpListener"] {
        assert!(
            !application.contains(forbidden),
            "Execution Application depends on transport type {forbidden}"
        );
    }
}

#[test]
fn execution_connections_are_installed_in_exact_conflux_collections() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let routes = fs::read_to_string(root.join("src/composition/connections/routes.rs"))
        .expect("read routes");
    let actor =
        fs::read_to_string(root.join("src/application/conflux.rs")).expect("read Conflux actor");
    for family in [
        "binance_spot_rest",
        "binance_usdm_rest",
        "binance_coinm_rest",
        "binance_options_rest",
        "binance_stocks_rest",
        "okx_private_rest",
        "ibkr_order",
    ] {
        assert!(routes.contains(family), "missing typed create for {family}");
        assert!(
            actor.contains(family),
            "missing typed Actor access for {family}"
        );
    }
    assert!(!root.join("src/services/gateway/managed.rs").exists());
    assert!(!actor.contains("build_managed_gateways"));
    assert!(!actor.contains("into_connection"));
}

#[test]
fn execution_publication_is_owned_by_conflux_resources() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let actor = fs::read_to_string(root.join("src/application/conflux.rs")).expect("read actor");
    let services = rust_source(&root.join("src/services/publication"));
    assert!(actor.contains("outputs()"));
    assert!(actor.contains(".aeron"));
    assert!(actor.contains(".indexed"));
    assert!(!actor.contains("try_with"));
    assert!(actor.contains("flush_durable_events"));
    assert!(!services.contains("SharedExecutionSnapshotPublisher"));
    assert!(!services.contains("SharedIntentSnapshotPublisher"));
    assert!(!services.contains("AeronExecutionEventPublisher"));
}
