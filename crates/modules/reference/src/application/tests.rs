use kairos_primitives::reference::{AssetId, Exchange, InstrumentId, ListingId, MarketId, Symbol};

use crate::composition::{ReferenceCompositionConfig, build_application};
use crate::domain::{Asset, Entity, Instrument, Listing, Market, ProviderCatalog, ReferenceResult};
use crate::services::source::ReferenceSource;
use crate::services::sqlx_storage::SqlxCatalogStore;
use crate::{
    LifecycleQuery, MarketQuery, ReferenceApplication, ReferenceKind, ReferenceQuery,
    ReferenceRecord, UpsertAssetCommand, UpsertInstrumentCommand, UpsertListingCommand,
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

#[async_trait::async_trait(?Send)]
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

#[async_trait::async_trait(?Send)]
impl ReferenceSource for TestSource {
    fn source_id(&self) -> &str {
        "test"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Ok(self.catalog.clone())
    }
}

async fn test_store() -> SqlxCatalogStore {
    let root = tempfile::tempdir().unwrap().keep();
    SqlxCatalogStore::open(root.join("reference.sqlite"))
        .await
        .unwrap()
}

async fn application() -> ReferenceApplication {
    ReferenceApplication::new_test(
        "reference-test",
        TestSource {
            catalog: provider_catalog(),
        },
        test_store().await,
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
                code: symbol("BTC"),
                name: Some("Bitcoin".into()),
                asset_class: kairos_primitives::reference::AssetClass::Crypto,
                status: "active".into(),
                ..Default::default()
            },
            Asset {
                asset_id: asset_id("asset:USDT"),
                code: symbol("USDT"),
                asset_class: kairos_primitives::reference::AssetClass::Crypto,
                status: "active".into(),
                ..Default::default()
            },
        ],
        instruments: vec![Instrument {
            instrument_id: instrument_id("instrument:spot:BTC"),
            symbol: symbol("BTC"),
            name: Some("BTC spot instrument".into()),
            instrument_type: kairos_primitives::reference::InstrumentKind::Spot,
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
            instrument_id: instrument_id("instrument:spot:BTC"),
            listing_id: Some(listing_id("listing:binance:spot:BTC:USDT")),
            exchange_id: Exchange::new("exchange:binance").unwrap(),
            instrument_kind: kairos_primitives::reference::InstrumentKind::Spot,
            asset_type: Some(kairos_primitives::reference::AssetClass::Crypto),
            venue_symbol: Some(symbol("BTCUSDT")),
            base_asset_id: Some(asset_id("asset:BTC")),
            quote_asset_id: Some(asset_id("asset:USDT")),
            status: "active".into(),
            price_precision: 2,
            quantity_precision: 6,
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
    assert_eq!(result.events.len(), 6);
    assert_eq!(result.generation, 1.into());
    assert_eq!(application.catalog().markets.len(), 1);
}

#[tokio::test]
async fn application_exposes_read_only_market_queries() {
    let mut application = application().await;
    application.refresh().await.unwrap();

    let query = MarketQuery {
        exchange_id: Some(Exchange::new("exchange:binance").unwrap()),
        instrument_kind: Some(kairos_primitives::reference::InstrumentKind::Spot),
        asset_type: Some("crypto".into()),
        venue_symbol: Some(kairos_primitives::reference::Symbol::new("btcusdt").unwrap()),
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
    let mut composition = build_application(
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
    composition.activate_sources().await.unwrap();
    assert_eq!(composition.application.source_id(), "reference-default");
}

#[tokio::test]
async fn application_does_not_emit_duplicate_events_for_same_catalog() {
    let mut application = application().await;
    assert_eq!(application.refresh().await.unwrap().events.len(), 6);
    let second = application.refresh().await.unwrap();
    assert!(second.events.is_empty());
    assert_eq!(second.event_sequence, 6.into());
}

#[tokio::test]
async fn administrative_asset_upsert_is_versioned_and_emits_a_reference_event() {
    let mut application = application().await;
    application.refresh().await.unwrap();
    let generation = application
        .upsert_asset(UpsertAssetCommand {
            asset_id: asset_id("asset:sol"),
            code: symbol("SOL"),
            asset_class: kairos_primitives::reference::AssetClass::Crypto,
            status: "active".into(),
            name: None,
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
    assert_eq!(application.catalog().event_sequence, 7.into());
}

#[tokio::test]
async fn administrative_instrument_and_listing_upserts_share_commit_path() {
    let mut application = application().await;
    application.refresh().await.unwrap();
    let generation = application
        .upsert_instrument(UpsertInstrumentCommand {
            instrument_id: instrument_id("instrument:spot:ETH"),
            symbol: symbol("ETH/USDT"),
            instrument_type: kairos_primitives::reference::InstrumentKind::Spot,
            status: "active".into(),
            name: None,
            issuer_id: None,
            share_class: None,
            primary_currency_asset_id: None,
            underlying_instrument_id: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
        })
        .await
        .unwrap();
    assert_eq!(generation, 2.into());
    let generation = application
        .upsert_listing(UpsertListingCommand {
            listing_id: listing_id("listing:binance:spot:ETH:USDT"),
            instrument_id: instrument_id("instrument:spot:ETH"),
            exchange_id: Exchange::new("exchange:binance").unwrap(),
            exchange_symbol: Symbol::new("ETHUSDT").unwrap(),
            status: "active".into(),
            effective_from_unix_nanos: 1.into(),
            effective_to_unix_nanos: None,
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
    assert_eq!(application.catalog().lifecycle_events.len(), 8);
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
    assert!(
        all.iter()
            .any(|record| matches!(record, ReferenceRecord::Entity(_)))
    );
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
        instrument_type: kairos_primitives::reference::InstrumentKind::Equity,
        status: "active".into(),
        ..Default::default()
    });
    catalog.instruments[0].underlying_instrument_id = Some(instrument_id("instrument:equity:SPY"));
    let mut application = ReferenceApplication::new_test(
        "reference-test",
        TestSource { catalog },
        test_store().await,
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
    let mut application = ReferenceApplication::new_test(
        "reference-test",
        SequenceSource {
            catalogs: vec![provider_catalog(), ProviderCatalog::default()],
            index: 0,
        },
        test_store().await,
    )
    .await
    .unwrap();
    application.refresh().await.unwrap();
    application.refresh().await.unwrap();

    let events = application
        .replay_lifecycle_events(Some(1.into()), Some(12.into()))
        .await
        .unwrap();
    assert_eq!(events.len(), 12);
    assert!(events.iter().any(
        |event| event.event_type == "listed" && event.record_kind.as_deref() == Some("market")
    ));
    assert!(
        events.iter().any(|event| event.event_type == "delisted"
            && event.record_kind.as_deref() == Some("market"))
    );

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
