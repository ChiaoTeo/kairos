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
        assert!(source.contains("attach_async_sources"));
    }

    assert!(!composition.contains("pub async fn refresh_report"));
    let application =
        fs::read_to_string(root.join("application/service.rs")).expect("read application facade");
    assert!(application.contains("pub async fn refresh_report_async"));
    assert!(application.contains("pub async fn refresh_market_profile_async"));
}

#[test]
fn account_process_gates_readiness_and_tracks_external_stream_continuity() {
    let process = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/application/process.rs"),
    )
    .expect("read account process");
    assert!(process.contains("initial_refresh_complete"));
    assert!(process.contains("async_stream_health"));
    assert!(process.contains("external_event_watermarks"));
    assert!(process.contains("account stream sequence gap"));
    assert!(process.contains("provider_event_id"));
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

    let composition =
        fs::read_to_string(root.join("composition/account.rs")).expect("read account composition");
    assert!(!composition.contains("compose_blocking_account_stream"));
    assert!(!composition.contains("attach_account_stream"));
    assert!(!composition.contains("pub fn compose_account_application_for_segments"));
    assert!(composition.contains("pub fn compose_blocking_account_application_for_segments"));

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
    assert!(native_binance.contains("AccountAsyncEventSource::BinanceFutures"));
    assert!(native_binance.contains("AccountAsyncEventSource::BinanceOptions"));
    assert!(native_binance.contains("AccountAsyncEventSource::BinanceMargin"));
    assert!(native_binance.contains("BinanceFuturesChannelConfig"));
    assert!(native_binance.contains("BinanceOptionsChannelConfig"));
    assert!(native_binance.contains("BinanceMarginChannelConfig"));
    assert!(native_binance.contains("isolated_margin_connection(provider_symbol)"));
    assert!(native_binance.contains("values.isolated_margin_symbol"));
    assert!(!native_binance.contains("market_id.split"));
    assert!(!native_binance.contains("blocking_futures_account_stream"));
    assert!(!native_binance.contains("spawn_blocking"));
}

#[test]
fn ibkr_account_uses_one_native_async_hard_session() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    let composition =
        fs::read_to_string(root.join("composition/account.rs")).expect("read composition");
    let native = composition
        .split("pub fn compose_ibkr_async_account_application")
        .nth(1)
        .and_then(|source| source.split("/// Inspect an account credential").next())
        .expect("IBKR native composition");
    assert!(native.contains("IbkrConnection::connect"));
    assert!(native.contains("connection.account_read()"));
    assert!(native.contains(".account_events("));
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
    let integration_root = account_root.join("../../../kairos-integration/src");
    let facts =
        fs::read_to_string(integration_root.join("application/capabilities/account_facts.rs"))
            .expect("read Integration account facts");
    assert!(!facts.contains("canonical_account_identity"));
    assert!(facts.contains("provider_instrument: ProviderInstrumentRef"));
    assert!(!facts.contains("pub instrument_id: InstrumentId"));
    assert!(!facts.contains("pub market_id: Option<MarketId>"));

    let adapter = fs::read_to_string(account_root.join("src/services/integration.rs"))
        .expect("read Account Integration adapter");
    assert!(adapter.contains("ReferenceMmapSnapshotSetReader"));
    assert!(adapter.contains("Reference identity resolution expected one match"));

    let server = fs::read_to_string(account_root.join("src/bin/kairos-account-server.rs"))
        .expect("read Account server");
    assert!(server.contains("workspace.child(&[\"snapshots\", \"reference\"])"));
}
