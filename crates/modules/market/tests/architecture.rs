use std::path::{Path, PathBuf};

// These checks are intentionally limited to safety and ownership rules that
// cannot yet be expressed through Cargo visibility or the repository-wide
// layer/dependency checks. Layout inventories and positive implementation
// snippets belong in compiler-checked modules and behavior tests instead.

fn crate_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).to_path_buf()
}

fn source(path: &str) -> String {
    std::fs::read_to_string(crate_root().join(path)).expect("read Market source file")
}

#[test]
fn production_market_runtime_does_not_block_provider_io() {
    for path in [
        "src/bin/kairos-market-server.rs",
        "src/composition/mod.rs",
        "src/composition/sources/connections.rs",
        "src/services/source/snapshot.rs",
        "src/services/source/stream.rs",
    ] {
        let source = source(path);
        for forbidden in [
            concat!("kairos_", "integration::blocking"),
            "spawn_blocking",
            "block_in_place",
        ] {
            assert!(
                !source.contains(forbidden),
                "Market production provider I/O uses {forbidden}: {path}"
            );
        }
    }
}

#[test]
fn production_server_does_not_select_provider_or_transport() {
    let server = source("src/bin/kairos-market-server.rs");
    for forbidden in [
        "--provider",
        "--endpoint",
        "--credential-id",
        "--replay-file",
        "provider ==",
        "provider.as_str",
        "axum::",
        "UnixListener",
        "TcpListener",
    ] {
        assert!(
            !server.contains(forbidden),
            "production server owns forbidden selection or transport detail: {forbidden}"
        );
    }
}

#[test]
fn business_code_does_not_import_provider_implementations() {
    for directory in ["src/application", "src/domain", "src/services"] {
        let mut pending = vec![crate_root().join(directory)];
        while let Some(directory) = pending.pop() {
            for entry in std::fs::read_dir(directory).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    pending.push(path);
                    continue;
                }
                if path.extension().and_then(|value| value.to_str()) != Some("rs") {
                    continue;
                }
                let source = std::fs::read_to_string(&path).unwrap();
                let production = source.split("#[cfg(test)]").next().unwrap_or(&source);
                for forbidden in [
                    concat!("kairos_", "integration::services"),
                    concat!("kairos_", "integration::participants"),
                    "tokio_tungstenite",
                    "tungstenite::",
                ] {
                    assert!(
                        !production.contains(forbidden),
                        "Market business code imports provider implementation {forbidden}: {}",
                        path.display()
                    );
                }
            }
        }
    }
}

#[test]
fn live_publication_uses_system_aeron_outputs() {
    let assembly = source("src/composition/launch/assembly.rs");
    let process = source("src/application/process/conflux.rs");
    assert!(assembly.contains("AeronOutputDeclaration"));
    assert!(assembly.contains("outputs()"));
    assert!(assembly.contains(".aeron"));
    assert!(process.contains("outputs()"));
    assert!(process.contains(".aeron"));
    assert!(!process.contains("event_socket_path"));
}

#[test]
fn reference_facts_are_queried_without_a_market_replica() {
    let process = source("src/application/process/conflux.rs");
    let assembly = source("src/composition/launch/assembly.rs");
    assert!(process.contains(".reference_client(&reference.client_key)"));
    assert!(process.contains(".market_catalog(&query)"));
    assert!(assembly.contains("install_reference_connection"));
    assert!(!assembly.contains("spawn_market_universe_watcher"));
    assert!(!assembly.contains("reference_market_snapshot"));
    assert!(!process.contains(".market_snapshot()"));
    assert!(!crate_root().join("src/domain/reference").exists());
}

#[test]
fn application_has_no_second_source_state_owner() {
    let application = source("src/application/mod.rs");
    let application_struct = application
        .split("pub struct MarketApplication")
        .nth(1)
        .unwrap()
        .split('}')
        .next()
        .unwrap();
    assert!(application_struct.contains("actor: crate::services::actor::MarketActor"));
    assert!(!application_struct.contains("sources:"));
    assert!(!application_struct.contains("pending_source_requests:"));
}

#[test]
fn conflux_uses_the_closed_market_contract() {
    let process = source("src/application/process/conflux.rs");
    assert!(!process.contains("ConfluxEvent::Rest(request)"));
    assert!(!process.contains("MarketRestRequest"));
    assert!(!process.contains("take_managed_source"));
    assert!(!process.contains("SourceActivator"));

    let contract = source("contract/src/control/types.rs");
    assert!(!contract.contains("pub enum MarketRestRequest"));
    assert!(!contract.contains("pub enum MarketRestResponse"));
}
