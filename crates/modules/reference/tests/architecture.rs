use std::path::PathBuf;

fn source(path: &str) -> String {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    std::fs::read_to_string(root.join(path)).expect("read Reference source")
}

#[test]
fn reference_provider_and_storage_paths_are_async_first() {
    let providers = [
        "src/composition/providers/binance.rs",
        "src/composition/providers/hyperliquid.rs",
        "src/composition/providers/massive.rs",
        "src/composition/providers/okx.rs",
    ]
    .into_iter()
    .map(source)
    .collect::<String>();
    assert!(!providers.contains("kairos_integration::blocking"));
    assert!(!providers.contains("blocking_instrument_catalog"));
    assert!(!providers.contains("std::thread::Builder"));

    let services = source("src/services/mod.rs");
    assert!(
        !services.contains("mod providers"),
        "concrete provider clients belong to composition"
    );

    let storage = source("src/services/sqlx_storage.rs");
    assert!(!storage.contains("tokio::runtime::Runtime"));
    assert!(!storage.contains("block_on("));

    let server = source("src/bin/kairos-reference-server.rs");
    assert!(!server.contains("block_in_place"));
}

#[test]
fn concrete_providers_and_fan_in_remain_separate_composition_units() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let providers = root.join("src/composition/providers");
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
    let module = source("src/composition/providers/mod.rs");
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
    assert!(entities.contains("market_type: ProviderProductCode"));
    assert!(entities.contains("provider_product: ProviderProductCode"));
}

#[test]
fn reference_rest_exposes_health_as_its_only_get_query() {
    let server = source("src/bin/kairos-reference-server.rs");
    assert!(server.contains("method == \"GET\" && path != control::HEALTH"));
    assert!(server
        .contains("Reference business queries use the contract-owned read-only SQLite client"));
    assert!(server.contains("path == control::HEALTH && method != \"GET\""));
    let health = server
        .split("fn health_json(application")
        .nth(1)
        .expect("Reference health functions")
        .split("fn reference_status")
        .next()
        .expect("Reference health bodies");
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
    let server = source("src/bin/kairos-reference-server.rs");
    assert!(application.contains("command: UpsertAssetCommand"));
    assert!(application.contains("command: UpsertInstrumentCommand"));
    assert!(application.contains("command: UpsertListingCommand"));
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
