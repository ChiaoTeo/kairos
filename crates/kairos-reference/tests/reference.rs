use kairos_reference::application::{CatalogStore, ReferenceSource};
use kairos_reference::composition::{build_application, ReferenceCompositionConfig};
use kairos_reference::domain::{
    Asset, Entity, Instrument, Listing, Market, ProviderCatalog, ReferenceCatalog, ReferenceResult,
};
use kairos_reference::{
    MarketQuery, ReferenceApplication, ReferenceKind, ReferenceQuery, ReferenceReader,
    ReferenceRecord,
};

struct TestSource {
    catalog: ProviderCatalog,
}

impl ReferenceSource for TestSource {
    fn source_id(&self) -> &str {
        "test"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Ok(self.catalog.clone())
    }
}

#[derive(Default)]
struct TestStore(Option<ReferenceCatalog>);

impl CatalogStore for TestStore {
    fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
        Ok(self.0.clone())
    }

    fn save(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<()> {
        self.0 = Some(catalog.clone());
        Ok(())
    }
}

fn application() -> ReferenceApplication {
    ReferenceApplication::new(
        "reference-test",
        Box::new(TestSource {
            catalog: provider_catalog(),
        }),
        Box::new(TestStore::default()),
    )
    .unwrap()
}

fn provider_catalog() -> ProviderCatalog {
    ProviderCatalog {
        entities: vec![Entity {
            entity_id: "binance".into(),
            entity_type: "venue".into(),
            name: "Binance".into(),
            status: "active".into(),
        }],
        assets: vec![Asset {
            asset_id: "asset:btc".into(),
            code: "BTC".into(),
            name: Some("Bitcoin".into()),
            asset_class: "crypto".into(),
            status: "active".into(),
        }],
        instruments: vec![Instrument {
            instrument_id: "instrument:binance:spot:BTCUSDT".into(),
            symbol: "BTC/USDT".into(),
            name: Some("BTC/USDT spot".into()),
            instrument_type: "spot".into(),
            product_family: Some("spot".into()),
            status: "active".into(),
            ..Default::default()
        }],
        listings: vec![Listing {
            listing_id: "listing:binance:BTCUSDT".into(),
            instrument_id: "instrument:binance:spot:BTCUSDT".into(),
            venue_id: "binance".into(),
            venue_symbol: "BTCUSDT".into(),
            status: "active".into(),
            effective_from_unix_nanos: 1,
            ..Default::default()
        }],
        markets: vec![Market {
            market_id: "market:binance:spot:BTCUSDT".into(),
            market_key: "binance.spot.BTCUSDT".into(),
            instrument_id: "instrument:binance:spot:BTCUSDT".into(),
            listing_id: "listing:binance:BTCUSDT".into(),
            venue_id: "binance".into(),
            market_type: "spot".into(),
            asset_type: Some("crypto".into()),
            source_symbol: "BTCUSDT".into(),
            base_asset_id: Some("asset:btc".into()),
            quote_asset_id: Some("asset:usdt".into()),
            status: "active".into(),
            price_precision: 2,
            quantity_precision: 6,
            effective_from_unix_nanos: 1,
            ..Default::default()
        }],
    }
}

#[test]
fn application_reconciles_reference_catalog() {
    let mut application = application();
    let result = application.refresh().unwrap();
    assert_eq!(result.events.len(), 1);
    assert_eq!(result.generation, 1);
    assert_eq!(application.catalog().markets.len(), 1);
}

#[test]
fn application_exposes_read_only_market_queries() {
    let mut application = application();
    application.refresh().unwrap();

    let query = MarketQuery {
        venue_id: Some("binance".into()),
        market_type: Some("spot".into()),
        asset_type: Some("crypto".into()),
        source_symbol: Some("btcusdt".into()),
        active_only: true,
        ..MarketQuery::default()
    };
    assert_eq!(application.markets(&query).len(), 1);
    assert_eq!(
        application.resolve_market(&query).unwrap().market_id,
        "market:binance:spot:BTCUSDT"
    );
    assert_eq!(ReferenceReader::markets(&application, &query).len(), 1);
}

#[test]
fn workspace_reference_composes_all_market_product_sources_without_network() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path();
    std::fs::create_dir_all(root.join("credentials")).unwrap();
    std::fs::write(
        root.join("kairos.toml"),
        r#"version = 1
workspace_id = "reference-test"

[market.connections.binance-spot]
provider = "binance-spot-rest"
[market.connections.binance-usdm]
provider = "binance-usdm-futures-rest"
[market.connections.binance-coinm]
provider = "binance-coinm-futures-rest"
[market.connections.binance-options]
provider = "binance-options-rest"
[market.connections.binance-equity]
provider = "binance-equity-rest"
credential_id = "binance-readonly"
[market.connections.okx-spot]
provider = "okx-spot-rest"
asset_type = "crypto"
[market.connections.okx-equity]
provider = "okx-spot-rest"
asset_type = "equity"
[market.connections.okx-swap]
provider = "okx-swap-rest"
[market.connections.okx-futures]
provider = "okx-futures-rest"
[market.connections.okx-options]
provider = "okx-options-rest"
[market.connections.massive-equity]
provider = "massive-equity-websocket"
credential_id = "massive-readonly"
[market.connections.massive-options]
provider = "massive-options-websocket"
credential_id = "massive-readonly"
"#,
    )
    .unwrap();
    for (id, provider) in [
        ("binance-readonly", "binance"),
        ("massive-readonly", "massive"),
    ] {
        std::fs::write(
            root.join("credentials").join(format!("{id}.toml")),
            format!("[credential]\nid = \"{id}\"\nprovider = \"{provider}\"\napi_key = \"test-key\"\napi_secret = \"test-secret\"\n"),
        )
        .unwrap();
    }
    let composition = build_application(
        &ReferenceCompositionConfig {
            workspace: Some(root.to_path_buf()),
            provider: "workspace".into(),
            endpoint: "https://example.invalid".into(),
            database: root.join("reference.sqlite"),
            api_key: String::new(),
            binance_api_key: String::new(),
            secret: String::new(),
            underlying: "SPY".into(),
            aeron_dir: None,
            channel: "aeron:udp?endpoint=localhost:40123".into(),
            catalog_stream: 1201,
            markets_stream: 1202,
            lifecycle_stream: 1203,
            changes_stream: 1204,
        },
        false,
    )
    .unwrap();
    assert_eq!(composition.application.source_id(), "workspace");
}

#[test]
fn application_does_not_emit_duplicate_events_for_same_catalog() {
    let mut application = application();
    assert_eq!(application.refresh().unwrap().events.len(), 1);
    let second = application.refresh().unwrap();
    assert!(second.events.is_empty());
    assert_eq!(second.event_sequence, 1);
}

#[test]
fn application_query_covers_each_reference_record_kind() {
    let mut application = application();
    application.refresh().unwrap();

    let markets = application.query(&ReferenceQuery {
        kind: ReferenceKind::Market,
        text: Some("BTCUSDT".into()),
        ..ReferenceQuery::default()
    });
    assert!(matches!(markets.as_slice(), [ReferenceRecord::Market(_)]));

    let all = application.query(&ReferenceQuery {
        kind: ReferenceKind::All,
        text: Some("binance".into()),
        ..ReferenceQuery::default()
    });
    assert!(all
        .iter()
        .any(|record| matches!(record, ReferenceRecord::Entity(_))));
    assert!(application.record("market:binance:spot:BTCUSDT").is_ok());
}
