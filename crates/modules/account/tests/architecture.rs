use std::fs;
use std::path::{Path, PathBuf};

// Keep only ownership and safety prohibitions without an equivalent compiler,
// behavior-test, or repository-wide architecture check.

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn source(path: &str) -> String {
    fs::read_to_string(root().join(path)).expect("read Account source")
}

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
fn account_control_transport_is_framework_owned() {
    let manifest = source("Cargo.toml");
    let server = source("src/bin/kairos-account-server.rs");
    assert!(!manifest.contains("axum.workspace"));
    assert!(!manifest.contains("kairos-transport"));
    for forbidden in ["with_http_control", "axum::", "UnixListener", "TcpListener"] {
        assert!(!server.contains(forbidden));
    }
}

#[test]
fn account_does_not_import_risk_or_foreign_private_services() {
    for path in rust_files(&root().join("src")) {
        let source = fs::read_to_string(&path).expect("read Account source");
        assert!(
            !source.contains("kairos_risk") && !source.contains("OrderRisk"),
            "Account coordinates Risk directly: {}",
            path.display()
        );
        assert!(
            !source.lines().any(|line| {
                line.trim_start().starts_with("use kairos_") && line.contains("::services")
            }),
            "Account imports foreign private services: {}",
            path.display()
        );
    }
}

#[test]
fn account_actor_has_no_io_dependencies() {
    let actor = source("src/services/actor.rs");
    for forbidden in ["JsonAccountStore", "std::fs", "std::net"] {
        assert!(!actor.contains(forbidden));
    }
}

#[test]
fn account_queries_reference_instead_of_caching_identity_snapshots() {
    let integration = source("src/services/integration.rs");
    assert!(!integration.contains("AccountReferenceSnapshot"));
    assert!(!integration.contains("update_reference_snapshot"));
    assert!(integration.contains("ReferenceCatalog"));
    assert!(integration.contains(".read_session()"));
    assert!(integration.contains(".resolve_participant_symbol("));
    assert!(!integration.contains(".market_catalog("));
}

#[test]
fn account_does_not_own_execution_planning_or_lifecycle() {
    let domain = source("src/domain/mod.rs");
    for forbidden in ["Planned", "Reserved", "Submitting", "OrderState"] {
        assert!(!domain.contains(forbidden));
    }
    let contract = source("contract/src/control/account.rs");
    assert!(!contract.contains("OrderPlan"));
    assert!(!contract.contains("plan_order"));
}

#[test]
fn live_fact_mutation_is_not_exposed_by_account_control() {
    let service = source("contract/src/control/service.rs");
    assert!(!service.contains("publish_order_event"));
    assert!(!service.contains("publish_fill"));

    let server = source("src/bin/kairos-account-server.rs");
    for forbidden in ["\"/v1/fill\"", "\"/v1/order-event\""] {
        assert!(!server.contains(forbidden));
    }
}

#[test]
fn simulation_commands_remain_mode_gated() {
    let application = source("src/application/app.rs");
    assert!(application.contains("AccountRuntimeMode::Live"));
    assert!(application.contains("AccountRuntimeMode::Simulation"));
    assert!(application.contains("simulation command is disabled"));
}

#[test]
fn production_account_server_has_no_blocking_provider_fallback() {
    let server = source("src/bin/kairos-account-server.rs");
    for forbidden in [
        "compose_blocking_account",
        "compose_blocking_account_stream",
        "IntegrationAccountStream",
        "spawn_blocking",
        "block_in_place",
    ] {
        assert!(!server.contains(forbidden));
    }
}

#[test]
fn account_cli_does_not_expose_provider_money_mutations() {
    let cli = source("src/bin/kairos-account-cli.rs");
    for forbidden in [
        "Command::Transfer",
        "EarnSubscribe",
        "EarnRedeem",
        "EarnCommand",
        "compose_binance_transfer",
        "compose_binance_earn",
    ] {
        assert!(!cli.contains(forbidden));
    }
}

#[test]
fn reference_remains_the_canonical_instrument_identity_owner() {
    let integration =
        fs::read_to_string(root().join("../../platform/integration/src/domain/account.rs"))
            .expect("read Integration account facts");
    assert!(!integration.contains("canonical_account_identity"));
    assert!(!integration.contains("pub instrument_id: InstrumentId"));
    assert!(!integration.contains("pub market_id: Option<MarketId>"));

    let adapter = source("src/services/integration.rs");
    for forbidden in ["ReferenceClient", "ReferenceViewReader", "rusqlite"] {
        assert!(!adapter.contains(forbidden));
    }
}

#[test]
fn account_control_does_not_duplicate_indexed_business_views() {
    let server = source("src/bin/kairos-account-server.rs");
    let process = source("src/application/process/conflux.rs");
    let contract = source("contract/src/control/account.rs");
    for obsolete in [
        "/v1/balances",
        "/v1/positions",
        "/v1/open-orders",
        "/v1/account-state",
        "BalancesResponse",
        "PositionsResponse",
    ] {
        assert!(!server.contains(obsolete));
        assert!(!process.contains(obsolete));
        assert!(!contract.contains(obsolete));
    }
}

#[test]
fn application_has_no_synchronous_business_query_facade() {
    let application = source("src/application/app.rs");
    for forbidden in [
        "pub fn query(",
        "pub fn snapshot(",
        "pub fn balances(",
        "pub fn positions(",
        "pub fn open_orders(",
    ] {
        assert!(!application.contains(forbidden));
    }
}
