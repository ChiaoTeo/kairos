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
fn capital_application_does_not_publish_provider_or_persistence_ports() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    assert!(!root.join("application/ports.rs").exists());
    let application = fs::read_to_string(root.join("application/mod.rs")).unwrap();
    assert!(!application.contains("Binance"));
    assert!(!application.contains("StateStore"));
}

#[test]
fn capital_indexed_databases_use_dedicated_current_roots() {
    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let publisher = fs::read_to_string(crate_root.join("contract/src/view/encode.rs")).unwrap();
    for dedicated_root in [
        "CapitalObjectiveCurrent",
        "CapitalDemandCurrent",
        "CapitalPolicyCurrent",
        "CapitalFactsCurrent",
        "CapitalAvailabilityCurrent",
        "CapitalRouteCurrent",
        "CapitalPlanCurrent",
        "CapitalReservationCurrent",
        "CapitalOperationCurrent",
        "CapitalAlertCurrent",
    ] {
        assert!(publisher.contains(dedicated_root));
    }
    assert!(!publisher.contains("CapitalEntityCurrent"));
}

#[test]
fn capital_uses_owner_capabilities_at_their_intended_boundaries() {
    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let manifest = fs::read_to_string(crate_root.join("Cargo.toml")).unwrap();
    assert!(manifest.contains("kairos-conflux.workspace = true"));
    assert!(manifest.contains("kairos-integration.workspace = true"));
    for path in rust_files(&crate_root.join("src")) {
        let source = fs::read_to_string(&path).unwrap();
        if !path
            .components()
            .any(|component| component.as_os_str() == "composition")
        {
            assert!(
                !source.contains("BinanceCapitalRestConnection")
                    && !source.contains("BinanceTransferAccount"),
                "{} leaks provider composition outside Capital composition",
                path.display()
            );
        }
        assert!(
            !source.contains("CapitalTransferConnections")
                && !source.contains("CapitalTransferProcess"),
            "{} restores a transfer-only name for the broader Capital runtime",
            path.display()
        );
    }

    let standalone = fs::read_to_string(crate_root.join("src/application/transfer.rs")).unwrap();
    assert!(standalone.contains("kairos_integration"));
    assert!(!standalone.contains("kairos_conflux"));

    let process_root = crate_root.join("src/application/process");
    let process = rust_files(&process_root)
        .into_iter()
        .map(|path| fs::read_to_string(path).unwrap())
        .collect::<String>();
    assert!(process.contains("kairos_conflux"));
    assert!(!process.contains("kairos_integration"));

    assert!(process.contains("AssetTransferCommand"));
    assert!(process.contains("AssetTransferStatusQuery"));
    assert!(process.contains("EarnCommand"));
    assert!(process.contains("EarnActionStatusQuery"));
    assert!(!process.contains("CapitalTransferConnection"));
    assert!(!process.contains("CapitalEarnConnection"));

    let conflux_root = crate_root.join("../../system/conflux/src");
    assert!(!conflux_root.join("capital.rs").exists());
    for path in rust_files(&conflux_root) {
        let source = fs::read_to_string(&path).unwrap();
        assert!(!source.contains("trait CapitalTransferConnection"));
        assert!(!source.contains("trait CapitalEarnConnection"));
        assert!(!source.contains("struct CapitalTransferRequest"));
    }

    let integration_root = crate_root.join("../../platform/integration/src");
    let transfer_capabilities =
        fs::read_to_string(integration_root.join("capabilities/transfer.rs")).unwrap();
    let earn_capabilities =
        fs::read_to_string(integration_root.join("capabilities/earn.rs")).unwrap();
    assert!(transfer_capabilities.contains("pub trait AssetTransferCommand"));
    assert!(transfer_capabilities.contains("pub trait AssetTransferStatusQuery"));
    assert!(earn_capabilities.contains("pub trait EarnCommand"));
    assert!(earn_capabilities.contains("pub trait EarnActionStatusQuery"));

    let conflux = fs::read_to_string(conflux_root.join("lib.rs")).unwrap();
    assert!(conflux.contains("AssetTransferCommand"));
    assert!(conflux.contains("AssetTransferStatusQuery"));
    assert!(conflux.contains("EarnCommand"));
    assert!(conflux.contains("EarnActionStatusQuery"));
}

#[test]
fn capital_shutdown_reconciles_but_never_compensates_or_resubmits() {
    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let actor = fs::read_to_string(crate_root.join("src/application/process/conflux.rs")).unwrap();
    let start = actor.find("async fn stopping").unwrap();
    let end = actor[start..].find("impl<C> CapitalProcess").unwrap() + start;
    let handler = &actor[start..end];
    assert!(handler.contains("reconcile_capital_plan"));
    assert!(handler.contains("record_recovery_required"));
    assert!(!handler.contains("execute_capital_plan"));
    assert!(!handler.contains("submit_"));
    assert!(!handler.contains("compensate"));

    let server = fs::read_to_string(crate_root.join("src/bin/kairos-capital-server.rs")).unwrap();
    assert!(server.contains("build_capital_host"));
    assert!(server.contains("host.run().await"));
    assert!(!server.contains("axum::serve"));
    assert!(!server.contains("Router::new"));
    assert!(!server.contains("CapitalState"));
}
