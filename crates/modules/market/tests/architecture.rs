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
        "src/services/source/snapshot.rs",
        "src/services/source/stream.rs",
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
fn live_market_events_use_only_aeron_while_replay_keeps_uds() {
    let process = source("src/composition/process/mod.rs");
    let runtime = source("src/application/process/lifecycle.rs");
    assert!(process.contains("without_event_socket()"));
    assert!(process.contains("with_aeron_event_publisher"));
    assert!(process.contains("profile.scope != MarketRuntimeScope::Replay"));
    assert!(runtime.contains("event_socket_path: Option<PathBuf>"));
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
fn private_services_do_not_depend_on_composition_or_provider_types() {
    let mut pending = vec![crate_root().join("src/services")];
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
            let production = value.split("#[cfg(test)]").next().unwrap_or(&value);
            for forbidden in ["crate::composition", "kairos_integration::participants"] {
                assert!(
                    !production.contains(forbidden),
                    "Market service imports forbidden dependency {forbidden}: {}",
                    path.display()
                );
            }
        }
    }
}

#[test]
fn reference_client_and_contract_are_composition_only() {
    for directory in ["src/application", "src/services", "src/domain"] {
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
                assert!(
                    !value.contains("kairos_reference_contract")
                        && !value.contains("ReferenceSqliteReader")
                        && !value.contains("ReferenceProjection")
                        && !value.contains("ReferenceChanged"),
                    "Market business layer contains a Reference adapter/model: {}",
                    path.display()
                );
            }
        }
    }
    for path in ["src/domain/reference", "src/services/reference"] {
        assert!(
            !crate_root().join(path).exists(),
            "obsolete Market-owned Reference boundary remains: {path}"
        );
    }
    let composition = source("src/composition/reference/projection.rs");
    assert!(composition.contains("ReferenceProjectionSnapshot"));
    assert!(composition.contains("ReconcileMarketUniverse"));
    let process = source("src/composition/process/mod.rs");
    let watcher = source("src/composition/reference/watcher.rs");
    assert!(process.contains("market_snapshot()"));
    assert!(watcher.contains("market_snapshot()"));
}

#[test]
fn actor_is_the_single_source_runtime_state_owner() {
    let actor = source("src/services/actor/state.rs");
    for owned in [
        "attached_sources",
        "pending_source_requests",
        "next_source_input_index",
        "source_input_capacity",
    ] {
        assert!(actor.contains(owned), "MarketActor must own {owned}");
    }
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
fn market_rest_exposes_health_as_its_only_get_query() {
    let process = source("src/application/process/ingress.rs");
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

#[test]
fn application_root_contains_only_its_module_boundary() {
    let root = crate_root().join("src/application");
    let files = std::fs::read_dir(root)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.is_file())
        .map(|path| path.file_name().unwrap().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    assert_eq!(files, vec!["mod.rs"]);
}

#[test]
fn services_root_contains_only_its_module_boundary() {
    let root = crate_root().join("src/services");
    let files = std::fs::read_dir(root)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.is_file())
        .map(|path| path.file_name().unwrap().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    assert_eq!(files, vec!["mod.rs"]);
}

#[test]
fn domain_root_contains_only_its_module_boundary() {
    let root = crate_root().join("src/domain");
    let files = std::fs::read_dir(root)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.is_file())
        .map(|path| path.file_name().unwrap().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    assert_eq!(files, vec!["mod.rs"]);
}

#[test]
fn composition_root_contains_only_its_module_boundary() {
    let root = crate_root().join("src/composition");
    let files = std::fs::read_dir(root)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.is_file())
        .map(|path| path.file_name().unwrap().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    assert_eq!(files, vec!["mod.rs"]);
}

#[test]
fn every_public_observation_has_a_symmetric_vertical_directory() {
    for kind in [
        "quote",
        "trade",
        "bar",
        "trade_bar",
        "quote_bar",
        "ticker_24h",
        "option_greeks",
        "rate",
        "mark_price",
        "index_price",
        "funding_rate",
        "open_interest",
        "order_book",
    ] {
        for layer in [
            "domain/observation",
            "application/observations",
            "services/actor/observations",
        ] {
            assert!(
                crate_root()
                    .join(format!("src/{layer}/{kind}/mod.rs"))
                    .is_file(),
                "missing symmetric observation module: {layer}/{kind}"
            );
        }
    }
    assert!(!crate_root().join("src/domain/orderbook.rs").exists());
    assert!(!crate_root().join("src/domain/orderbook").exists());
    assert!(!crate_root()
        .join("src/domain/observation/kind/mod.rs")
        .exists());
    for file in ["book.rs", "level.rs", "delta.rs", "continuity.rs"] {
        assert!(crate_root()
            .join(format!("src/domain/observation/order_book/{file}"))
            .is_file());
    }
    let observation = source("src/domain/observation/mod.rs");
    assert!(observation.contains("pub fn kind(&self) -> ObservationKind"));
    let application_quote = source("src/application/observations/quote/mod.rs");
    assert!(application_quote.contains("pub fn ingest_quote"));
    let actor_quote = source("src/services/actor/observations/quote/mod.rs");
    assert!(actor_quote.contains("fn apply_quote"));
}

#[test]
fn view_checkpoint_and_change_have_distinct_boundaries() {
    assert!(!crate_root().join("src/domain/snapshot").exists());
    assert!(crate_root()
        .join("src/services/actor/checkpoint.rs")
        .is_file());
    let root = source("src/lib.rs");
    assert!(!root.contains("ReplayCheckpoint"));
    assert!(!root.contains("MarketSnapshot"));
    assert!(!root.contains("MarketSnapshotPublisher"));
    let projection = source("src/application/observations/projection.rs");
    assert!(!projection.contains("pub fn snapshot"));
    assert!(projection.contains("pub fn current_view"));
    let publication = source("src/application/process/publication.rs");
    assert!(publication.contains("trait MarketChangePublisher"));
    assert!(!publication.contains("SnapshotPublisher"));
}

#[test]
fn canonical_market_capabilities_do_not_create_stateless_kind_wrappers() {
    let capability = source("src/domain/observation/identity/capability.rs");
    assert!(capability.contains("fn supports_observation("));
    for kind in ["spot", "perpetual", "future", "option"] {
        assert!(!crate_root()
            .join(format!("src/domain/market/{kind}.rs"))
            .exists());
    }
}

#[test]
fn observation_identity_and_order_book_behavior_live_in_their_owned_modules() {
    for file in ["kind.rs", "key.rs", "qualifier.rs", "capability.rs"] {
        assert!(crate_root()
            .join(format!("src/domain/observation/identity/{file}"))
            .is_file());
    }
    for file in ["projection.rs", "continuity.rs", "resync.rs"] {
        assert!(crate_root()
            .join(format!("src/application/observations/order_book/{file}"))
            .is_file());
    }
    assert!(crate_root()
        .join("src/services/actor/observations/views.rs")
        .is_file());
    assert!(crate_root()
        .join("src/services/actor/observations/order_book/continuity.rs")
        .is_file());

    let actor_state = source("src/services/actor/state.rs");
    for migrated in [
        "fn apply_observation",
        "fn apply_orderbook_snapshot",
        "fn apply_orderbook_delta",
        "fn begin_orderbook_resync",
        "fn complete_orderbook_resync",
        "fn record_orderbook_freshness",
    ] {
        assert!(
            !actor_state.contains(migrated),
            "migrated Observation behavior remains in actor/state.rs: {migrated}"
        );
    }
}

#[test]
fn subscription_and_universe_slices_have_owned_vertical_modules() {
    for file in ["intent.rs", "member.rs", "selector.rs", "status.rs"] {
        assert!(crate_root()
            .join(format!("src/domain/subscription/{file}"))
            .is_file());
    }
    for file in [
        "static_subscription.rs",
        "dynamic_subscription.rs",
        "lifecycle.rs",
        "resolution.rs",
    ] {
        assert!(crate_root()
            .join(format!("src/application/subscriptions/{file}"))
            .is_file());
    }
    for file in ["reconciliation.rs", "recovery.rs"] {
        assert!(crate_root()
            .join(format!("src/application/universe/{file}"))
            .is_file());
    }
    assert!(crate_root()
        .join("src/services/actor/subscriptions.rs")
        .is_file());
    assert!(crate_root()
        .join("src/services/actor/universe/mod.rs")
        .is_file());
    assert!(!crate_root()
        .join("src/application/universe/resolution.rs")
        .exists());

    let actor_state = source("src/services/actor/state.rs");
    for migrated in [
        "fn subscribe_static",
        "fn subscribe_dynamic",
        "fn unsubscribe",
        "fn release_owner",
        "fn set_member_requirement",
        "fn apply_market_universe",
        "fn reconcile_market_universe_members",
        "fn subscription_states",
    ] {
        assert!(
            !actor_state.contains(migrated),
            "migrated Subscription/Universe behavior remains in actor/state.rs: {migrated}"
        );
    }
}

#[test]
fn source_and_freshness_slices_have_owned_modules() {
    for file in ["identity.rs", "route.rs", "state.rs", "readiness.rs"] {
        assert!(crate_root()
            .join(format!("src/domain/source/{file}"))
            .is_file());
    }
    for file in ["status.rs", "evaluation.rs"] {
        assert!(crate_root()
            .join(format!("src/domain/freshness/{file}"))
            .is_file());
    }
    for file in ["attachment.rs", "subscriptions.rs", "recovery.rs"] {
        assert!(crate_root()
            .join(format!("src/application/sources/{file}"))
            .is_file());
    }
    for file in ["driver.rs", "normalization.rs", "recovery.rs"] {
        assert!(crate_root()
            .join(format!("src/services/source/{file}"))
            .is_file());
    }
    for file in ["routing.rs", "activation.rs", "replay.rs"] {
        assert!(crate_root()
            .join(format!("src/composition/sources/{file}"))
            .is_file());
    }
    assert!(!crate_root()
        .join("src/application/sources/orchestration.rs")
        .exists());

    let actor_state = source("src/services/actor/state.rs");
    for migrated in [
        "fn register_source",
        "fn take_source_handle",
        "fn apply_source_status",
        "fn apply_source_failure",
        "fn refresh_feed_status",
        "fn evaluate_freshness",
    ] {
        assert!(
            !actor_state.contains(migrated),
            "migrated Source/Freshness behavior remains in actor/state.rs: {migrated}"
        );
    }
}

#[test]
fn domain_is_not_a_public_crate_module() {
    let root = source("src/lib.rs");
    assert!(root.contains("mod domain;"));
    assert!(!root.contains("pub mod domain;"));
}

#[test]
fn process_does_not_decode_control_wire_records() {
    for file in [
        "lifecycle.rs",
        "actor_task.rs",
        "ingress.rs",
        "maintenance.rs",
        "universe.rs",
        "recovery.rs",
        "publication.rs",
        "shutdown.rs",
    ] {
        assert!(crate_root()
            .join(format!("src/application/process/{file}"))
            .is_file());
    }
    for file in ["transport.rs", "wire.rs", "ingress.rs", "response.rs"] {
        assert!(crate_root()
            .join(format!("src/services/control/{file}"))
            .is_file());
    }
    assert!(!crate_root()
        .join("src/application/process/runtime.rs")
        .exists());
    let process = [
        source("src/application/process/actor_task.rs"),
        source("src/application/process/ingress.rs"),
    ]
    .join("\n");
    let process = process.split("#[cfg(test)]").next().unwrap_or(&process);
    for forbidden in [
        "serde::Deserialize",
        "serde_json::from_str",
        "serde_json::from_value",
        "struct CommandEnvelope",
        "struct SubscribePayload",
    ] {
        assert!(
            !process.contains(forbidden),
            "Market process decodes control wire record: {forbidden}"
        );
    }
    let wire = source("src/services/control/wire.rs");
    assert!(wire.contains("struct CommandEnvelope"));
    assert!(wire.contains("parse_subscribe_command"));
}

#[test]
fn publication_history_and_replay_implementations_have_final_owners() {
    for file in ["fanout.rs", "queue.rs"] {
        assert!(crate_root()
            .join(format!("src/services/publication/{file}"))
            .is_file());
    }
    for file in ["views.rs", "events.rs", "encoding.rs", "mmap.rs"] {
        assert!(crate_root()
            .join(format!("src/composition/publication/{file}"))
            .is_file());
    }
    assert!(crate_root()
        .join("src/composition/history/jsonl.rs")
        .is_file());
    for file in ["model.rs", "loader.rs"] {
        assert!(crate_root()
            .join(format!("src/application/replay/{file}"))
            .is_file());
    }
    for old in [
        "src/services/publication/encoding.rs",
        "src/services/history/mod.rs",
        "src/composition/publisher/mmap.rs",
    ] {
        assert!(
            !crate_root().join(old).exists(),
            "obsolete path remains: {old}"
        );
    }

    let application = source("src/application/process/actor_task.rs");
    let application = application
        .split("#[cfg(test)]")
        .next()
        .unwrap_or(&application);
    let services = source("src/services/publication/queue.rs");
    assert!(!application.contains("crate::composition"));
    assert!(!services.contains("crate::composition"));
}
