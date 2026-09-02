use std::fs;
use std::path::{Path, PathBuf};

#[test]
fn account_control_transport_is_framework_owned() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let manifest = fs::read_to_string(root.join("Cargo.toml")).unwrap();
    let server = fs::read_to_string(root.join("src/bin/kairos-account-server.rs")).unwrap();
    assert!(!manifest.contains("axum.workspace"));
    assert!(!manifest.contains("kairos-transport"));
    assert!(server.contains("with_json_rpc"));
    assert!(server.contains("AccountRpcService"));
    assert!(server.contains("AccountControlRpcServer"));
    assert!(!server.contains("with_http_control"));
    for forbidden in ["axum::", "UnixListener", "TcpListener"] {
        assert!(!server.contains(forbidden));
    }
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
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("contract/src/control/account.rs"),
    )
    .expect("read account contract client");
    assert!(!client.contains("OrderPlan"));
    assert!(!client.contains("plan_order"));
}

#[test]
fn account_application_does_not_expose_provider_capability_or_fee_queries() {
    let source = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/application/app.rs"),
    )
    .expect("read account application");
    assert!(!source.contains("pub fn capabilities("));
    assert!(!source.contains("pub fn fee_schedules("));
    assert!(!source.contains("refresh_market_profile"));
    assert!(!source.contains("broker == \"binance\""));
}

#[test]
fn account_conflux_has_one_live_fact_source_and_mode_gated_paper_settlement() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let server = fs::read_to_string(root.join("src/bin/kairos-account-server.rs"))
        .expect("read account server");
    let actor = fs::read_to_string(root.join("src/application/process/conflux.rs"))
        .expect("read Account Conflux actor");
    assert!(!server.contains("\"/v1/fill\""));
    assert!(!server.contains("\"/v1/order-event\""));
    assert!(!server.contains("\"/v1/simulation/settlements\""));
    assert!(actor.contains("async fn apply_simulated_settlement"));
    assert!(actor.contains("self.apply_simulated_fill"));
    assert!(actor.contains("fn simulated_fill"));
    assert!(actor.contains("SimulatedSettlement"));
    let composition = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/composition/account.rs"),
    )
    .expect("read Account composition");
    assert!(composition.contains("application.enable_simulation()"));
    assert!(composition.contains("\"paper\" | \"simulated\""));

    let application = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/application/app.rs"),
    )
    .expect("read Account application");
    assert!(application.contains("AccountRuntimeMode::Live"));
    assert!(application.contains("AccountRuntimeMode::Simulation"));
    assert!(application.contains("simulation command is disabled for this Account application"));

    let integration = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/services/integration.rs"),
    )
    .expect("read Account Integration ingress");
    assert!(integration.contains("ExternalAccountEvent::Order"));
    assert!(integration.contains("ExternalAccountEvent::Fill"));
}

#[test]
fn account_contract_exposes_no_live_fact_mutation() {
    let contract = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("contract/src/control/service.rs"),
    )
    .expect("read Account control contract");
    assert!(!contract.contains("publish_order_event"));
    assert!(!contract.contains("publish_fill"));
    assert!(contract.contains("apply_simulated_settlement"));
}

#[test]
fn account_composition_owns_its_binding_registry() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    assert!(!root.join("src/composition/account_registry.rs").exists());
    assert!(root.join("src/composition/registry.rs").exists());
    let composition = fs::read_to_string(root.join("src/composition/account.rs"))
        .expect("read account composition");
    assert!(!composition.contains("CredentialStore"));
    assert!(!composition.contains("TradeLockRecord"));
    let registry = fs::read_to_string(root.join("src/composition/registry.rs"))
        .expect("read account registry");
    for provider_environment in ["BINANCE_API_KEY", "BINANCE_API_SECRET", "OKX_API_KEY"] {
        assert!(
            !registry.contains(provider_environment),
            "Integration must own provider credential convention {provider_environment}"
        );
    }
}

#[test]
fn account_segment_identity_is_independent_from_provider_segment() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let composition = fs::read_to_string(root.join("src/composition/account.rs"))
        .expect("read account composition");
    let registry = fs::read_to_string(root.join("src/composition/registry.rs"))
        .expect("read account registry");

    assert!(composition.contains("pub struct AccountSegmentBinding"));
    assert!(composition.contains("normalized_segment(&configured_segment.provider_segment)"));
    assert!(composition.contains("pub trading_mode: Option<String>"));
    assert!(!composition.contains("segment_options.product = segment_key"));
    assert!(registry.contains("pub segment_products: BTreeMap<String, String>"));
    assert!(registry.contains("pub segment_trading_modes: BTreeMap<String, String>"));
}

#[test]
fn account_broker_identity_is_independent_from_integration_provider_route() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let registry = fs::read_to_string(root.join("src/composition/registry.rs"))
        .expect("read account registry");
    let server = fs::read_to_string(root.join("src/bin/kairos-account-server.rs"))
        .expect("read account server");
    let cli =
        fs::read_to_string(root.join("src/bin/kairos-account-cli.rs")).expect("read account cli");
    let cli_composition = fs::read_to_string(root.join("src/composition/cli.rs"))
        .expect("read Account CLI composition");

    assert!(registry.contains("pub broker: String"));
    assert!(registry.contains("pub integration_provider: String"));
    assert!(registry.contains("missing account.integration_provider"));
    assert!(!registry.contains("or_else(|| table_text(account, \"provider\"))"));
    assert!(server.contains("record.integration_provider.clone()"));
    assert!(!server.contains("let provider = record.broker.clone()"));
    assert!(cli_composition.contains("record.integration_provider.clone()"));
    assert!(!cli.contains("record.integration_provider.clone()"));
    assert!(!cli.contains(".map(|record| record.broker.clone())"));
}

#[test]
fn account_cli_does_not_expose_provider_money_mutations() {
    let cli = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/bin/kairos-account-cli.rs"),
    )
    .expect("read account cli");
    assert!(!cli.contains("Command::Transfer"));
    assert!(!cli.contains("EarnSubscribe"));
    assert!(!cli.contains("EarnRedeem"));
    assert!(!cli.contains("EarnCommand"));
    assert!(!cli.contains("compose_binance_transfer"));
    assert!(!cli.contains("compose_binance_earn"));
}

#[test]
fn native_account_refresh_does_not_bridge_async_io_through_blocking_threads() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    let integration =
        fs::read_to_string(root.join("services/integration.rs")).expect("read integration adapter");
    for forbidden in [
        "AsyncAccountReadProxy",
        "AsyncAccountMarketProfileProxy",
        "blocking_send(",
        "must be called by its dedicated refresh worker",
    ] {
        assert!(
            !integration.contains(forbidden),
            "native async Account IO retains transitional bridge {forbidden}"
        );
    }

    let composition =
        fs::read_to_string(root.join("composition/account.rs")).expect("read composition");
    let native_binance = composition
        .split("pub fn compose_binance_async_account_application")
        .nth(1)
        .and_then(|source| {
            source
                .split("pub fn compose_okx_async_account_application")
                .next()
        })
        .expect("Binance native composition");
    let native_okx = composition
        .split("pub fn compose_okx_async_account_application")
        .nth(1)
        .and_then(|source| {
            source
                .split("pub async fn inspect_account_credential")
                .next()
        })
        .expect("OKX native composition");
    for source in [native_binance, native_okx] {
        assert!(!source.contains("Box<dyn AccountReadConnection"));
        assert!(!source.contains("async_account_read_channel"));
        assert!(source.contains("AccountApplication::with_async_dependencies"));
        assert!(source.contains("account_system"));
    }

    assert!(!composition.contains("pub async fn refresh_report"));
    let application =
        fs::read_to_string(root.join("application/app.rs")).expect("read application facade");
    assert!(!application.contains("pub async fn refresh_report_async"));
    assert!(!application.contains("refresh_market_profile"));
}

#[test]
fn account_conflux_gates_readiness_and_tracks_external_stream_continuity() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let actor = fs::read_to_string(root.join("src/application/process/conflux.rs"))
        .expect("read Account Conflux actor");
    let synchronization = fs::read_to_string(root.join("src/services/synchronization.rs"))
        .expect("read account segment synchronization");
    assert!(actor.contains("AccountConfluxState"));
    assert!(synchronization.contains("initial_snapshot_complete"));
    assert!(synchronization.contains("channel_health"));
    assert!(synchronization.contains("event_watermarks"));
    assert!(synchronization.contains("recovery_events"));
    assert!(actor.contains("Account stream sequence gap"));
    assert!(actor.contains("provider_event_id"));
}

#[test]
fn production_account_server_never_falls_back_to_blocking_provider_io() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    let server =
        fs::read_to_string(root.join("bin/kairos-account-server.rs")).expect("read account server");
    for forbidden in [
        "compose_blocking_account",
        "compose_blocking_account_stream",
        "IntegrationAccountStream",
    ] {
        assert!(
            !server.contains(forbidden),
            "production Account server retains blocking provider path {forbidden}"
        );
    }
    assert!(server.contains("production Account requires a provider-native async source"));
    assert!(server.contains("process_lock(socket_name)"));
    assert!(server.contains("service_health(socket_name)"));
    assert!(server.contains("instance.snapshot(&[])"));

    let composition =
        fs::read_to_string(root.join("composition/account.rs")).expect("read account composition");
    assert!(!composition.contains("compose_blocking_account_stream"));
    assert!(!composition.contains("attach_account_stream"));
    assert!(!composition.contains("pub fn compose_account_application_for_segments"));
    assert!(!composition.contains("compose_blocking_account_application"));
    assert!(composition.contains("pub fn compose_local_account_application_for_segments"));

    let runtime =
        fs::read_to_string(root.join("services/runtime.rs")).expect("read account runtime");
    assert!(!runtime.contains("AccountEventStream"));
}

#[test]
fn binance_derivatives_account_streams_are_native_async_in_production_composition() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    let composition =
        fs::read_to_string(root.join("composition/account.rs")).expect("read composition");
    let native_binance = composition
        .split("pub fn compose_binance_async_account_application")
        .nth(1)
        .and_then(|source| {
            source
                .split("pub fn compose_okx_async_account_application")
                .next()
        })
        .expect("Binance native composition");
    assert!(native_binance.contains("AccountAsyncEventSource::BinanceUsdM"));
    assert!(native_binance.contains("AccountAsyncEventSource::BinanceCoinM"));
    assert!(native_binance.contains("AccountAsyncEventSource::BinanceOptions"));
    assert!(native_binance.contains("AccountAsyncEventSource::BinanceMargin"));
    let integration =
        fs::read_to_string(root.join("services/integration.rs")).expect("read Account integration");
    for collection in [
        "binance_usdm_user_websocket",
        "binance_coinm_user_websocket",
        "binance_options_user_websocket",
        "binance_margin_user_websocket",
    ] {
        assert!(integration.contains(collection), "missing {collection}");
    }
    assert!(!native_binance.contains("UserWebSocketConnection::new"));
    assert!(native_binance.contains("values.isolated_margin_symbol"));
    assert!(!native_binance.contains("market_id.split"));
    assert!(!native_binance.contains("blocking_futures_account_stream"));
    assert!(!native_binance.contains("spawn_blocking"));
    assert!(!composition.contains("\"swap\" | \"usd-m-futures\""));
    assert!(!composition.contains("\"futures\" | \"coin-m-futures\""));
}

#[test]
fn ibkr_account_uses_explicit_virtual_query_and_stream_connections() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    let composition =
        fs::read_to_string(root.join("composition/account.rs")).expect("read composition");
    let native = composition
        .split("pub fn compose_ibkr_async_account_application")
        .nth(1)
        .and_then(|source| source.split("/// Inspect an account credential").next())
        .expect("IBKR native composition");
    assert!(native.contains("AccountAsyncSnapshotConnection::Ibkr"));
    assert!(native.contains("AccountAsyncEventSource::Ibkr"));
    let integration =
        fs::read_to_string(root.join("services/integration.rs")).expect("read Account integration");
    assert!(integration.contains("connections.ibkr_account_query.create"));
    assert!(integration.contains("connections.ibkr_account_stream.create"));
    assert!(!native.contains("IbkrAccountQueryConnection::new"));
    assert!(!native.contains("IbkrAccountStreamConnection::new"));
    assert!(native.contains("client_id: options.client_id.saturating_add(1)"));
    assert!(!native.contains("blocking::account"));
    assert!(!native.contains("spawn_blocking"));

    let server =
        fs::read_to_string(root.join("bin/kairos-account-server.rs")).expect("read Account server");
    assert!(server.contains("exclusive_process_lock(\"ibkr-client\""));
    assert!(server.contains("compose_ibkr_async_account_application"));
}

#[test]
fn reference_is_the_only_owner_of_account_canonical_instrument_identity() {
    let account_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let integration_root = account_root.join("../../platform/integration/src");
    let facts = fs::read_to_string(integration_root.join("domain/account.rs"))
        .expect("read Integration account facts");
    assert!(!facts.contains("canonical_account_identity"));
    assert!(facts.contains("participant_instrument: ParticipantInstrumentRef"));
    assert!(!facts.contains("pub instrument_id: InstrumentId"));
    assert!(!facts.contains("pub market_id: Option<MarketId>"));

    let adapter = fs::read_to_string(account_root.join("src/services/integration.rs"))
        .expect("read Account Integration adapter");
    assert!(adapter.contains("AccountReferenceSnapshot"));
    assert!(adapter.contains("update_reference_snapshot"));
    assert!(!adapter.contains("ReferenceClient"));
    assert!(!adapter.contains("ReferenceViewReader"));
    assert!(!adapter.contains("rusqlite"));
    assert!(!adapter.contains("reference_markets_current"));
    assert!(adapter.contains("Reference identity resolution expected one match"));

    let server = fs::read_to_string(account_root.join("src/bin/kairos-account-server.rs"))
        .expect("read Account server");
    assert!(server.contains("workspace.child(&[\"state\", \"reference\", \"reference.sqlite\"])",));
}

#[test]
fn account_server_bootstrap_selects_only_a_registered_account() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    let server =
        fs::read_to_string(root.join("bin/kairos-account-server.rs")).expect("read Account server");
    assert!(server.contains("account binding is not configured"));
    assert!(server.contains("account binding has no segments"));
    let args = server
        .split("struct Args {")
        .nth(1)
        .and_then(|value| value.split("impl Args").next())
        .expect("Account server Args");
    for forbidden in [
        "api_key: String",
        "secret: String",
        "provider: String",
        "product: String",
        "environment: String",
    ] {
        assert!(
            !args.contains(forbidden),
            "Account server retains connection selection argument: {forbidden}"
        );
    }

    let registry =
        fs::read_to_string(root.join("composition/registry.rs")).expect("read Account registry");
    let domain = fs::read_to_string(root.join("domain/mod.rs")).expect("read Account domain");
    assert!(registry.contains("pub broker: String"));
    assert!(!registry.contains("pub provider: String"));
    assert!(domain.contains("pub broker: BrokerId"));
    assert!(!domain.contains("pub broker: String"));
}

#[test]
fn account_control_plane_does_not_duplicate_balance_or_position_views() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let server = fs::read_to_string(root.join("src/bin/kairos-account-server.rs"))
        .expect("read Account server");
    let actor = fs::read_to_string(root.join("src/application/process/conflux.rs"))
        .expect("read Account actor");
    let contract = fs::read_to_string(root.join("contract/src/control/account.rs"))
        .expect("read Account control contract");
    for obsolete in [
        "/v1/balances",
        "/v1/positions",
        "/v1/open-orders",
        "/v1/account-state",
        "BalancesResponse",
        "PositionsResponse",
    ] {
        assert!(
            !server.contains(obsolete) && !actor.contains(obsolete) && !contract.contains(obsolete),
            "Account control plane duplicates indexed business view: {obsolete}"
        );
    }
    let publisher = fs::read_to_string(root.join("src/services/publication.rs"))
        .expect("read Account indexed publisher");
    assert!(publisher.contains("encode_indexed_current"));
    assert!(publisher.contains("ACCOUNT_BALANCES_DATABASE"));
    assert!(publisher.contains("ACCOUNT_POSITIONS_DATABASE"));
}

#[test]
fn account_cli_separates_standalone_direct_queries_from_launch_connected_views() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let cli =
        fs::read_to_string(root.join("src/bin/kairos-account-cli.rs")).expect("read account cli");
    let standalone = fs::read_to_string(root.join("src/composition/cli.rs"))
        .expect("read standalone Account composition");
    let connected = fs::read_to_string(root.join("src/application/connected.rs"))
        .expect("read connected Account facade");
    let composition = fs::read_to_string(root.join("src/composition/mod.rs"))
        .expect("read connected Account composition");

    assert!(cli.contains("app.balances("));
    assert!(standalone.contains("query_direct_account_snapshot("));
    assert!(!standalone.contains("ConnectedAccountApplication"));
    assert!(!standalone.contains("ConfluxSystem"));
    assert!(!standalone.contains("configure_conflux("));

    assert!(!connected.contains("install_account_connection("));
    assert!(composition.contains("install_account_connection("));
    assert!(composition.contains("account_client("));
    assert!(connected.contains(".indexed_current("));
    assert!(connected.contains("snapshot.observed_orders()"));
    assert!(cli.contains("Account connected mode is launch-scoped"));
    assert!(cli.contains("command.is_indexed_query()"));
    for forbidden in [
        "composition.application.snapshot_query(",
        "composition.application.balances_query(",
        "composition.application.balance_rows_query(",
        "composition.application.positions_query(",
        "composition.application.open_orders_query(",
    ] {
        assert!(!cli.contains(forbidden));
    }
}

#[test]
fn account_application_has_no_synchronous_business_query_facade() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let application =
        fs::read_to_string(root.join("src/application/app.rs")).expect("read Account application");
    let exports = fs::read_to_string(root.join("src/lib.rs")).expect("read Account exports");

    assert!(!root.join("src/application/query.rs").exists());
    for forbidden in [
        "pub fn query(",
        "pub fn snapshot(",
        "pub fn snapshot_query(",
        "pub fn balances(",
        "pub fn balances_query(",
        "pub fn positions(",
        "pub fn positions_query(",
        "pub fn open_orders(",
        "pub fn open_orders_query(",
    ] {
        assert!(
            !application.contains(forbidden),
            "Account application retains synchronous business query {forbidden}"
        );
    }
    assert!(!exports.contains("AccountQuery,"));
    assert!(!exports.contains("AccountDataQuery"));
}

#[test]
fn account_json_rpc_exposes_health_as_typed_control() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let server = fs::read_to_string(root.join("src/bin/kairos-account-server.rs"))
        .expect("read Account server");
    assert!(server.contains("AccountRpcService"));
    assert!(!server.contains("(\"GET\", \"/v1/health\")"));
    assert!(!server.contains("(\"GET\", \"/v1/balances\")"));

    let contract = fs::read_to_string(root.join("contract/src/control/service.rs"))
        .expect("read Account control service");
    assert!(contract.contains("#[conflux_rpc(namespace = \"account\")]"));
    assert!(contract.contains("async fn health"));

    let types = fs::read_to_string(root.join("contract/src/control/account.rs"))
        .expect("read typed Account health contract");
    assert!(types.contains("pub generation: Generation"));
    assert!(types.contains("pub event_sequence: Sequence"));
}
