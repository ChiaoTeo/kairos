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
        "src/composition/sources/connections.rs",
        "src/services/source/snapshot.rs",
        "src/services/source/stream.rs",
    ] {
        let source = source(path);
        assert!(
            !source.contains(concat!("kairos_", "integration::blocking"))
                && !source.contains("spawn_blocking")
                && !source.contains("block_in_place"),
            "Market production provider I/O must stay on the caller Tokio runtime: {path}"
        );
    }
}

#[test]
fn historical_download_uses_async_provider_capabilities() {
    let source = source("src/application/cli.rs");
    assert!(source.contains("HistoricalBarQuery"));
    assert!(source.contains("HistoricalQuoteQuery"));
    assert!(source.contains("HistoricalTradeQuery"));
    assert!(source.contains(".fetch_bars(bar_request)"));
    assert!(source.contains(".fetch_quotes(window)"));
    assert!(source.contains(".fetch_trades(window)"));
    assert!(source.contains(".await?"));
    assert!(!source.contains(concat!("kairos_", "integration::blocking")));
    assert!(!source.contains("blocking_historical_market"));
    assert!(!source.contains("ConfluxSystem::new()"));
    assert!(source.contains("MassiveRestConnection::new("));
    assert!(source.contains("BinanceSpotRestConnection::new("));
}

#[test]
fn standalone_snapshot_is_bounded_and_does_not_start_a_market_runtime() {
    let cli = source("src/application/cli.rs");
    let composition = source("src/composition/direct/mod.rs");
    let service = source("src/services/direct/mod.rs");
    let once = cli
        .split("pub async fn once")
        .nth(1)
        .unwrap()
        .split("pub async fn replay")
        .next()
        .unwrap();
    assert!(once.contains("direct_connection"));
    assert!(once.contains(".snapshot("));
    assert!(composition.contains("BinanceSpotRestConnection::new("));
    assert!(composition.contains("BinanceOptionsRestConnection::new("));
    assert!(composition.contains("MassiveRestConnection::new("));
    assert!(service.contains("fetch_quotes"));
    assert!(service.contains("fetch_trades"));
    assert!(service.contains("fetch_bars"));
    assert!(!composition.contains("ConfluxSystem::new()"));
    assert!(!once.contains("ConfluxSystem::new()"));
    assert!(!once.contains("MarketApplication::new("));
    assert!(!once.contains("subscribe_static"));
    assert!(!once.contains("WebSocket"));
    assert!(
        !crate_root()
            .join("src/composition/launch/diagnostic.rs")
            .exists()
    );
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
    assert!(source.contains("build_market_host"));
}

#[test]
fn live_market_events_use_only_aeron_while_replay_keeps_uds() {
    let process = source("src/composition/launch/assembly.rs");
    let conflux = source("src/application/conflux.rs");
    assert!(process.contains("AeronOutputDeclaration"));
    assert!(process.contains("system"));
    assert!(process.contains("outputs()"));
    assert!(process.contains(".aeron"));
    assert!(conflux.contains("outputs()"));
    assert!(conflux.contains(".aeron"));
    assert!(!conflux.contains("event_socket_path"));
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
                    concat!("kairos_", "integration::services"),
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
            for forbidden in [
                "crate::composition",
                concat!("kairos_", "integration::participants"),
            ] {
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
fn reference_aeron_is_polled_by_conflux_without_a_watcher_task() {
    let actor = std::fs::read_to_string(crate_root().join("src/application/conflux.rs")).unwrap();
    let assembly =
        std::fs::read_to_string(crate_root().join("src/composition/launch/assembly.rs")).unwrap();
    assert!(actor.contains("ConfluxEvent::Reference"));
    assert!(actor.contains(".reference_client(&client_key)"));
    assert!(assembly.contains("install_reference_connection"));
    assert!(!assembly.contains("spawn_market_universe_watcher"));
    assert!(
        !crate_root()
            .join("src/composition/reference/client.rs")
            .exists()
    );
    assert!(
        !crate_root()
            .join("src/composition/reference/events.rs")
            .exists()
    );
    assert!(!crate_root().join("src/domain/reference").exists());
    let resolution = source("src/application/universe/resolution.rs");
    assert!(resolution.contains("MarketReferenceSnapshot"));
    assert!(resolution.contains("ReconcileMarketUniverse"));
    let composition = source("src/composition/reference/universe.rs");
    assert!(composition.contains("MarketProviderBinding"));
    assert!(composition.contains("MarketProviderCapability"));
    assert!(assembly.contains("reference_market_snapshot"));
    assert!(actor.contains(".market_snapshot()"));
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
fn market_json_rpc_keeps_current_state_in_the_indexed_owner_view() {
    let host = source("src/composition/host.rs");
    let actor = source("src/application/conflux.rs");
    let indexed = source("src/services/publication/contract/indexed.rs");
    assert!(host.contains("MarketRpcService"));
    assert!(host.contains("MarketControlRpcServer"));
    assert!(host.contains("with_json_rpc"));
    assert!(!host.contains("MarketHttpControl"));
    assert!(!host.contains("with_http_control"));
    assert!(!host.contains("axum::"));
    assert!(actor.contains("impl MarketRpcActor for MarketApplication"));
    assert!(actor.contains("async fn health"));
    assert!(actor.contains("async fn data_routes"));
    assert!(actor.contains("outputs()"));
    assert!(indexed.contains("IndexedMutation::Put"));
    assert!(!indexed.contains("IndexedMutation::DeletePrefix"));
    for dedicated_root in [
        "MarketQuoteCurrent",
        "MarketBarCurrent",
        "MarketGreeksCurrent",
        "MarketRateCurrent",
        "MarketTicker24hCurrent",
        "MarketMarkPriceCurrent",
        "MarketFundingRateCurrent",
        "MarketOpenInterestCurrent",
        "MarketIndexPriceCurrent",
        "MarketOrderBookCurrent",
        "MarketFreshnessCurrent",
    ] {
        assert!(indexed.contains(dedicated_root));
    }
    assert!(!indexed.contains("MarketEntityCurrent"));
    assert!(!indexed.contains("BarWindow"));
    assert!(actor.contains(".indexed"));
    assert!(actor.contains(".apply("));
}

#[test]
fn market_transport_hosts_are_framework_owned() {
    let manifest = source("Cargo.toml");
    assert!(!manifest.contains("axum.workspace"));
    assert!(!manifest.contains("kairos-transport"));
    for path in ["src/composition/host.rs", "src/bin/kairos-market-server.rs"] {
        let source = source(path);
        for forbidden in ["axum::", "UnixListener", "TcpListener"] {
            assert!(!source.contains(forbidden), "{path} owns {forbidden}");
        }
    }
}

#[test]
fn application_root_contains_only_its_module_boundary() {
    let root = crate_root().join("src/application");
    let mut files = std::fs::read_dir(root)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.is_file())
        .map(|path| path.file_name().unwrap().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    files.sort();
    assert_eq!(
        files,
        vec!["cli.rs", "conflux.rs", "connected.rs", "mod.rs"]
    );
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
    assert_eq!(files, vec!["host.rs", "mod.rs"]);
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
    assert!(
        !crate_root()
            .join("src/domain/observation/kind/mod.rs")
            .exists()
    );
    for file in ["book.rs", "level.rs", "delta.rs", "continuity.rs"] {
        assert!(
            crate_root()
                .join(format!("src/domain/observation/order_book/{file}"))
                .is_file()
        );
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
    assert!(
        crate_root()
            .join("src/services/actor/checkpoint.rs")
            .is_file()
    );
    let root = source("src/lib.rs");
    assert!(!root.contains("ReplayCheckpoint"));
    assert!(!root.contains("MarketSnapshot"));
    assert!(!root.contains("MarketSnapshotPublisher"));
    let access = source("src/application/observations/access.rs");
    assert!(!access.contains("pub fn snapshot"));
    assert!(access.contains("pub fn current_view"));
    assert!(!crate_root().join("src/application/process").exists());
    let publication = source("src/services/publication/contract/indexed.rs");
    assert!(publication.contains("fn encode_latest_change_views"));
    assert!(!publication.contains("trait MarketChangePublisher"));
}

#[test]
fn canonical_market_capabilities_do_not_create_stateless_kind_wrappers() {
    let capability = source("src/domain/observation/identity/capability.rs");
    assert!(capability.contains("fn supports_observation("));
    for kind in ["spot", "perpetual", "future", "option"] {
        assert!(
            !crate_root()
                .join(format!("src/domain/market/{kind}.rs"))
                .exists()
        );
    }
}

#[test]
fn observation_identity_and_order_book_behavior_live_in_their_owned_modules() {
    for file in ["kind.rs", "key.rs", "qualifier.rs", "capability.rs"] {
        assert!(
            crate_root()
                .join(format!("src/domain/observation/identity/{file}"))
                .is_file()
        );
    }
    for file in ["snapshot.rs", "continuity.rs", "resync.rs"] {
        assert!(
            crate_root()
                .join(format!("src/application/observations/order_book/{file}"))
                .is_file()
        );
    }
    assert!(
        crate_root()
            .join("src/services/actor/observations/views.rs")
            .is_file()
    );
    assert!(
        crate_root()
            .join("src/services/actor/observations/order_book/continuity.rs")
            .is_file()
    );

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
        assert!(
            crate_root()
                .join(format!("src/domain/subscription/{file}"))
                .is_file()
        );
    }
    for file in [
        "static_subscription.rs",
        "dynamic_subscription.rs",
        "lifecycle.rs",
    ] {
        assert!(
            crate_root()
                .join(format!("src/application/subscriptions/{file}"))
                .is_file()
        );
    }
    for file in ["reconciliation.rs", "resolution.rs", "recovery.rs"] {
        assert!(
            crate_root()
                .join(format!("src/application/universe/{file}"))
                .is_file()
        );
    }
    assert!(
        crate_root()
            .join("src/services/actor/subscriptions.rs")
            .is_file()
    );
    assert!(
        crate_root()
            .join("src/services/actor/universe/mod.rs")
            .is_file()
    );
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
        assert!(
            crate_root()
                .join(format!("src/domain/source/{file}"))
                .is_file()
        );
    }
    for file in ["status.rs", "evaluation.rs"] {
        assert!(
            crate_root()
                .join(format!("src/domain/freshness/{file}"))
                .is_file()
        );
    }
    for file in ["attachment.rs", "subscriptions.rs", "recovery.rs"] {
        assert!(
            crate_root()
                .join(format!("src/application/sources/{file}"))
                .is_file()
        );
    }
    for file in ["driver.rs", "normalization.rs"] {
        assert!(
            crate_root()
                .join(format!("src/services/source/{file}"))
                .is_file()
        );
    }
    assert!(
        !crate_root()
            .join("src/services/source/recovery.rs")
            .exists()
    );
    for file in ["routing.rs", "activation.rs", "replay.rs"] {
        assert!(
            crate_root()
                .join(format!("src/composition/sources/{file}"))
                .is_file()
        );
    }
    assert!(
        !crate_root()
            .join("src/application/sources/orchestration.rs")
            .exists()
    );

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
fn conflux_uses_the_closed_market_contract() {
    assert!(!crate_root().join("src/application/process").exists());
    assert!(!crate_root().join("src/services/control").exists());
    let actor = source("src/application/conflux.rs");
    assert!(actor.contains("impl ConfluxActor for MarketApplication"));
    assert!(!actor.contains("ConfluxEvent::Rest(request)"));
    assert!(!actor.contains("MarketRestRequest"));
    assert!(!actor.contains("take_managed_source"));
    assert!(actor.contains("sync_all_source_subscriptions"));
    assert!(!actor.contains("SourceActivator"));
    let contract = source("contract/src/control/types.rs");
    assert!(!contract.contains("pub enum MarketRestRequest"));
    assert!(!contract.contains("pub enum MarketRestResponse"));
    assert!(contract.contains("pub struct MarketCommandEnvelope"));
    let service = source("contract/src/control/service.rs");
    assert!(service.contains("#[conflux_rpc(namespace = \"market\")]"));
}

#[test]
fn provider_connections_enter_market_through_named_conflux_collections() {
    let installer = source("src/composition/sources/connections.rs");
    let actor = source("src/application/conflux.rs");
    for collection in [
        ".binance_spot_rest",
        ".binance_spot_websocket",
        ".binance_stocks_rest",
        ".okx_public_rest",
        ".okx_public_websocket",
        ".hyperliquid_info_rest",
        ".hyperliquid_websocket",
        ".massive_stocks_websocket",
        ".massive_options_websocket",
        ".massive_futures_websocket",
        ".massive_indices_websocket",
        ".massive_forex_websocket",
        ".massive_crypto_websocket",
        ".ibkr_market_data",
    ] {
        assert!(
            installer.contains(collection),
            "installer misses {collection}"
        );
        assert!(
            actor.contains(collection),
            "Actor does not borrow {collection} through Context"
        );
    }
    let driver = source("src/services/source/driver.rs");
    assert!(!driver.contains("trait SourceActivator"));
}

#[test]
fn publication_history_and_replay_implementations_have_final_owners() {
    assert!(
        crate_root()
            .join("src/services/publication/queue.rs")
            .is_file()
    );
    assert!(
        !crate_root()
            .join("src/services/publication/fanout.rs")
            .exists()
    );
    for file in ["events.rs", "encoding.rs", "indexed.rs"] {
        assert!(
            crate_root()
                .join(format!("src/services/publication/contract/{file}"))
                .is_file()
        );
    }
    assert!(
        crate_root()
            .join("src/composition/history/jsonl.rs")
            .is_file()
    );
    for file in ["model.rs", "loader.rs"] {
        assert!(
            crate_root()
                .join(format!("src/application/replay/{file}"))
                .is_file()
        );
    }
    for old in [
        "src/services/publication/encoding.rs",
        "src/services/history/mod.rs",
    ] {
        assert!(
            !crate_root().join(old).exists(),
            "obsolete path remains: {old}"
        );
    }

    let application = source("src/application/conflux.rs");
    let application = application
        .split("#[cfg(test)]")
        .next()
        .unwrap_or(&application);
    let services = source("src/services/publication/queue.rs");
    assert!(!application.contains("crate::composition"));
    assert!(!services.contains("crate::composition"));
}

#[test]
fn composition_uses_symmetric_launch_config_and_reference_modules() {
    for file in ["mod.rs", "assembly.rs"] {
        assert!(
            crate_root()
                .join(format!("src/composition/launch/{file}"))
                .is_file()
        );
    }
    for file in ["dto.rs", "profile.rs", "sources.rs", "defaults.rs"] {
        assert!(
            crate_root()
                .join(format!("src/composition/config/{file}"))
                .is_file()
        );
    }
    for file in ["mod.rs", "universe.rs"] {
        assert!(
            crate_root()
                .join(format!("src/composition/reference/{file}"))
                .is_file()
        );
    }
    for old in [
        "src/composition/assembly/mod.rs",
        "src/composition/process/mod.rs",
        "src/composition/diagnostic/mod.rs",
        "src/composition/config/model.rs",
        "src/composition/config/runtime.rs",
        "src/composition/reference/watcher.rs",
    ] {
        assert!(
            !crate_root().join(old).exists(),
            "obsolete path remains: {old}"
        );
    }
}

#[test]
fn tests_and_public_boundaries_follow_final_layout() {
    for file in ["application.rs", "behavior.rs", "architecture.rs"] {
        assert!(crate_root().join(format!("tests/{file}")).is_file());
    }
    for file in [
        "application/actor.rs",
        "behavior/orderbook.rs",
        "behavior/replay.rs",
    ] {
        assert!(crate_root().join(format!("tests/{file}")).is_file());
    }
    for old in ["tests/actor.rs", "tests/orderbook.rs", "tests/replay.rs"] {
        assert!(
            !crate_root().join(old).exists(),
            "obsolete test path remains: {old}"
        );
    }
    let root = source("src/lib.rs");
    assert!(root.contains("pub mod application;"));
    assert!(root.contains("pub mod composition;"));
    assert!(root.contains("mod domain;"));
    assert!(root.contains("mod services;"));
    assert!(!root.contains("pub mod domain;"));
    assert!(!root.contains("pub mod services;"));
}
