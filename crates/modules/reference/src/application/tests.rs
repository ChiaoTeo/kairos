use kairos_primitives::reference::{AssetId, Exchange, InstrumentId, ListingId, MarketId, Symbol};
use kairos_reference_contract::{ReferenceUpsertConflictPolicy, ReferenceUpsertProvenance};

use crate::composition::{ReferenceCompositionConfig, build_application};
use crate::domain::{Asset, Entity, Instrument, Listing, Market, ProviderCatalog, ReferenceResult};
use crate::services::sources::ReferenceSource;
use crate::services::storage::catalog_store::SqlxCatalogStore;
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

struct FailingSource;

struct SyncInProgressSource;

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

#[async_trait::async_trait(?Send)]
impl ReferenceSource for FailingSource {
    fn source_id(&self) -> &str {
        "failing-test"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Err(crate::domain::ReferenceError::Provider(
            "application test provider failed".into(),
        ))
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for SyncInProgressSource {
    fn source_id(&self) -> &str {
        "syncing-test"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Err(crate::domain::ReferenceError::SyncInProgress {
            providers: vec!["syncing-test".into()],
        })
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
async fn application_runtime_status_exposes_tick_timing() {
    let mut application = application().await;
    application.refresh().await.unwrap();

    let status = application.contract_runtime_status().await;
    assert!(status.catalog.committed_at_unix_nanos.get() > 0);
    assert_eq!(status.catalog.entity_count, 1);
    assert_eq!(status.catalog.asset_count, 2);
    assert_eq!(status.catalog.instrument_count, 1);
    assert_eq!(status.catalog.listing_count, 1);
    assert_eq!(status.catalog.market_count, 1);
    assert_eq!(status.catalog.active_market_count, 1);
    assert_eq!(status.catalog.lifecycle_event_count, 6);
    assert!(!status.catalog.integrity.degraded);
    assert_eq!(status.catalog.integrity.missing_equity_market_count, 0);
    assert_eq!(status.catalog.integrity.legacy_exchange_market_id_count, 0);
    assert_eq!(status.catalog.integrity.legacy_exchange_listing_id_count, 0);
    assert!(status.app_runtime.last_tick_started_unix_nanos.is_some());
    assert!(status.app_runtime.last_tick_finished_unix_nanos.is_some());
    assert!(status.app_runtime.last_tick_duration_millis.is_some());
    assert!(status.app_runtime.next_tick_due_unix_nanos.is_some());
    assert_eq!(status.app_runtime.last_error, None);
    assert!(
        status.app_runtime.last_tick_finished_unix_nanos
            >= status.app_runtime.last_tick_started_unix_nanos
    );
    assert!(
        status.app_runtime.next_tick_due_unix_nanos
            >= status.app_runtime.last_tick_finished_unix_nanos
    );
}

#[tokio::test]
async fn application_runtime_status_exposes_last_tick_error() {
    let mut application =
        ReferenceApplication::new_test("reference-test", FailingSource, test_store().await)
            .await
            .unwrap();

    let error = application.refresh().await.unwrap_err();
    assert!(
        error
            .to_string()
            .contains("application test provider failed")
    );
    let status = application.contract_runtime_status().await;
    let last_error = status
        .app_runtime
        .last_error
        .expect("failed tick is visible in app runtime");
    assert_eq!(last_error.code, "reference.provider_failed");
    assert!(last_error.retryable);
    assert!(
        last_error
            .message
            .contains("application test provider failed")
    );
}

#[tokio::test]
async fn application_sync_in_progress_does_not_record_last_tick_error() {
    let mut application =
        ReferenceApplication::new_test("reference-test", SyncInProgressSource, test_store().await)
            .await
            .unwrap();

    let error = application.refresh().await.unwrap_err();
    assert!(error.is_sync_in_progress());

    let status = application.contract_runtime_status().await;
    assert_eq!(status.app_runtime.last_error, None);
    assert_ne!(
        status.app_runtime.phase,
        kairos_reference_contract::ReferenceAppPhase::Degraded
    );
}

#[tokio::test]
async fn application_runtime_status_exposes_last_publication_error() {
    let mut application = application().await;
    application.record_publication_error_summary(
        "reference.publication_failed",
        true,
        "reference publication failed: missing publisher",
    );

    let status = application.contract_runtime_status().await;
    let last_error = status
        .publication
        .last_error
        .expect("failed publication is visible in publication runtime");
    assert_eq!(last_error.code, "reference.publication_failed");
    assert!(last_error.retryable);
    assert!(last_error.message.contains("missing publisher"));

    application.record_publication_ready();
    let status = application.contract_runtime_status().await;
    assert_eq!(status.publication.last_error, None);
}

#[tokio::test]
async fn acknowledge_publications_clears_publication_error_status() {
    let mut application = application().await;
    application.record_publication_error_summary(
        "reference.publication_failed",
        true,
        "reference publication failed: transient ack failure",
    );

    application.acknowledge_publications(&[]).await.unwrap();

    let status = application.contract_runtime_status().await;
    assert_eq!(status.publication.last_error, None);
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
            aeron_channel: kairos_conflux::DEFAULT_AERON_CHANNEL.into(),
            reference_changes_stream: kairos_conflux::output_stream_ids::REFERENCE_CHANGES,
        },
        false,
    )
    .await
    .unwrap();
    composition.activate_sources().await.unwrap();
    assert_eq!(composition.application.source_id(), "reference-default");
}

#[tokio::test]
async fn composition_applies_runtime_tick_budget_from_reference_config() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path();
    std::fs::write(
        root.join("kairos.toml"),
        r#"
        version = 1
        workspace_id = "reference-test"

        [reference.runtime.tick_budget]
        max_sources_per_tick = 2
        max_batches_per_source = 7
        max_records_per_batch = 1000
        max_wall_clock_millis = 3000
        max_publications_per_tick = 25
        "#,
    )
    .unwrap();

    let composition = build_application(
        &ReferenceCompositionConfig {
            workspace: Some(root.to_path_buf()),
            database: root.join("reference.sqlite"),
            aeron_dir: None,
            aeron_channel: kairos_conflux::DEFAULT_AERON_CHANNEL.into(),
            reference_changes_stream: kairos_conflux::output_stream_ids::REFERENCE_CHANGES,
        },
        false,
    )
    .await
    .unwrap();

    let budget = composition.application.tick_budget();
    assert_eq!(budget.max_sources_per_tick, 2);
    assert_eq!(budget.max_batches_per_source, 7);
    assert_eq!(budget.max_records_per_batch, Some(1000));
    assert_eq!(budget.max_wall_clock_millis, Some(3000));
    assert_eq!(budget.max_publications_per_tick, Some(25));
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
            provenance: ReferenceUpsertProvenance::Manual,
            conflict_policy: ReferenceUpsertConflictPolicy::RejectProviderOwned,
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
    assert_eq!(event.provenance.as_deref(), Some("manual"));
    assert_eq!(
        event.conflict_policy.as_deref(),
        Some("reject_provider_owned")
    );
    assert_eq!(application.catalog().event_sequence, 7.into());
}

#[tokio::test]
async fn administrative_asset_upsert_rejects_provider_owned_records_by_default() {
    let mut catalog = provider_catalog();
    catalog.assets[0].source_id = Some("binance-spot".to_owned());
    let mut application = ReferenceApplication::new_test(
        "reference-test",
        TestSource { catalog },
        test_store().await,
    )
    .await
    .unwrap();
    application.refresh().await.unwrap();

    let error = application
        .upsert_asset(UpsertAssetCommand {
            asset_id: asset_id("asset:BTC"),
            code: symbol("BTC"),
            asset_class: kairos_primitives::reference::AssetClass::Crypto,
            status: "active".into(),
            name: Some("Manual Bitcoin".into()),
            provenance: ReferenceUpsertProvenance::Manual,
            conflict_policy: ReferenceUpsertConflictPolicy::RejectProviderOwned,
        })
        .await
        .unwrap_err()
        .to_string();

    assert!(error.contains("provider-owned asset asset:BTC"));
    assert!(error.contains("binance-spot"));
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
            provenance: ReferenceUpsertProvenance::Manual,
            conflict_policy: ReferenceUpsertConflictPolicy::RejectProviderOwned,
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
            provenance: ReferenceUpsertProvenance::Manual,
            conflict_policy: ReferenceUpsertConflictPolicy::RejectProviderOwned,
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
async fn administrative_listing_upsert_rejects_provider_owned_records_by_default() {
    let mut catalog = provider_catalog();
    catalog.listings[0].source_id = Some("binance-spot".to_owned());
    let mut application = ReferenceApplication::new_test(
        "reference-test",
        TestSource { catalog },
        test_store().await,
    )
    .await
    .unwrap();
    application.refresh().await.unwrap();

    let error = application
        .upsert_listing(UpsertListingCommand {
            listing_id: listing_id("listing:binance:spot:BTC:USDT"),
            instrument_id: instrument_id("instrument:spot:BTC-USDT"),
            exchange_id: Exchange::new("exchange:binance").unwrap(),
            exchange_symbol: Symbol::new("BTC-USDT").unwrap(),
            status: "active".into(),
            effective_from_unix_nanos: 1.into(),
            effective_to_unix_nanos: None,
            provenance: ReferenceUpsertProvenance::Manual,
            conflict_policy: ReferenceUpsertConflictPolicy::RejectProviderOwned,
        })
        .await
        .unwrap_err()
        .to_string();

    assert!(error.contains("provider-owned listing listing:binance:spot:BTC:USDT"));
    assert!(error.contains("binance-spot"));
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
