use std::path::{Path, PathBuf};

fn crate_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).to_path_buf()
}

fn source(path: &str) -> String {
    std::fs::read_to_string(crate_root().join(path)).expect("read Market source file")
}

#[test]
fn production_market_runtime_never_bridges_provider_io_through_blocking_threads() {
    for path in [
        "src/bin/kairos-market-server.rs",
        "src/composition/mod.rs",
        "src/composition/sources/binance.rs",
        "src/composition/sources/massive.rs",
        "src/composition/sources/okx.rs",
        "src/services/sources/snapshot.rs",
        "src/services/sources/stream.rs",
    ] {
        let source = source(path);
        assert!(
            !source.contains("kairos_integration::blocking")
                && !source.contains("spawn_blocking")
                && !source.contains("block_in_place"),
            "Market production provider I/O must stay on the caller Tokio runtime: {path}"
        );
    }
}

#[test]
fn historical_download_uses_async_provider_capabilities() {
    let source = source("src/bin/kairos-market-cli.rs");
    assert!(source.contains("AsyncHistoricalMarketDataConnection"));
    assert!(source.contains(".fetch(&request)"));
    assert!(source.contains(".await?"));
    assert!(!source.contains("kairos_integration::blocking"));
    assert!(!source.contains("blocking_historical_market"));
}

#[test]
fn production_server_has_no_provider_or_transport_selection_surface() {
    let source = source("src/bin/kairos-market-server.rs");
    for forbidden in [
        "--provider",
        "--endpoint",
        "--credential-id",
        "--replay-file",
        "provider ==",
        "provider.as_str",
    ] {
        assert!(
            !source.contains(forbidden),
            "production server must not select provider details: {forbidden}"
        );
    }
    assert!(source.contains("build_market_process"));
}

#[test]
fn legacy_feed_runtime_files_and_names_are_absent() {
    for path in [
        "src/application/runtime.rs",
        "src/application/ports.rs",
        "src/services/connection.rs",
        "src/services/worker.rs",
        "src/services/composite.rs",
        "src/services/feed.rs",
        "src/services/integration.rs",
    ] {
        assert!(
            !crate_root().join(path).exists(),
            "legacy file remains: {path}"
        );
    }
    let mut pending = vec![crate_root().join("src")];
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
            let value = std::fs::read_to_string(&path).unwrap();
            for forbidden in [
                "MarketFeedWorker",
                "MarketConnectionManager",
                "AsyncMarketConnectionManager",
                "MarketEngine",
                "poll_feed",
                "drain_source_inputs",
            ] {
                assert!(
                    !value.contains(forbidden),
                    "legacy runtime concept {forbidden} remains in {}",
                    path.display()
                );
            }
        }
    }
}

#[test]
fn business_layers_do_not_import_provider_implementation_or_composition() {
    for directory in ["src/application", "src/domain"] {
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
                let value = std::fs::read_to_string(&path).unwrap();
                let value = value.split("#[cfg(test)]").next().unwrap_or(&value);
                for forbidden in [
                    "kairos_integration::services",
                    "tokio_tungstenite",
                    "tungstenite::",
                    "crate::composition",
                ] {
                    assert!(
                        !value.contains(forbidden),
                        "business layer imports forbidden implementation {forbidden}: {}",
                        path.display()
                    );
                }
            }
        }
    }
}

#[test]
fn actor_is_the_single_source_runtime_state_owner() {
    let actor = source("src/services/actor.rs");
    for owned in [
        "attached_sources",
        "pending_source_requests",
        "next_source_input_index",
        "source_input_capacity",
    ] {
        assert!(actor.contains(owned), "MarketActor must own {owned}");
    }
    let facade = source("src/application/facade.rs");
    let application_struct = facade
        .split("pub struct MarketApplication")
        .nth(1)
        .unwrap()
        .split('}')
        .next()
        .unwrap();
    assert!(application_struct.contains("actor: MarketActor"));
    assert!(!application_struct.contains("sources:"));
    assert!(!application_struct.contains("pending_source_requests:"));
}

#[test]
fn market_rest_exposes_health_as_its_only_get_query() {
    let process = source("src/application/process.rs");
    assert!(process.contains("method == \"GET\" && path != HEALTH_PATH"));
    assert!(process.contains("Market business queries are available only through typed mmap views"));
    assert!(process.contains("path == HEALTH_PATH && method != \"GET\""));
    let health = process
        .split("fn health(&self)")
        .nth(1)
        .expect("Market health function")
        .split("fn subscribe")
        .next()
        .expect("Market health body");
    for forbidden in [
        "actor_id",
        "generation",
        "event_sequence",
        "subscription_count",
        "subscriptions",
        "source_count",
    ] {
        assert!(
            !health.contains(forbidden),
            "Market health leaks {forbidden}"
        );
    }
}
