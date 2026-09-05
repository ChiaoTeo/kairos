use std::path::{Path, PathBuf};

// This file contains only prohibitions whose ownership meaning is not already
// enforced by Cargo, visibility, repository layer checks, or behavior tests.

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn source(path: &str) -> String {
    std::fs::read_to_string(root().join(path)).expect("read Reference source")
}

fn rust_files(path: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    for entry in std::fs::read_dir(path).expect("read Reference source directory") {
        let path = entry.expect("read Reference source entry").path();
        if path.is_dir() {
            files.extend(rust_files(&path));
        } else if path.extension().and_then(|value| value.to_str()) == Some("rs") {
            files.push(path);
        }
    }
    files
}

#[test]
fn control_transport_is_framework_owned() {
    let manifest = source("Cargo.toml");
    let server = source("src/bin/kairos-reference-server.rs");
    assert!(!manifest.contains("axum.workspace"));
    assert!(!manifest.contains("kairos-transport"));
    for forbidden in [
        "with_http_control",
        "ReferenceHttpControl",
        "axum::",
        "UnixListener",
        "TcpListener",
    ] {
        assert!(!server.contains(forbidden));
    }
}

#[test]
fn source_workflow_does_not_regress_to_generic_factories() {
    for path in rust_files(&root().join("src")) {
        let source = std::fs::read_to_string(&path).unwrap();
        for forbidden in [
            "ProviderUpdate",
            "CompositeSource",
            "ProviderFactory",
            "SourceFactory",
            "ConnectionFactory",
            "FactoryRegistry",
            "ProviderAdapterRegistry",
        ] {
            assert!(
                !source.contains(forbidden),
                "{forbidden} in {}",
                path.display()
            );
        }
    }
}

#[test]
fn provider_and_storage_paths_are_async_first() {
    let providers = [
        "src/services/providers/binance.rs",
        "src/services/providers/hyperliquid.rs",
        "src/services/providers/massive.rs",
        "src/services/providers/okx.rs",
    ]
    .into_iter()
    .map(source)
    .collect::<String>();
    for forbidden in [
        "::blocking",
        "blocking_instrument_catalog",
        "std::thread::Builder",
    ] {
        assert!(!providers.contains(forbidden));
    }
    let storage = source("src/services/sqlx_storage.rs");
    assert!(!storage.contains("tokio::runtime::Runtime"));
    assert!(!storage.contains("block_on("));
    assert!(!source("src/bin/kairos-reference-server.rs").contains("block_in_place"));
}

#[test]
fn domain_classification_is_not_raw_text() {
    let entities = source("src/domain/entities.rs");
    for forbidden in [
        "asset_class: String",
        "instrument_type: String",
        "pub product_family: Option<String>",
        "pub provider_segment: Option<String>",
        "market_type: String",
        "asset_type: Option<String>",
        "pub provider_id: String",
    ] {
        assert!(!entities.contains(forbidden));
    }
}

#[test]
fn control_contract_has_no_rest_compatibility_facade() {
    let server = source("src/bin/kairos-reference-server.rs");
    let types = source("contract/src/control/types.rs");
    assert!(!server.contains("ReferenceHttpControl"));
    assert!(!server.contains("with_http_control"));
    assert!(!types.contains("ReferenceRestRequest"));
    assert!(!types.contains("ReferenceRestResponse"));
    assert!(!source("contract/Cargo.toml").contains("kairos-conflux"));
}

#[test]
fn workspace_configuration_hides_internal_source_bindings() {
    let config = source("src/composition/config.rs");
    for forbidden in [
        "BTreeMap",
        "ReferenceSourceBinding",
        "source_id",
        "sync_policy",
        "provider_product",
        "provider_segment",
        "ReferenceProviders",
        "credential_id",
        "endpoint",
    ] {
        assert!(!config.contains(forbidden));
    }
}

#[test]
fn v3_contract_separates_venues_memberships_and_coverage() {
    let model = source("contract/src/catalog/model.rs");
    let sqlite = source("contract/src/catalog/sqlite.rs");
    for required in [
        "pub struct Venue",
        "pub struct VenueListing",
        "pub struct VenueMarket",
        "pub struct ProviderCatalogMembership",
        "pub struct ReferenceCoverage",
        "pub struct ReferenceQueryEvidence",
        "pub fn search_venues",
        "pub fn search_venue_markets",
    ] {
        assert!(
            model.contains(required) || sqlite.contains(required),
            "{required}"
        );
    }
    assert!(model.contains("listing_venue_id"));
    assert!(model.contains("execution_venue_id"));
}

#[test]
fn sqlite_is_the_only_current_fact_store() {
    let composition = source("src/composition/mod.rs");
    let server = source("src/bin/kairos-reference-server.rs");
    let actor = source("src/services/actor.rs");
    assert!(!composition.contains("MmapReferenceViewPublisher"));
    assert!(!composition.contains("ReferenceViewKey"));
    assert!(!server.contains("ReferenceCurrentViewPublisher"));
    assert!(!server.contains("current_view_publisher"));
    assert!(!actor.contains("dyn CatalogStore"));
}

#[test]
fn public_contract_has_no_catalog_or_consumer_snapshots() {
    for path in rust_files(&root().join("contract/src")) {
        let source = std::fs::read_to_string(&path).unwrap();
        for forbidden in [
            "ReferenceCatalogSnapshot",
            "MarketReferenceSnapshot",
            "ExecutionReferenceSnapshot",
            "AccountReferenceSnapshot",
        ] {
            assert!(
                !source.contains(forbidden),
                "{forbidden} in {}",
                path.display()
            );
        }
    }
}

#[test]
fn transport_does_not_deserialize_domain_write_models() {
    let server = source("src/bin/kairos-reference-server.rs");
    for forbidden in [
        "from_str::<Asset>",
        "from_str::<Instrument>",
        "from_str::<Listing>",
    ] {
        assert!(!server.contains(forbidden));
    }
}

#[test]
fn provider_products_do_not_invent_canonical_venues() {
    let binance = source("src/services/providers/binance.rs");
    let equity = binance
        .split("pub(super) fn binance_equity_provider_catalog")
        .nth(1)
        .unwrap()
        .split("pub(super) fn binance_provider_catalog")
        .next()
        .unwrap();
    for forbidden in [
        "exchange:binance",
        "listing:binance:equity",
        "market:binance:equity",
        "catalog.listings.push",
        "catalog.markets.push",
    ] {
        assert!(!equity.contains(forbidden));
    }
    let massive = source("src/services/providers/massive.rs");
    assert!(!massive.contains("listing:massive"));
    assert!(!massive.contains("market:massive"));
}
