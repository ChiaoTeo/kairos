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
fn risk_application_does_not_publish_persistence_protocols() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    assert!(!root.join("application/protocol.rs").exists());
    let application = fs::read_to_string(root.join("application/mod.rs")).unwrap();
    assert!(!application.contains("RiskStateStore"));
}

#[test]
fn risk_domain_has_no_infrastructure_dependencies() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/domain");
    for path in rust_files(&root) {
        let source = fs::read_to_string(&path).unwrap();
        let shared_types_only = source
            .replace("kairos_primitives", "")
            .replace("kairos-primitives", "");
        assert!(!shared_types_only.contains("kairos_") && !source.contains("std::fs"));
    }
}

#[test]
fn risk_server_selects_a_profile_instead_of_an_account_or_exchange() {
    let server = std::fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/bin/kairos-risk-server.rs"),
    )
    .expect("read Risk server");
    assert!(server.contains("normalized_config()"));
    assert!(!server.contains("normalized-config.json"));
    assert!(server.contains("risk_profile"));
    assert!(server.contains("unknown Risk profile"));
    assert!(server.contains("Risk profile is required for a live launch"));
    for forbidden in [
        "account_id: String",
        "exchange_id: String",
        "provider: String",
    ] {
        assert!(
            !server.contains(forbidden),
            "Risk startup is incorrectly scoped by {forbidden}"
        );
    }
}

#[test]
fn risk_transport_is_owned_by_conflux_and_not_application() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    assert!(!root.join("src/application/host.rs").exists());
    let manifest = fs::read_to_string(root.join("Cargo.toml")).unwrap();
    assert!(!manifest.contains("axum.workspace"));
    assert!(!manifest.contains("kairos-transport"));

    for path in rust_files(&root.join("src/application")) {
        let source = fs::read_to_string(path).unwrap();
        for forbidden in [
            "axum::",
            "UnixListener",
            "TcpListener",
            "HttpControlRequest",
            "HttpControlResponse",
        ] {
            assert!(
                !source.contains(forbidden),
                "Risk Application depends on transport type {forbidden}"
            );
        }
    }

    let application = fs::read_to_string(root.join("src/application/process/conflux.rs")).unwrap();
    assert!(!application.contains("ConfluxEvent::Rest"));
    assert!(!application.contains("RestContract"));
    assert!(application.contains("impl RiskRpcActor for RiskApplication"));

    let composition = fs::read_to_string(root.join("src/composition/mod.rs")).unwrap();
    assert!(!composition.contains("with_http_control"));
    assert!(!composition.contains("RiskHttpControl"));
    assert!(composition.contains("with_json_rpc"));
    assert!(composition.contains("RiskRpcService"));

    let contract = fs::read_to_string(root.join("contract/src/control/mod.rs")).unwrap();
    assert!(!contract.contains("RiskHttpControl"));
    assert!(contract.contains("RiskControlRpcServer"));
}
