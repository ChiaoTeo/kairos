use std::path::PathBuf;

fn source(path: &str) -> String {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    std::fs::read_to_string(root.join(path)).expect("read Reference source")
}

#[test]
fn reference_control_transport_is_framework_owned() {
    let manifest = source("Cargo.toml");
    let server = source("src/bin/kairos-reference-server.rs");
    assert!(!manifest.contains("axum.workspace"));
    assert!(server.contains("with_http_control"));
    assert!(server.contains("ReferenceHttpControl"));
    for forbidden in ["axum::", "UnixListener", "TcpListener"] {
        assert!(!server.contains(forbidden));
    }
}

#[test]
fn reference_connections_enter_through_exact_conflux_collections() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let plan = std::fs::read_to_string(root.join("src/services/providers/plan.rs"))
        .expect("read Reference provider plan");
    for collection in [
        ".binance_spot_rest",
        ".binance_usdm_rest",
        ".binance_coinm_rest",
        ".binance_options_rest",
        ".binance_stocks_rest",
        ".okx_public_rest",
        ".hyperliquid_info_rest",
        ".massive_rest",
    ] {
        assert!(plan.contains(collection), "missing {collection}");
    }
    assert!(plan.contains("ConnectionCollections<'_>"));
    assert!(!plan.contains("ConfluxSystem"));

    for path in [
        "src/application/app.rs",
        "src/application/conflux.rs",
        "src/services/actor.rs",
        "src/services/source.rs",
        "src/services/providers/fan_in.rs",
        "src/services/providers/binance.rs",
        "src/services/providers/hyperliquid.rs",
        "src/services/providers/massive.rs",
        "src/services/providers/okx.rs",
    ] {
        let text = source(path);
        assert!(
            !text.contains("&mut kairos_conflux::ConfluxSystem"),
            "Reference runtime must receive typed connection collections, not ConfluxSystem: {path}"
        );
        assert!(
            !text.contains("with_system"),
            "legacy whole-System operation remains in {path}"
        );
    }
    let actor =
        std::fs::read_to_string(root.join("src/services/actor.rs")).expect("read Reference actor");
    assert!(actor.contains("type ActorReferenceSource = ConfiguredReferenceSource"));
    assert!(actor.contains("#[cfg(test)]\ntype ActorReferenceSource = Box<dyn ReferenceSource>"));
    assert!(actor.contains("activate_sources"));
}

#[test]
fn reference_provider_and_storage_paths_are_async_first() {
    let providers = [
        "src/services/providers/binance.rs",
        "src/services/providers/hyperliquid.rs",
        "src/services/providers/massive.rs",
        "src/services/providers/okx.rs",
    ]
    .into_iter()
    .map(source)
    .collect::<String>();
    assert!(!providers.contains("::blocking"));
    assert!(!providers.contains("blocking_instrument_catalog"));
    assert!(!providers.contains("std::thread::Builder"));

    let services = source("src/services/mod.rs");
    assert!(
        services.contains("mod providers"),
        "provider adapters are private Reference services"
    );

    let storage = source("src/services/sqlx_storage.rs");
    assert!(!storage.contains("tokio::runtime::Runtime"));
    assert!(!storage.contains("block_on("));

    let server = source("src/bin/kairos-reference-server.rs");
    assert!(!server.contains("block_in_place"));
}

#[test]
fn concrete_provider_adapters_and_fan_in_remain_separate_service_units() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let providers = root.join("src/services/providers");
    for unit in [
        "binance.rs",
        "fan_in.rs",
        "hyperliquid.rs",
        "massive.rs",
        "okx.rs",
        "tests.rs",
    ] {
        assert!(
            providers.join(unit).is_file(),
            "missing provider unit: {unit}"
        );
    }
    let module = source("src/services/providers/mod.rs");
    assert!(!module.contains("impl ReferenceSource for Binance"));
    assert!(!module.contains("impl ReferenceSource for Okx"));
    assert!(!module.contains("impl ReferenceSource for Hyperliquid"));
    assert!(!module.contains("impl ReferenceSource for Massive"));
    assert!(!module.contains("impl<S> ReferenceSource for CompositeSource"));
}

#[test]
fn reference_domain_classification_is_not_unconstrained_text() {
    let entities = source("src/domain/entities.rs");
    for forbidden in [
        "asset_class: String",
        "instrument_type: String",
        "pub product_family: Option<String>",
        "market_type: String",
        "asset_type: Option<String>",
        "pub provider_id: String",
        "pub product_family: String",
    ] {
        assert!(
            !entities.contains(forbidden),
            "Reference domain classification regressed to raw text: {forbidden}"
        );
    }
    assert!(entities.contains("asset_class: AssetClass"));
    assert!(entities.contains("instrument_type: InstrumentKind"));
    assert!(entities.contains("instrument_kind: InstrumentKind"));
}

#[test]
fn reference_rest_exposes_health_as_its_only_get_query() {
    let server = source("src/bin/kairos-reference-server.rs");
    let codec = source("contract/src/control/http.rs");
    assert!(server.contains("ReferenceHttpControl"));
    assert!(codec.contains("runtime::HEALTH_PATH"));
    assert!(codec.contains("Reference business queries use the contract-owned SQLite client"));
    assert!(codec.contains("request.method == \"GET\""));
    assert!(codec.contains("ControlAction::Request"));
    assert!(!server.contains("mpsc::channel"));
    assert!(!server.contains("oneshot::channel"));
    let contract = source("contract/src/control/types.rs");
    let health = contract
        .split("pub struct ReferenceHealthResponse")
        .nth(1)
        .expect("Reference health response")
        .split("pub struct ReferenceProviderHealth")
        .next()
        .expect("Reference health response body");
    for forbidden in [
        "actor_id",
        "generation",
        "event_sequence",
        "market_count",
        "outbox_depth",
        "control_queue_depth",
        "last_attempt_unix_nanos",
        "last_success_unix_nanos",
        "consecutive_failures",
    ] {
        assert!(
            !health.contains(forbidden),
            "Reference health leaks {forbidden}"
        );
    }
}

#[test]
fn reference_uses_sqlite_as_its_only_current_fact_store() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let composition = source("src/composition/mod.rs");
    let server = source("src/bin/kairos-reference-server.rs");
    let actor = source("src/services/actor.rs");
    assert!(!composition.contains("MmapReferenceProjectionPublisher"));
    assert!(!composition.contains("ReferenceViewKey"));
    assert!(!server.contains("ReferenceCurrentViewPublisher"));
    assert!(!server.contains("current_view_publisher"));
    assert!(!server.contains("snapshot_publish_failed"));
    assert!(actor.contains("SqlxCatalogStore"));
    assert!(!actor.contains("dyn CatalogStore"));
    assert!(!root.join("src/services/store.rs").exists());

    let schemas = root.join("../../../schemas/v2/reference");
    assert!(!schemas.join("views/reference_latest.fbs").exists());
    assert!(!schemas.join("views/reference_projection.fbs").exists());
    assert!(!schemas.join("types/financial_product.fbs").exists());
    assert!(!schemas.join("types/provider.fbs").exists());
    assert!(!schemas.join("types/broker.fbs").exists());
    assert!(!schemas.join("types/exchange.fbs").exists());
}

#[test]
fn administrative_writes_enter_through_application_commands() {
    let application = source("src/application/app.rs");
    let commands = source("src/application/commands.rs");
    let contract = source("contract/src/control/types.rs");
    let server = source("src/bin/kairos-reference-server.rs");
    assert!(application.contains("command: UpsertAssetCommand"));
    assert!(application.contains("command: UpsertInstrumentCommand"));
    assert!(application.contains("command: UpsertListingCommand"));
    assert!(commands.contains("pub use kairos_reference_contract"));
    assert!(contract.contains("pub struct UpsertAssetRequest"));
    assert!(contract.contains("pub struct UpsertInstrumentRequest"));
    assert!(contract.contains("pub struct UpsertListingRequest"));
    for domain_payload in [
        "from_str::<Asset>",
        "from_str::<Instrument>",
        "from_str::<Listing>",
    ] {
        assert!(
            !server.contains(domain_payload),
            "transport must not deserialize a domain entity: {domain_payload}"
        );
    }
}

#[test]
fn broker_and_data_provider_products_do_not_invent_canonical_venues() {
    let binance = source("src/services/providers/binance.rs");
    let equity_mapping = binance
        .split("pub(super) fn binance_equity_provider_catalog")
        .nth(1)
        .expect("Binance Equity mapping")
        .split("pub(super) fn binance_provider_catalog")
        .next()
        .expect("Binance Equity mapping body");
    for forbidden in [
        "exchange:binance",
        "listing:binance:equity",
        "market:binance:equity",
        "catalog.listings.push",
        "catalog.markets.push",
    ] {
        assert!(
            !equity_mapping.contains(forbidden),
            "Binance Equity broker facts must not create canonical venue facts: {forbidden}"
        );
    }

    let massive = source("src/services/providers/massive.rs");
    for forbidden in ["listing:massive", "market:massive"] {
        assert!(
            !massive.contains(forbidden),
            "Massive data-provider identity must not become canonical identity: {forbidden}"
        );
    }
    assert!(massive.contains("\"OPRA\" => None"));
    assert!(massive.contains("\"BATO\" => Some(\"exchange:cboe-bzx-options\".into())"));
}
