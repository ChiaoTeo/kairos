use std::fs;
use std::path::{Path, PathBuf};

// Only cross-cutting ownership prohibitions remain here. Concrete file
// inventories and positive call snippets are covered by compilation and
// behavior tests and must not become architecture snapshots.

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
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

fn rust_source(root: &Path) -> String {
    rust_files(root)
        .into_iter()
        .map(|path| fs::read_to_string(path).expect("read Rust source"))
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn public_boundaries_do_not_expose_decimal_storage_parts() {
    for boundary in [
        root().join("src/application"),
        root().join("src/bin"),
        root().join("contract/src"),
    ] {
        for path in rust_files(&boundary) {
            let source = fs::read_to_string(&path).unwrap();
            for forbidden in [
                "pub quantity_mantissa",
                "pub quantity_scale",
                "pub price_mantissa",
                "pub price_scale",
            ] {
                assert!(
                    !source.contains(forbidden),
                    "{forbidden} leaked through {}",
                    path.display()
                );
            }
        }
    }
}

#[test]
fn execution_has_no_cross_provider_channel_aliases() {
    for path in rust_files(&root().join("src")) {
        let source = fs::read_to_string(&path).unwrap();
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
                "{forbidden} leaked through {}",
                path.display()
            );
        }
    }
}

#[test]
fn public_models_have_no_serde_compatibility_aliases() {
    for boundary in [root().join("src"), root().join("contract/src")] {
        for path in rust_files(&boundary) {
            let source = fs::read_to_string(&path).unwrap();
            assert!(
                !source.contains("serde(alias"),
                "compatibility alias in {}",
                path.display()
            );
            assert!(
                !source.contains("alias ="),
                "compatibility alias in {}",
                path.display()
            );
        }
    }
}

#[test]
fn execution_does_not_own_treasury_operations() {
    let mut production = rust_source(&root().join("src/application"));
    production.push_str(&rust_source(&root().join("src/composition")));
    production.push_str(&rust_source(&root().join("src/bin")));
    for forbidden in [
        "FundingAllocation",
        "MoneyOperation",
        "capabilities::transfer",
        "capabilities::earn",
        "AssetTransferRequest",
        "EarnSubscribeRequest",
        "EarnRedeemRequest",
        "WithdrawRequest",
        "RepayRequest",
    ] {
        assert!(!production.contains(forbidden));
    }
}

#[test]
fn server_does_not_restore_obsolete_single_route_configuration() {
    let server = fs::read_to_string(root().join("src/bin/kairos-execution-server.rs")).unwrap();
    for forbidden in [
        "normalized-config.json",
        "routes_json:",
        "#[arg(long, default_value = \"main\")]",
        "#[arg(long, default_value = \"simulated\")]",
        "pub provider: String",
    ] {
        assert!(!server.contains(forbidden));
    }
}

#[test]
fn execution_does_not_create_reference_clients_or_read_reference_storage() {
    let dependencies =
        fs::read_to_string(root().join("src/services/dependencies/state/mod.rs")).unwrap();
    let composition = rust_source(&root().join("src/composition/connections"));
    for forbidden in [
        "ReferenceClient::connect",
        "ReferenceViewReader",
        "ReferenceSqliteReader",
        "reference_markets_current",
    ] {
        assert!(!dependencies.contains(forbidden));
        assert!(!composition.contains(forbidden));
    }
}

#[test]
fn control_transport_is_framework_owned() {
    let application = rust_source(&root().join("src/application"));
    let composition = rust_source(&root().join("src/composition"));
    let manifest = fs::read_to_string(root().join("Cargo.toml")).unwrap();
    assert!(!manifest.contains("axum.workspace"));
    assert!(!manifest.contains("kairos-transport"));
    assert!(!composition.contains("with_http_control"));
    for forbidden in ["axum::", "UnixListener", "TcpListener"] {
        assert!(!application.contains(forbidden));
    }
}

#[test]
fn publication_resources_have_no_second_concrete_owner() {
    let process = fs::read_to_string(root().join("src/application/process/conflux.rs")).unwrap();
    let services = rust_source(&root().join("src/services/publication"));
    assert!(!process.contains("try_with"));
    for forbidden in [
        "SharedExecutionSnapshotPublisher",
        "SharedIntentSnapshotPublisher",
        "AeronExecutionEventPublisher",
    ] {
        assert!(!services.contains(forbidden));
    }
}
