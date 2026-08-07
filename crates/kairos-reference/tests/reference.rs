use kairos_reference::application::{CatalogStore, ReferenceSource};
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
