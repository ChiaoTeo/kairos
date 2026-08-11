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
fn account_domain_has_no_cross_module_or_infrastructure_dependencies() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/domain");
    for path in rust_files(&root) {
        let source = fs::read_to_string(&path).expect("read domain source");
        let shared_types_only = source
            .replace("kairos_domain_types", "")
            .replace("kairos-domain-types", "");
        assert!(
            !shared_types_only.contains("kairos_"),
            "domain source imports another Kairos module: {}",
            path.display()
        );
    }
}

#[test]
fn account_application_does_not_publish_dependency_protocols() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    assert!(!root.join("application/protocol.rs").exists());
    let application =
        fs::read_to_string(root.join("application/mod.rs")).expect("read application module");
    for forbidden in [
        "AccountSnapshotSource",
        "AccountStreamSource",
        "AccountMarketProfileSource",
        "AccountStateStore",
        "OrderRisk",
    ] {
        assert!(
            !application.contains(forbidden),
            "application exports dependency protocol {forbidden}"
        );
    }
}

#[test]
fn account_does_not_reintroduce_risk_or_cross_module_service_imports() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    for path in rust_files(&root) {
        let source = fs::read_to_string(&path).expect("read account source");
        assert!(
            !source.contains("kairos_risk") && !source.contains("OrderRisk"),
            "Account coordinates Risk directly: {}",
            path.display()
        );
        let imports_cross_module_services = source.lines().any(|line| {
            line.trim_start().starts_with("use kairos_") && line.contains("::services")
        });
        assert!(
            !imports_cross_module_services,
            "Account imports another module's private services: {}",
            path.display()
        );
    }
}

#[test]
fn account_actor_is_a_state_owner_without_io_dependencies() {
    let actor =
        fs::read_to_string(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/services/actor.rs"))
            .expect("read account actor");
    for forbidden in [
        "AccountSnapshotGateway",
        "AccountEventStream",
        "JsonAccountStore",
        "std::fs",
        "std::net",
    ] {
        assert!(
            !actor.contains(forbidden),
            "AccountActor contains IO dependency {forbidden}"
        );
    }
}

#[test]
fn account_does_not_own_execution_order_lifecycle_or_expose_raw_aggregates() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    assert!(!root.join("domain/order.rs").exists());
    let domain = fs::read_to_string(root.join("domain/mod.rs")).expect("read account domain");
    for forbidden in ["Planned", "Reserved", "Submitting", "OrderState"] {
        assert!(
            !domain.contains(forbidden),
            "Account domain contains execution lifecycle concept {forbidden}"
        );
    }
    let results =
        fs::read_to_string(root.join("application/result.rs")).expect("read application results");
    assert!(!results.contains("pub account: Account"));
}

#[test]
fn account_contract_does_not_expose_execution_order_planning() {
    let client = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../contract/src/client.rs"),
    )
    .expect("read account contract client");
    assert!(!client.contains("OrderPlan"));
    assert!(!client.contains("plan_order"));
}

#[test]
fn account_capabilities_do_not_infer_provider_transfer_support() {
    let source = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/application/service.rs"),
    )
    .expect("read account application");
    assert!(source.contains("let can_transfer = false"));
    assert!(!source.contains("broker == \"binance\""));
}

#[test]
fn account_process_separates_live_fills_from_paper_settlement() {
    let process = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/application/process.rs"),
    )
    .expect("read account process");
    assert!(process.contains("\"/v1/fill\""));
    assert!(process.contains("\"/v1/order-event\""));
    assert!(process.contains("\"/v1/simulated-fill\""));
    assert!(process.contains("AccountEvent::Fill"));
}

#[test]
fn account_does_not_own_workspace_registry_implementation() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    assert!(!root.join("src/composition/account_registry.rs").exists());
    let composition = fs::read_to_string(root.join("src/composition/account.rs"))
        .expect("read account composition");
    assert!(!composition.contains("CredentialStore"));
    assert!(!composition.contains("TradeLockRecord"));
}

#[test]
fn account_has_no_legacy_trade_lock_protocol() {
    let cli = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/bin/kairos-account-cli.rs"),
    )
    .expect("read account cli");
    let workspace = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../kairos-workspace/src/account.rs"),
    )
    .expect("read workspace account registry");
    assert!(!cli.contains("TradeLock"));
    assert!(!workspace.contains("TradeLock"));
    assert!(!workspace.contains("locks.toml"));
}

#[test]
fn account_cli_does_not_expose_provider_money_operations() {
    let cli = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/bin/kairos-account-cli.rs"),
    )
    .expect("read account cli");
    assert!(!cli.contains("Command::Transfer"));
    assert!(!cli.contains("Command::Earn"));
    assert!(!cli.contains("compose_binance_transfer"));
    assert!(!cli.contains("compose_binance_earn"));
}
