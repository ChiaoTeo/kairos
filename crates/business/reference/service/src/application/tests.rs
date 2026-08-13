use kairos_domain_types::{AssetId, Exchange, InstrumentId, ListingId, MarketId, Symbol};
use kairos_reference::composition::{build_application, ReferenceCompositionConfig};
use kairos_reference::domain::{
    Asset, Entity, FinancialProduct, Instrument, Listing, Market, ProviderCatalog,
    ReferenceCatalog, ReferenceError, ReferenceResult,
};
use kairos_reference::services::providers::ReferenceSource;
use kairos_reference::services::store::CatalogStore;
use kairos_reference::{
    LifecycleQuery, MarketQuery, ReferenceApplication, ReferenceKind, ReferenceQuery,
    ReferenceRecord,
};

struct TestSource {
    catalog: ProviderCatalog,
}

struct SequenceSource {
    catalogs: Vec<ProviderCatalog>,
    index: usize,
}

fn instrument_id(value: &str) -> InstrumentId {
    InstrumentId::new(value).unwrap()
}

fn listing_id(value: &str) -> ListingId {
    ListingId::new(value).unwrap()
}

fn market_id(value: &str) -> MarketId {
    MarketId::new(value).unwrap()
}

fn asset_id(value: &str) -> AssetId {
    AssetId::new(value).unwrap()
}

fn symbol(value: &str) -> Symbol {
    Symbol::new(value).unwrap()
}

impl ReferenceSource for SequenceSource {
    fn source_id(&self) -> &str {
        "sequence-test"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let catalog = self
            .catalogs
            .get(self.index.min(self.catalogs.len().saturating_sub(1)))
            .cloned()
            .unwrap_or_default();
        self.index = self.index.saturating_add(1);
        Ok(catalog)
    }
}

impl ReferenceSource for TestSource {
    fn source_id(&self) -> &str {
        "test"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Ok(self.catalog.clone())
    }
}

#[derive(Default)]
struct TestStore(Option<ReferenceCatalog>);

struct FailingStore(Option<ReferenceCatalog>);

impl CatalogStore for TestStore {
    async fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
        Ok(self.0.clone())
    }

    async fn save_refresh(
        &mut self,
        catalog: &ReferenceCatalog,
        _events: &[kairos_reference::domain::LifecycleEvent],
    ) -> ReferenceResult<()> {
        self.0 = Some(catalog.clone());
        Ok(())
    }
}

impl CatalogStore for FailingStore {
    async fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
        Ok(self.0.clone())
    }

    async fn save_refresh(
        &mut self,
        _catalog: &ReferenceCatalog,
        _events: &[kairos_reference::domain::LifecycleEvent],
    ) -> ReferenceResult<()> {
        Err(ReferenceError::Persistence("injected failure".into()))
    }
}

async fn application() -> ReferenceApplication<TestSource, TestStore> {
    ReferenceApplication::new(
        "reference-test",
        TestSource {
            catalog: provider_catalog(),
        },
        TestStore::default(),
    )
    .await
    .unwrap()
}

fn provider_catalog() -> ProviderCatalog {
    ProviderCatalog {
        entities: vec![Entity {
            entity_id: "exchange:binance".into(),
            entity_type: "exchange".into(),
            name: "Binance".into(),
            status: "active".into(),
            ..Default::default()
        }],
        assets: vec![
            Asset {
                asset_id: asset_id("asset:BTC"),
                code: "BTC".into(),
                name: Some("Bitcoin".into()),
                asset_class: "crypto".into(),
                status: "active".into(),
                ..Default::default()
            },
            Asset {
                asset_id: asset_id("asset:USDT"),
                code: "USDT".into(),
                asset_class: "crypto".into(),
                status: "active".into(),
                ..Default::default()
            },
        ],
        instruments: vec![Instrument {
            instrument_id: instrument_id("instrument:spot:BTC"),
            symbol: symbol("BTC"),
            name: Some("BTC spot instrument".into()),
            instrument_type: "spot".into(),
            product_family: Some("spot".into()),
            status: "active".into(),
            ..Default::default()
        }],
        listings: vec![Listing {
            listing_id: listing_id("listing:binance:spot:BTC:USDT"),
            instrument_id: instrument_id("instrument:spot:BTC"),
            exchange_id: Exchange::new("exchange:binance").unwrap(),
            exchange_symbol: Symbol::new("BTCUSDT").unwrap(),
            status: "active".into(),
            effective_from_unix_nanos: 1.into(),
            ..Default::default()
        }],
        markets: vec![Market {
            market_id: market_id("market:binance:spot:BTCUSDT"),
            market_key: "binance.spot.BTCUSDT".into(),
            instrument_id: instrument_id("instrument:spot:BTC"),
            listing_id: listing_id("listing:binance:spot:BTC:USDT"),
            exchange_id: Exchange::new("exchange:binance").unwrap(),
            market_type: "spot".into(),
            asset_type: Some("crypto".into()),
            source_symbol: symbol("BTCUSDT"),
            base_asset_id: Some(asset_id("asset:BTC")),
            quote_asset_id: Some(asset_id("asset:USDT")),
            status: "active".into(),
            price_precision: 2,
            quantity_precision: 6,
            effective_from_unix_nanos: 1.into(),
            ..Default::default()
        }],
        financial_products: vec![FinancialProduct {
            product_id: "product:binance:earn:btc".into(),
            product_type: "earn".into(),
            name: "BTC Earn".into(),
            asset_id: asset_id("asset:BTC"),
            provider_product_id: "btc-earn".into(),
            provider_id: Some("binance".into()),
            status: "active".into(),
            effective_from_unix_nanos: 1.into(),
            ..Default::default()
        }],
        ..Default::default()
    }
}

#[test]
fn one_listing_can_back_multiple_markets_on_different_exchanges() {
    let mut catalog = provider_catalog();
    catalog.entities.push(Entity {
        entity_id: "exchange:iex".into(),
        entity_type: "exchange".into(),
        name: "IEX".into(),
        status: "active".into(),
        ..Default::default()
    });
    let mut iex_market = catalog.markets[0].clone();
    iex_market.market_id = market_id("market:iex:spot:BTCUSDT");
    iex_market.market_key = "iex.spot.BTCUSDT".into();
    iex_market.exchange_id = Exchange::new("exchange:iex").unwrap();
    catalog.markets.push(iex_market);

    catalog
        .validate()
        .expect("one listing may reference multiple trading exchanges");
}

#[tokio::test]
async fn application_reconciles_reference_catalog() {
    let mut application = application().await;
    let result = application.refresh().await.unwrap();
    assert_eq!(result.events.len(), 7);
    assert_eq!(result.generation, 1.into());
    assert_eq!(application.catalog().markets.len(), 1);
}

#[tokio::test]
async fn application_exposes_read_only_market_queries() {
    let mut application = application().await;
    application.refresh().await.unwrap();

    let query = MarketQuery {
        exchange_id: Some(Exchange::new("exchange:binance").unwrap()),
        market_type: Some("spot".into()),
        asset_type: Some("crypto".into()),
        source_symbol: Some(kairos_domain_types::Symbol::new("btcusdt").unwrap()),
        active_only: true,
        ..MarketQuery::default()
    };
    assert_eq!(application.markets(&query).len(), 1);
    assert_eq!(
        application.resolve_market(&query).unwrap().market_id,
        "market:binance:spot:BTCUSDT"
    );
}

#[tokio::test]
async fn default_reference_registry_composes_without_market_configuration() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path();
    std::fs::write(
        root.join("kairos.toml"),
        "version = 1\nworkspace_id = \"reference-test\"\n",
    )
    .unwrap();
    let composition = build_application(
        &ReferenceCompositionConfig {
            workspace: Some(root.to_path_buf()),
            database: root.join("reference.sqlite"),
            aeron_dir: None,
            aeron_channel: kairos_transport::DEFAULT_CHANNEL.into(),
            reference_changes_stream: kairos_transport::stream_ids::REFERENCE_CHANGES,
        },
        false,
    )
    .await
    .unwrap();
    assert_eq!(composition.application.source_id(), "reference-default");
}

#[tokio::test]
async fn application_does_not_emit_duplicate_events_for_same_catalog() {
    let mut application = application().await;
    assert_eq!(application.refresh().await.unwrap().events.len(), 7);
    let second = application.refresh().await.unwrap();
    assert!(second.events.is_empty());
    assert_eq!(second.event_sequence, 7.into());
}

#[tokio::test]
async fn failed_refresh_does_not_advance_in_memory_catalog() {
    let mut application = ReferenceApplication::new(
        "reference-test",
        TestSource {
            catalog: provider_catalog(),
        },
        FailingStore(None),
    )
    .await
    .unwrap();
    let before = application.catalog().clone();

    let error = application.refresh().await.unwrap_err();

    assert!(matches!(error, ReferenceError::Persistence(_)));
    assert_eq!(application.catalog(), &before);
}

#[tokio::test]
async fn failed_administrative_commit_does_not_advance_in_memory_catalog() {
    let mut persisted = ReferenceCatalog::default();
    persisted.apply(provider_catalog(), 1.into());
    let mut application = ReferenceApplication::new(
        "reference-test",
        TestSource {
            catalog: provider_catalog(),
        },
        FailingStore(Some(persisted)),
    )
    .await
    .unwrap();
    let before = application.catalog().clone();

    let error = application
        .upsert_asset(Asset {
            asset_id: asset_id("asset:SOL"),
            code: "SOL".into(),
            asset_class: "crypto".into(),
            status: "active".into(),
            ..Default::default()
        })
        .await
        .unwrap_err();

    assert!(matches!(error, ReferenceError::Persistence(_)));
    assert_eq!(application.catalog(), &before);
}

#[tokio::test]
async fn administrative_asset_upsert_is_versioned_and_emits_a_reference_event() {
    let mut application = application().await;
    application.refresh().await.unwrap();
    let generation = application
        .upsert_asset(Asset {
            asset_id: asset_id("asset:sol"),
            code: "SOL".into(),
            asset_class: "crypto".into(),
            status: "active".into(),
            ..Default::default()
        })
        .await
        .unwrap();
    assert_eq!(generation, 2.into());
    let event = application
        .catalog()
        .lifecycle_events
        .last()
        .expect("asset upsert event");
    assert_eq!(event.event_type, "asset_changed");
    assert_eq!(event.record_kind.as_deref(), Some("asset"));
    assert_eq!(event.record_id.as_deref(), Some("asset:sol"));
    assert_eq!(application.catalog().event_sequence, 8.into());
}

#[tokio::test]
async fn administrative_instrument_and_listing_upserts_share_commit_path() {
    let mut application = application().await;
    application.refresh().await.unwrap();
    let generation = application
        .upsert_instrument(Instrument {
            instrument_id: instrument_id("instrument:spot:ETH"),
            symbol: symbol("ETH/USDT"),
            instrument_type: "spot".into(),
            status: "active".into(),
            ..Default::default()
        })
        .await
        .unwrap();
    assert_eq!(generation, 2.into());
    let generation = application
        .upsert_listing(Listing {
            listing_id: listing_id("listing:binance:spot:ETH:USDT"),
            instrument_id: instrument_id("instrument:spot:ETH"),
            exchange_id: Exchange::new("exchange:binance").unwrap(),
            exchange_symbol: Symbol::new("ETHUSDT").unwrap(),
            status: "active".into(),
            effective_from_unix_nanos: 1.into(),
            ..Default::default()
        })
        .await
        .unwrap();
    assert_eq!(generation, 3.into());
    let events = application
        .catalog()
        .lifecycle_events
        .iter()
        .rev()
        .take(2)
        .collect::<Vec<_>>();
    assert_eq!(events[0].record_kind.as_deref(), Some("listing"));
    assert_eq!(events[1].record_kind.as_deref(), Some("instrument"));
    assert_eq!(application.catalog().lifecycle_events.len(), 9);
}

#[tokio::test]
async fn application_query_covers_each_reference_record_kind() {
    let mut application = application().await;
    application.refresh().await.unwrap();

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
    assert!(all
        .iter()
        .any(|record| matches!(record, ReferenceRecord::FinancialProduct(_))));
    let asset_events = application.query(&ReferenceQuery {
        kind: ReferenceKind::Event,
        record_kind: Some("asset".into()),
        ..ReferenceQuery::default()
    });
    assert_eq!(asset_events.len(), 2);
    assert!(application.record("market:binance:spot:BTCUSDT").is_ok());
}

#[tokio::test]
async fn instrument_underlying_is_a_query_filter_not_a_sync_scope() {
    let mut catalog = provider_catalog();
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id("instrument:equity:SPY"),
        symbol: symbol("SPY"),
        instrument_type: "equity".into(),
        status: "active".into(),
        ..Default::default()
    });
    catalog.instruments[0].underlying_instrument_id = Some(instrument_id("instrument:equity:SPY"));
    let mut application = ReferenceApplication::new(
        "reference-test",
        TestSource { catalog },
        TestStore::default(),
    )
    .await
    .unwrap();
    application.refresh().await.unwrap();

    let records = application.query(&ReferenceQuery {
        kind: ReferenceKind::Instrument,
        underlying_instrument_id: Some("instrument:equity:SPY".into()),
        ..ReferenceQuery::default()
    });
    assert_eq!(records.len(), 1);
    assert!(matches!(records[0], ReferenceRecord::Instrument(_)));
}

#[tokio::test]
async fn lifecycle_history_can_be_replayed_by_stable_sequence() {
    let mut application = ReferenceApplication::new(
        "reference-test",
        SequenceSource {
            catalogs: vec![provider_catalog(), ProviderCatalog::default()],
            index: 0,
        },
        TestStore::default(),
    )
    .await
    .unwrap();
    application.refresh().await.unwrap();
    application.refresh().await.unwrap();

    let events = application
        .replay_lifecycle_events(Some(1.into()), Some(14.into()))
        .await
        .unwrap();
    assert_eq!(events.len(), 14);
    assert!(events.iter().any(
        |event| event.event_type == "listed" && event.record_kind.as_deref() == Some("market")
    ));
    assert!(events
        .iter()
        .any(|event| event.event_type == "delisted"
            && event.record_kind.as_deref() == Some("market")));

    let delisted = application
        .lifecycle_events(&LifecycleQuery {
            event_type: Some("delisted".into()),
            sequence_from: Some(1.into()),
            ..LifecycleQuery::default()
        })
        .await
        .unwrap();
    assert_eq!(delisted.len(), 1);
    assert_eq!(delisted[0].event_type, "delisted");
    assert_eq!(delisted[0].record_kind.as_deref(), Some("market"));
}

#[tokio::test]
async fn immutable_read_model_contains_only_bounded_operational_metadata() {
    let mut application = application().await;
    application.refresh().await.unwrap();
    let read_model = application.read_model().await;
    assert_eq!(read_model.generation(), application.catalog().generation);
    assert_eq!(
        read_model.event_sequence(),
        application.catalog().event_sequence
    );
    assert_eq!(
        read_model.market_count(),
        application.catalog().markets.len()
    );
}
