use std::path::PathBuf;

fn source(path: &str) -> String {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    std::fs::read_to_string(root.join(path)).expect("read Reference source")
}

#[test]
fn reference_provider_and_storage_paths_are_async_first() {
    let providers = source("src/services/providers.rs");
    assert!(!providers.contains("kairos_integration::blocking"));
    assert!(!providers.contains("blocking_instrument_catalog"));
    assert!(!providers.contains("std::thread::Builder"));

    let storage = source("src/services/sqlx_storage.rs");
    assert!(!storage.contains("tokio::runtime::Runtime"));
    assert!(!storage.contains("block_on("));

    let server = source("src/bin/kairos-reference-server.rs");
    assert!(!server.contains("block_in_place"));
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
