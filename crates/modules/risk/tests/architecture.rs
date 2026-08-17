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
    assert!(server.contains("normalized-config.json"));
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
fn risk_rest_exposes_health_as_its_only_get_query() {
    let process = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/application/process.rs"),
    )
    .unwrap();
    assert!(process.contains("method == \"GET\" && path != HEALTH_PATH"));
    assert!(process.contains("Risk business queries are available only through typed mmap views"));
    assert!(process.contains("path == HEALTH_PATH && method != \"GET\""));
    let health = process
        .split("fn health_body(&self)")
        .nth(1)
        .expect("Risk health function")
        .split("async fn risk_http_handler")
        .next()
        .expect("Risk health body");
    for forbidden in [
        "actor_id",
        "generation",
        "event_sequence",
        "policy_version",
        "budget_count",
        "reservation_count",
        "open_circuit_count",
        ".snapshot()",
    ] {
        assert!(!health.contains(forbidden), "Risk health leaks {forbidden}");
    }
}
