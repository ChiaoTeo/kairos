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
    for relative in [
        "application/service.rs",
        "application/service/intent_use_cases.rs",
        "application/service/order_use_cases.rs",
        "application/model.rs",
        "application/process.rs",
        "composition/mod.rs",
        "bin/kairos-execution-server.rs",
        "bin/kairos-execution-cli.rs",
    ] {
        production
            .push_str(&fs::read_to_string(root.join(relative)).expect("read Execution source"));
    }
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
    assert!(server.contains("normalized-config.json"));
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
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/composition");
    let dependencies = fs::read_to_string(root.join("dependencies.rs"))
        .expect("read Execution dependency adapter");
    let access = fs::read_to_string(root.join("dependencies/access.rs"))
        .expect("read Execution dependency access");
    let intent_planning = fs::read_to_string(root.join("dependencies/intent_planning.rs"))
        .expect("read Execution intent planning context");
    let order_admission = fs::read_to_string(root.join("dependencies/order_admission.rs"))
        .expect("read Execution order admission context");
    let projection = fs::read_to_string(root.join("dependency_projection.rs"))
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
    assert!(intent_planning.contains("impl ExecutionIntentPlanner for IntentPlanningContext"));
    assert!(!intent_planning.contains("ExecutionOrderAdmission"));
    assert!(!dependencies.contains("struct OrderAdmissionContext"));
    assert!(order_admission.contains("struct OrderAdmissionContext"));
    assert!(order_admission.contains("impl ExecutionOrderAdmission for OrderAdmissionContext"));
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
}

#[test]
fn execution_reads_reference_business_state_from_the_typed_mmap_view() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let projection =
        fs::read_to_string(root.join("src/composition/dependency_projection.rs")).unwrap();
    let module = fs::read_to_string(root.join("src/composition/mod.rs")).unwrap();
    assert!(projection.contains("ReferenceViewReader::open"));
    assert!(module.contains("ReferenceViewReader::open"));
    assert!(!projection.contains("ReferenceSqliteReader"));
    assert!(!module.contains("ReferenceSqliteReader"));
}

#[test]
fn live_order_gateway_is_guarded_by_account_segment_writer_fencing() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let server = fs::read_to_string(root.join("src/bin/kairos-execution-server.rs"))
        .expect("read Execution server");
    let composition = fs::read_to_string(root.join("src/composition/mod.rs"))
        .expect("read Execution composition");
    assert!(server.contains("acquire_execution_writer_leases"));
    assert!(server.contains("install_writer_fences"));
    assert!(server.contains("configure_live_trading(!simulated"));
    assert!(server.contains("if simulated"));
    assert!(composition.contains("self.validate_writer(request)?;"));
    assert!(
        composition
            .matches("self.validate_writer(request)?;")
            .count()
            >= 2
    );
    assert!(composition.contains("WorkspaceFencedLease"));

    let mut application = fs::read_to_string(root.join("src/application/service.rs"))
        .expect("read Execution application");
    application.push_str(
        &fs::read_to_string(root.join("src/application/service/order_use_cases.rs"))
            .expect("read Execution order use cases"),
    );
    let process = fs::read_to_string(root.join("src/application/process.rs"))
        .expect("read Execution process");
    assert!(application.contains("writer_recovery_ready = !enabled"));
    assert!(application.contains("complete_writer_reconciliation"));
    assert!(application.contains("blocked until writer takeover reconciliation completes"));
    assert!(process.contains("self.application.complete_writer_reconciliation()"));
}

#[test]
fn execution_actor_owns_order_lifecycle_state_and_submission_transitions() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let facade = fs::read_to_string(root.join("src/application/service.rs"))
        .expect("read Execution application");
    let intent_use_cases =
        fs::read_to_string(root.join("src/application/service/intent_use_cases.rs"))
            .expect("read Execution intent use cases");
    let order_use_cases =
        fs::read_to_string(root.join("src/application/service/order_use_cases.rs"))
            .expect("read Execution order use cases");
    let application = format!("{facade}\n{intent_use_cases}\n{order_use_cases}");
    let application_fields = facade
        .split("pub struct ExecutionApplication {")
        .nth(1)
        .and_then(|value| value.split("impl ExecutionApplication").next())
        .expect("locate ExecutionApplication fields");
    let actor =
        fs::read_to_string(root.join("src/services/actor.rs")).expect("read Execution actor");
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
    let process = fs::read_to_string(root.join("src/application/process.rs"))
        .expect("read Execution process");
    assert!(!process.contains("seen_exchange_events:"));
    assert!(!process.contains("exchange_event_order:"));
    assert!(actor.contains("seen_exchange_events: HashSet<String>"));
    assert!(actor.contains("fn accept_exchange_event("));
    let model = fs::read_to_string(root.join("src/application/model.rs"))
        .expect("read Execution application models");
    assert!(model.contains("pub struct SubmitOrder"));
    assert!(model.contains("pub enum ExecutionError"));
    assert!(!facade.contains("pub struct SubmitOrder"));
    assert!(!facade.contains("pub enum ExecutionError"));
    assert!(intent_use_cases.contains("pub fn submit_intent("));
    assert!(order_use_cases.contains("pub fn submit("));
}

#[test]
fn execution_rest_exposes_health_as_its_only_get_query() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let process = fs::read_to_string(root.join("src/application/process.rs"))
        .expect("read Execution process");
    let transport = fs::read_to_string(root.join("src/services/control_transport.rs"))
        .expect("read Execution control transport");
    assert!(
        transport.contains("method == \"GET\" && path != kairos_workspace::runtime::HEALTH_PATH")
    );
    assert!(transport.contains("path == kairos_workspace::runtime::HEALTH_PATH"));
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
        assert!(transport.contains(transport_mapping));
    }

    let contract = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("contract/src/control/client.rs"),
    )
    .expect("read Execution control contract");
    assert_eq!(contract.matches("\"GET\"").count(), 1);
    assert!(contract.contains("\"GET\", \"/v1/health\""));
}

#[test]
fn execution_health_does_not_expose_business_state_or_diagnostics() {
    let source = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/application/process.rs"),
    )
    .expect("read Execution process");
    let health_start = source
        .find("ControlOperation::Health =>")
        .expect("Execution health response branch");
    let health_end = source[health_start..]
        .find("\n            ControlOperation::AdvanceTime")
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
    assert!(health.contains("order_event_routes"));
}

#[test]
fn execution_cli_reads_business_state_only_from_current_mmap() {
    let source = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/bin/kairos-execution-cli.rs"),
    )
    .expect("read Execution CLI");
    assert!(source.contains("ExecutionViewKind::CurrentExecution"));
    assert!(source.contains("frame.current_execution()?"));
    assert!(source.contains("query command routed to typed mmap"));
    assert!(source.contains("RestControlClient::new"));
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
    let ports = format!(
        "{}\n{}",
        fs::read_to_string(root.join("src/application/intent_planner.rs"))
            .expect("read Execution intent planning boundary"),
        fs::read_to_string(root.join("src/application/order_admission.rs"))
            .expect("read Execution order admission boundary")
    );
    let account_facts = fs::read_to_string(root.join("src/application/account_facts.rs"))
        .expect("read Execution Account fact boundary");
    let actor =
        fs::read_to_string(root.join("src/services/actor.rs")).expect("read Execution Actor");

    for forbidden in ["prepare_order", "publish_order", "publish_fill"] {
        assert!(
            !ports.contains(forbidden),
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
            !ports.contains(forbidden),
            "application dependency ports still own Risk hook {forbidden}"
        );
    }
    assert!(account_facts.contains("trait ExecutionAccountFacts"));
    assert!(account_facts.contains("fn publish_order"));
    assert!(account_facts.contains("fn publish_fill"));
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

    let server = fs::read_to_string(root.join("src/bin/kairos-execution-server.rs"))
        .expect("read Execution server");
    assert!(server.contains("QueuedExecutionAccountFacts::start"));
    assert!(server.contains("attach_risk_reservations"));
    assert!(server.contains("QueuedExecutionRiskReservations::start"));

    let composition_dependencies = fs::read_to_string(root.join("src/composition/dependencies.rs"))
        .expect("read concrete Execution dependency readers");
    let application = fs::read_to_string(root.join("src/application/service.rs"))
        .expect("read Execution application");
    assert!(!root.join("src/application/preflight.rs").exists());
    assert!(!root.join("src/composition/preflight.rs").exists());
    assert!(!application.contains("attach_preflight"));
    assert!(application.contains("attach_intent_planner"));
    assert!(application.contains("attach_order_admission"));
    let worker = fs::read_to_string(root.join("src/composition/dependency_worker.rs"))
        .expect("read focused dependency workers");
    assert!(worker.contains("struct QueuedExecutionIntentPlanner"));
    assert!(worker.contains("struct QueuedExecutionOrderAdmission"));
    assert!(!worker.contains("ExecutionDependencyBackend"));
    assert!(!worker.contains("QueuedExecutionDependencyReader"));
    let risk_reservations = fs::read_to_string(root.join("src/composition/risk_reservations.rs"))
        .expect("read concrete Risk reservation adapter");
    assert!(!composition_dependencies.contains("impl ExecutionRiskReservations"));
    assert!(risk_reservations.contains("impl ExecutionRiskReservations"));
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
fn simulated_account_settlement_uses_the_durable_order_settlement_asset() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let account_facts = fs::read_to_string(root.join("src/composition/account_facts.rs"))
        .expect("read Execution Account fact adapter");
    let application = fs::read_to_string(root.join("src/application/service.rs"))
        .expect("read Execution application");
    let publisher = fs::read_to_string(root.join("src/composition/publishers.rs"))
        .expect("read Execution mmap publisher");
    let schema = fs::read_to_string(root.join("../../../schemas/v2/execution/types/order.fbs"))
        .expect("read Execution order schema");

    assert!(!account_facts.contains("settlement_asset: \"USDT\""));
    assert!(account_facts.contains(".settlement_asset"));
    assert!(application.contains("commitment.settlement_asset = request"));
    assert!(publisher.contains("let settlement_asset = commitment"));
    assert!(schema.contains("settlement_asset:string"));
}
