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
    for expected in [
        "quantity: String",
        "limit_price: Option<String>",
        "price: String",
    ] {
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
fn execution_does_not_reintroduce_cross_provider_product_aliases() {
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
                "cross-provider product alias {forbidden} leaked through {}",
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
        "capabilities::funding",
        "compose_binance_transfer",
        "TransferRequest",
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
    assert!(server.contains("participant_id: String"));
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
fn execution_reads_account_business_state_from_the_typed_mmap_view() {
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
    let projection = fs::read_to_string(root.join("dependencies/projection/mod.rs"))
        .expect("read Execution typed projections");
    assert!(projection.contains("SharedSnapshotReader"));
    assert!(projection.contains("metadata.applied_revision()"));
    assert!(projection.contains("ViewCompleteness::COMPLETE"));
    assert!(projection.contains("FreshnessState::FRESH"));
    assert!(projection.contains("struct DependencyProjectionRuntime"));
    assert!(projection.contains("impl Drop for DependencyProjectionRuntime"));
    assert!(!dependencies.contains("start_projection_workers"));
    assert!(!dependencies.contains("projection_workers"));
    assert!(!dependencies.contains("projection_stop"));
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
fn execution_reads_reference_business_state_through_the_contract_sqlite_client() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let projection =
        fs::read_to_string(root.join("src/services/dependencies/projection/mod.rs")).unwrap();
    let module = rust_source(&root.join("src/composition/connections"));
    assert!(projection.contains("ReferenceClient::connect"));
    assert!(module.contains("ReferenceClient::connect"));
    assert!(projection.contains("execution_snapshot()"));
    assert!(module.contains("execution_snapshot()"));
    assert!(!projection.contains("ReferenceViewReader"));
    assert!(!module.contains("ReferenceViewReader"));
    assert!(!projection.contains("ReferenceSqliteReader"));
    assert!(!module.contains("ReferenceSqliteReader"));
    assert!(!projection.contains("reference_markets_current"));
    assert!(!module.contains("reference_execution_accesses_current"));
}

#[test]
fn live_order_gateway_is_guarded_by_account_segment_writer_fencing() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let server = fs::read_to_string(root.join("src/bin/kairos-execution-server.rs"))
        .expect("read Execution server");
    let composition = rust_source(&root.join("src/composition"));
    assert!(server.contains("acquire_execution_writer_leases"));
    assert!(composition.contains("install_writer_fences"));
    assert!(composition.contains("configure_live_trading(!config.simulated"));
    assert!(server.contains("if simulated"));
    assert!(composition.contains("self.validate_writer(request)?;"));
    assert!(
        composition
            .matches("self.validate_writer(request)?;")
            .count()
            >= 2
    );
    assert!(composition.contains("WorkspaceFencedLease"));

    let application = rust_source(&root.join("src/application"));
    let process = fs::read_to_string(root.join("src/application/process/mod.rs"))
        .expect("read Execution process");
    assert!(application.contains("writer_recovery_ready = !enabled"));
    assert!(application.contains("complete_writer_reconciliation"));
    assert!(application.contains("blocked until writer takeover reconciliation completes"));
    assert!(process.contains("self.application.complete_writer_reconciliation()"));
}

#[test]
fn application_public_signatures_do_not_expose_internal_dependencies() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let source = rust_source(&root.join("src/application"));
    let forbidden = [
        "OrderEntryConnection",
        "OrderQueryConnection",
        "OrderEventSource",
        "AsyncOrderEntryConnection",
        "AsyncOrderQueryConnection",
        "AsyncOrderEventSource",
        "ExecutionStateStore",
        "ExecutionSimulator",
        "SimulatedAccountSettlement",
        "SharedExecutionSnapshotPublisher",
        "SharedIntentSnapshotPublisher",
        "AeronExecutionEventPublisher",
        "crate::services",
        "kairos_integration",
    ];

    for marker in ["pub fn ", "pub async fn "] {
        for tail in source.split(marker).skip(1) {
            let signature = tail.split('{').next().unwrap_or(tail);
            for dependency in forbidden {
                assert!(
                    !signature.contains(dependency),
                    "public Application signature exposes {dependency}: {signature}"
                );
            }
        }
    }

    let core = fs::read_to_string(root.join("src/application/core/mod.rs"))
        .expect("read Execution application facade");
    for method in [
        "assemble",
        "configure_execution_route",
        "configure_live_trading",
        "recover_risk_reservations",
    ] {
        assert!(
            core.contains(&format!("pub(crate) fn {method}")),
            "composition-only method must stay crate-private: {method}"
        );
    }

    let application_module = fs::read_to_string(root.join("src/application/mod.rs"))
        .expect("read Execution application module");
    assert!(!application_module.contains("pub use process"));
    assert!(application_module.contains("pub(crate) use process"));

    let lifecycle = fs::read_to_string(root.join("src/application/process/lifecycle.rs"))
        .expect("read Execution process lifecycle");
    for method in [
        "with_audit",
        "with_async_order_entry",
        "with_async_order_query",
        "with_async_execution_routes",
        "with_simulator",
        "with_simulated_account_settlement",
        "with_snapshot_publisher",
        "with_intent_snapshot_publisher",
        "with_event_publisher",
        "run",
    ] {
        let crate_private = format!("pub(crate) fn {method}");
        let crate_private_async = format!("pub(crate) async fn {method}");
        assert!(
            lifecycle.contains(&crate_private) || lifecycle.contains(&crate_private_async),
            "process wiring method must stay crate-private: {method}"
        );
    }
    let reconciliation =
        fs::read_to_string(root.join("src/application/core/reconciliation/mod.rs"))
            .expect("read Execution reconciliation");
    for method in [
        "take_execution_stream",
        "take_order_entry",
        "install_order_entry",
        "take_order_query",
        "install_order_query",
    ] {
        assert!(
            reconciliation.contains(&format!("pub(crate) fn {method}")),
            "process wiring method must stay crate-private: {method}"
        );
    }
}

#[test]
fn execution_actor_owns_order_lifecycle_state_and_submission_transitions() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let facade = fs::read_to_string(root.join("src/application/core/mod.rs"))
        .expect("read Execution application");
    let intent_use_cases = rust_source(&root.join("src/application/core/intents"));
    let order_use_cases = rust_source(&root.join("src/application/core/orders"));
    let application = format!("{facade}\n{intent_use_cases}\n{order_use_cases}");
    let application_fields = facade
        .split("pub struct ExecutionApplication {")
        .nth(1)
        .and_then(|value| value.split("impl ExecutionApplication").next())
        .expect("locate ExecutionApplication fields");
    let actor = rust_source(&root.join("src/services/actor"));
    for obsolete_application_field in [
        "orders: BTreeMap<",
        "commitments: BTreeMap<",
        "risk_reservations: BTreeMap<",
        "fills: Vec<ExecutionFill>",
        "unknown_remote_orders: BTreeMap<",
        "exchange_event_watermark_unix_nanos: u64",
        "intents: BTreeMap<",
        "intent_events: Vec<IntentEvent>",
        "pending_intent_events: Vec<IntentEvent>",
        "intent_idempotency: BTreeMap<",
    ] {
        assert!(
            !application_fields.contains(obsolete_application_field),
            "ExecutionApplication remains a duplicate state owner: {obsolete_application_field}"
        );
        assert!(actor.contains(obsolete_application_field));
    }
    assert!(application.contains("actor: crate::services::actor::ExecutionActor"));
    assert!(actor.contains("fn prepare_submission("));
    assert!(actor.contains("fn resize_commitment("));
    assert!(actor.contains("fn activate_submission("));
    assert!(actor.contains("fn set_risk_reservation_status("));
    assert!(application.contains("CommitmentStatus::Uncertain"));
    assert!(application.contains("RiskReservationSagaStatus::AuthorizePending"));
    assert!(application.contains("RiskReservationSagaStatus::Uncertain"));
    assert!(actor.contains("fn apply_order_entry_event("));
    assert!(actor.contains("fn mark_delivery_status("));
    assert!(actor.contains("fn record_fill("));
    assert!(!actor.contains("pub(crate) orders:"));
    assert!(!actor.contains("pub(crate) fills:"));
    assert!(actor.contains("fn reconcile_order("));
    assert!(actor.contains("fn record_unknown_remote_order("));
    assert!(!facade.contains("seen_exchange_events"));
    assert!(!facade.contains("exchange_event_order"));
    let process = fs::read_to_string(root.join("src/application/process/mod.rs"))
        .expect("read Execution process");
    assert!(!process.contains("seen_exchange_events:"));
    assert!(!process.contains("exchange_event_order:"));
    assert!(actor.contains("seen_exchange_events: HashSet<String>"));
    assert!(actor.contains("fn accept_exchange_event("));
    let model = rust_source(&root.join("src/application/model"));
    assert!(model.contains("pub struct SubmitOrder"));
    assert!(model.contains("pub enum ExecutionError"));
    assert!(!facade.contains("pub struct SubmitOrder"));
    assert!(!facade.contains("pub enum ExecutionError"));
    assert!(intent_use_cases.contains("pub fn submit_intent("));
    assert!(order_use_cases.contains("pub fn submit("));
}

#[test]
fn execution_rest_keeps_only_bounded_capability_queries_off_mmap() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let process = fs::read_to_string(root.join("src/application/process/mod.rs"))
        .expect("read Execution process");
    let control_root = root.join("src/services/control");
    let transport = fs::read_to_string(control_root.join("transport.rs"))
        .expect("read Execution control transport");
    let wire = fs::read_to_string(control_root.join("wire.rs"))
        .expect("read Execution control wire adapter");
    assert!(wire.contains("method == \"GET\" && path == \"/v1/routes\""));
    assert!(wire.contains("method == \"GET\" && path != kairos_workspace::runtime::HEALTH_PATH"));
    assert!(wire.contains("path == kairos_workspace::runtime::HEALTH_PATH"));
    assert!(!process.contains("\"/v1/open-orders\" =>"));
    assert!(!process.contains("\"/v1/events\" =>"));
    assert!(!process.contains("\"/v1/audit\" =>"));
    for transport_only in [
        "axum::",
        "body::to_bytes",
        "execution_http_handler",
        "StatusCode",
        "IntoResponse",
    ] {
        assert!(
            !process.contains(transport_only),
            "Execution application process retains raw HTTP transport: {transport_only}"
        );
        assert!(transport.contains(transport_only));
    }
    for transport_mapping in [
        "target.split_once('?')",
        "serde_json::from_str",
        "parse_operation(",
        "CommandEnvelope",
        "REST business queries are disabled",
    ] {
        assert!(
            !process.contains(transport_mapping),
            "Execution application process retains transport mapping: {transport_mapping}"
        );
        assert!(wire.contains(transport_mapping));
    }

    let contract = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("contract/src/control/client.rs"),
    )
    .expect("read Execution control contract");
    assert_eq!(contract.matches("\"GET\"").count(), 2);
    assert!(contract.contains("\"GET\", \"/v1/health\""));
    assert!(contract.contains("\"/v1/routes\""));
}

#[test]
fn execution_process_returns_typed_control_responses_without_json_wire_encoding() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let ingress = fs::read_to_string(root.join("src/application/process/ingress/mod.rs"))
        .expect("read Execution process ingress");
    let response = fs::read_to_string(root.join("src/services/control/response.rs"))
        .expect("read Execution control response encoder");
    let control = fs::read_to_string(root.join("src/services/control/mod.rs"))
        .expect("read Execution control transport");

    for forbidden in ["serde_json", "json!", "Value"] {
        assert!(
            !ingress.contains(forbidden),
            "Execution process still owns control wire encoding: {forbidden}"
        );
    }
    assert!(ingress.contains("Result<ControlResponse"));
    assert!(response.contains("serde_json::to_value"));
    assert!(control.contains("Sender<Result<ControlResponse"));
}

#[test]
fn execution_health_does_not_expose_business_state_or_diagnostics() {
    let source = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/application/process/ingress/mod.rs"),
    )
    .expect("read Execution process ingress");
    let health_start = source
        .find("ControlOperation::Health => {")
        .expect("Execution health response branch");
    let health_end = source[health_start..]
        .find("ControlOperation::AdvanceTime")
        .map(|offset| health_start + offset)
        .expect("next control branch after health");
    let health = &source[health_start..health_end];
    for forbidden in [
        "actor_id",
        "generation",
        "event_sequence",
        "order_count",
        "dependency_watermarks",
        "runtime_metrics",
        ".snapshot()",
    ] {
        assert!(
            !health.contains(forbidden),
            "Execution health leaks business state or diagnostics: {forbidden}"
        );
    }
    assert!(health.contains("writer_recovery_ready"));
    assert!(health.contains("ControlResponse::health"));
    let response = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/services/control/response.rs"),
    )
    .expect("read Execution health wire encoder");
    assert!(response.contains("order_event_routes"));
}

#[test]
fn execution_cli_reads_durable_business_state_from_mmap_and_routes_from_control() {
    let source = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/bin/kairos-execution-cli.rs"),
    )
    .expect("read Execution CLI");
    assert!(source.contains("ExecutionViewKind::CurrentExecution"));
    assert!(source.contains("frame.current_execution()?"));
    assert!(source.contains("query command routed to typed mmap"));
    assert!(source.contains("RestControlClient::new"));
    assert!(source.contains("/v1/routes"));
    assert!(!source.contains("compose_direct_execution_connections"));
    assert!(!source.contains("ExecutionApplication::with_dependencies"));
    assert!(!source.contains("Command::RemoteOpenOrders"));
    assert!(!source.contains("Command::RemoteHistory"));
    assert!(!source.contains("Command::RemoteInspect"));
    assert!(!source.contains("Command::StreamNext"));

    let schema = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../schemas/v2/execution/views/current_execution.fbs"),
    )
    .expect("read CurrentExecutionView schema");
    for required in [
        "orders:[OrderState]",
        "intents:[IntentState]",
        "fills:[Fill]",
        "order_events:[OrderLifecycleEventState]",
        "intent_events:[IntentLifecycleEventState]",
        "unknown_remote_orders:[UnknownRemoteOrderState]",
        "commitments:[OrderCommitmentState]",
        "risk_reservations:[RiskReservationSagaState]",
    ] {
        assert!(
            schema.contains(required),
            "missing current view field: {required}"
        );
    }
}

#[test]
fn execution_account_fact_publication_is_not_part_of_preflight() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let application = rust_source(&root.join("src/application"));
    let account_settlement =
        fs::read_to_string(root.join("src/services/simulation/account_settlement.rs"))
            .expect("read simulated Account settlement service");
    let publication = fs::read_to_string(root.join("src/application/process/publication.rs"))
        .expect("read Execution durable publication orchestration");
    let actor = rust_source(&root.join("src/services/actor"));

    for forbidden in ["prepare_order", "publish_order", "publish_fill"] {
        assert!(
            !application.contains(forbidden),
            "application dependency ports still own mixed hook {forbidden}"
        );
    }
    for forbidden in [
        "fn reserve_order",
        "fn reconcile_risk_reservation",
        "fn resize_order",
        "fn release_order",
        "fn consume_order",
    ] {
        assert!(
            !application.contains(forbidden),
            "application dependency ports still own Risk hook {forbidden}"
        );
    }
    assert!(!root.join("src/services/account_facts").exists());
    assert!(!account_settlement.contains("trait ExecutionAccountFacts"));
    assert!(!account_settlement.contains("publish_order"));
    assert!(!account_settlement.contains("publish_fill"));
    assert!(account_settlement.contains("apply_simulated_settlement"));
    assert!(publication.contains("pending_outbox"));
    assert!(publication.contains("simulated_settlement_fact"));
    assert!(
        publication.find("apply_fill").unwrap() < publication.find("acknowledge_outbox").unwrap()
    );
    for leaked in [
        "pub(crate) intents:",
        "pub(crate) intent_events:",
        "pub(crate) pending_intent_events:",
        "pub(crate) intent_idempotency:",
    ] {
        assert!(
            !actor.contains(leaked),
            "Actor state remains exposed: {leaked}"
        );
    }
    assert!(actor.contains("fn apply_intent_event"));
    assert!(actor.contains("fn attach_intent_plan_order"));
    assert!(actor.contains("fn refresh_intent_plan_progress"));

    let process_composition = fs::read_to_string(root.join("src/composition/process.rs"))
        .expect("read Execution process composition");
    assert!(!process_composition.contains("QueuedExecutionAccountFacts"));
    assert!(!process_composition.contains("with_account_facts"));
    assert!(process_composition.contains("with_simulated_account_settlement"));
    assert!(process_composition.contains("configure_execution_dependencies"));
    assert!(!process_composition.contains("QueuedExecutionRiskReservations"));

    let composition_dependencies =
        fs::read_to_string(root.join("src/services/dependencies/mod.rs"))
            .expect("read concrete Execution dependency readers");
    assert!(!root.join("src/application/preflight.rs").exists());
    assert!(!root.join("src/composition/preflight.rs").exists());
    assert!(!application.contains("attach_preflight"));
    assert!(!application.contains("trait ExecutionIntentPlanner"));
    assert!(!application.contains("trait ExecutionOrderAdmission"));
    assert!(!application.contains("pub fn attach_intent_planner"));
    assert!(!application.contains("pub fn attach_order_admission"));
    assert!(!application.contains("pub fn attach_risk_reservations"));
    assert!(!root.join("src/application/capabilities").exists());
    let dependency_composition =
        fs::read_to_string(root.join("src/composition/dependencies/mod.rs"))
            .expect("read Execution dependency composition");
    assert!(dependency_composition.contains("pub fn configure_execution_dependencies"));
    assert!(dependency_composition.contains("pub fn configure_simulated_risk"));
    let worker = fs::read_to_string(root.join("src/services/dependencies/workers/mod.rs"))
        .expect("read focused dependency workers");
    assert!(worker.contains("struct QueuedExecutionIntentPlanner"));
    assert!(worker.contains("struct QueuedExecutionOrderAdmission"));
    assert!(!worker.contains("ExecutionDependencyBackend"));
    assert!(!worker.contains("QueuedExecutionDependencyReader"));
    let risk_reservations = fs::read_to_string(root.join("src/services/risk/adapter.rs"))
        .expect("read concrete Risk reservation adapter");
    assert!(!composition_dependencies.contains("impl ExecutionRiskReservations"));
    assert!(!risk_reservations.contains("trait ExecutionRiskReservations"));
    assert!(risk_reservations.contains("impl SocketExecutionRiskReservations"));
    assert!(risk_reservations.contains("RiskViewReader::open"));
    assert!(risk_reservations.contains("ContractError::NotSent"));
    assert!(risk_reservations.contains("ContractError::Indeterminate"));
    for obsolete_map in [
        "reservations: BTreeMap<String, String>",
        "reservation_amounts:",
        "reservation_quantities:",
        "reservation_requests:",
    ] {
        assert!(
            !composition_dependencies.contains(obsolete_map),
            "dependency reader retains adapter-local Risk state: {obsolete_map}"
        );
    }
}

#[test]
fn execution_dependencies_follow_their_concrete_owner_modules() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/services");
    let dependencies = root.join("dependencies");

    for required in [
        "access/mod.rs",
        "order_admission/mod.rs",
        "planning/mod.rs",
        "projection/mod.rs",
        "workers/mod.rs",
    ] {
        assert!(
            dependencies.join(required).is_file(),
            "missing concrete dependency module: {required}"
        );
    }
    assert!(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("src/application/core/orders/admission/mod.rs")
        .is_file());
    assert!(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("src/services/simulation/account_settlement.rs")
        .is_file());
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
        "dependency_projection",
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
fn execution_application_uses_explicit_directory_modules() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/application");

    for required in [
        "core/mod.rs",
        "model/mod.rs",
        "model/command.rs",
        "model/query.rs",
        "model/result.rs",
        "model/event.rs",
        "model/snapshot.rs",
        "model/error.rs",
        "core/orders/mod.rs",
        "core/intents/mod.rs",
        "core/queries/mod.rs",
        "core/reconciliation/mod.rs",
        "process/mod.rs",
        "process/lifecycle.rs",
        "process/streams.rs",
        "process/gateways.rs",
        "process/readiness.rs",
        "process/recovery.rs",
        "process/publication.rs",
        "process/tests.rs",
        "backtest/mod.rs",
        "backtest/market.rs",
    ] {
        assert!(
            root.join(required).is_file(),
            "missing Execution application module: {required}"
        );
    }

    for obsolete in [
        "service.rs",
        "service",
        "facade.rs",
        "facade",
        "market_input.rs",
        "market_input",
        "capabilities",
    ] {
        assert!(
            !root.join(obsolete).exists(),
            "obsolete ambiguous application module remains: {obsolete}"
        );
    }
}

#[test]
fn execution_private_services_are_partitioned_by_capability() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/services");

    for required in [
        "actor/mod.rs",
        "actor/orders.rs",
        "actor/intents.rs",
        "actor/fills.rs",
        "actor/reconciliation.rs",
        "gateway/mod.rs",
        "gateway/entry.rs",
        "gateway/query.rs",
        "gateway/events.rs",
        "routing/mod.rs",
        "routing/route.rs",
        "routing/validation.rs",
        "routing/tests.rs",
        "persistence/mod.rs",
        "control/mod.rs",
        "control/transport.rs",
        "control/wire.rs",
        "control/response.rs",
        "simulation/mod.rs",
        "simulation/model.rs",
        "simulation/matching.rs",
    ] {
        assert!(
            root.join(required).is_file(),
            "missing private Execution service module: {required}"
        );
    }

    for obsolete in [
        "actor.rs",
        "gateway.rs",
        "routing.rs",
        "persistence.rs",
        "control_transport.rs",
        "simulator.rs",
    ] {
        assert!(
            !root.join(obsolete).exists(),
            "obsolete sibling service file remains: {obsolete}"
        );
    }
}

#[test]
fn execution_composition_root_only_aggregates_focused_modules() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/composition");
    let module = fs::read_to_string(root.join("mod.rs")).expect("read composition root");
    let connections =
        fs::read_to_string(root.join("connections/mod.rs")).expect("read connection composition");

    for required in [
        "mod connections;",
        "mod persistence;",
        "mod providers;",
        "mod dependencies;",
    ] {
        assert!(
            module.contains(required),
            "composition root is missing {required}"
        );
    }
    assert!(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("src/services/dependencies/mod.rs")
        .is_file());
    assert!(!module.contains("mod publication;"));
    assert!(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("src/services/publication/mod.rs")
        .is_file());
    for misplaced in [
        "BinanceSpotConnection::connect",
        "OkxConnection::connect",
        "IbkrConnection::connect",
        "impl ExecutionStateStore",
        "flatbuffers::FlatBufferBuilder",
    ] {
        assert!(
            !module.contains(misplaced),
            "composition root owns concrete implementation: {misplaced}"
        );
    }
    let routes = fs::read_to_string(root.join("connections/routes.rs"))
        .expect("read Execution route composition");
    assert!(routes.contains("pub fn compose_execution_routes"));
    assert!(!connections.contains("pub fn compose_execution_routes"));
    assert!(!connections.contains("pub struct FileExecutionStore"));
    assert!(!connections.contains("pub struct MemoryStateStore"));

    for required in [
        "connections/model.rs",
        "connections/direct.rs",
        "connections/entry.rs",
        "connections/query.rs",
        "connections/events.rs",
        "connections/routes.rs",
        "connections/writer_fence.rs",
        "dependencies/mod.rs",
        "connections/tests.rs",
    ] {
        assert!(
            root.join(required).is_file(),
            "missing focused Execution composition module: {required}"
        );
    }
    let services = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/services");
    for required in [
        "publication/mod.rs",
        "publication/snapshots.rs",
        "publication/events.rs",
        "publication/encoding.rs",
    ] {
        assert!(
            services.join(required).is_file(),
            "missing focused Execution service module: {required}"
        );
    }
}

#[test]
fn execution_provider_construction_is_partitioned_by_provider() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/composition");
    let connections =
        fs::read_to_string(root.join("connections/mod.rs")).expect("read connections module");

    for (provider, constructor) in [
        ("binance", "BinanceSpotConnection::connect"),
        ("okx", "OkxConnection::connect"),
        ("ibkr", "IbkrConnection::connect"),
    ] {
        let source = rust_source(&root.join(format!("providers/{provider}")));
        assert!(
            source.contains(constructor),
            "{provider} constructor is missing from its provider module"
        );
        assert!(
            !connections.contains(constructor),
            "{provider} constructor leaked into the connection aggregate"
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
