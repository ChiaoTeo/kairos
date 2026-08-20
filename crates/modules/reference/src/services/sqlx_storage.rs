//! SQLx-backed Reference persistence running on the caller's Tokio runtime.

use std::future::Future;
use std::path::Path;

use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use sqlx::{Row, Sqlite, SqlitePool};

use super::publication::StoredPublication;
use super::time::unix_nanos;
use crate::domain::{
    Asset, Entity, Instrument, LifecycleEvent, Listing, Market, ProviderCatalog, ReferenceCatalog,
    ReferenceError, ReferenceResult,
};

const LIFECYCLE_LIMIT: i64 = 4096;
pub(crate) const PROVIDER_PROJECTION_VERSION: i64 = 4;

#[derive(Clone, Copy, Default)]
#[cfg_attr(test, allow(dead_code))]
pub(crate) struct CatalogState {
    pub generation: kairos_primitives::time::Generation,
    pub event_sequence: kairos_primitives::time::Sequence,
    pub market_count: usize,
}

pub struct SqlxCatalogStore {
    pool: SqlitePool,
}

pub(crate) struct SqlxProviderSyncStore {
    pool: SqlitePool,
    normalized_promotion: bool,
}

fn persistence(error: impl std::fmt::Display) -> ReferenceError {
    ReferenceError::Persistence(error.to_string())
}

fn decode<T: serde::de::DeserializeOwned>(payload: String) -> ReferenceResult<T> {
    serde_json::from_str(&payload).map_err(persistence)
}

fn provider_records(
    catalog: &ProviderCatalog,
) -> ReferenceResult<Vec<(&'static str, String, String)>> {
    let mut records = Vec::with_capacity(
        catalog.entities.len()
            + catalog.assets.len()
            + catalog.instruments.len()
            + catalog.listings.len()
            + catalog.markets.len(),
    );
    macro_rules! push_records {
        ($kind:literal, $values:expr, $id:expr) => {
            for value in $values {
                records.push((
                    $kind,
                    $id(value),
                    serde_json::to_string(value).map_err(persistence)?,
                ));
            }
        };
    }
    push_records!("entity", &catalog.entities, |value: &Entity| value
        .entity_id
        .clone());
    push_records!("asset", &catalog.assets, |value: &Asset| value
        .asset_id
        .to_string());
    push_records!("instrument", &catalog.instruments, |value: &Instrument| {
        value.instrument_id.to_string()
    });
    push_records!("listing", &catalog.listings, |value: &Listing| value
        .listing_id
        .to_string());
    push_records!("market", &catalog.markets, |value: &Market| value
        .market_id
        .to_string());
    Ok(records)
}

fn push_provider_record(
    catalog: &mut ProviderCatalog,
    kind: &str,
    payload: String,
) -> ReferenceResult<()> {
    match kind {
        "entity" => catalog.entities.push(decode(payload)?),
        "asset" => catalog.assets.push(decode(payload)?),
        "instrument" => catalog.instruments.push(decode(payload)?),
        "listing" => catalog.listings.push(decode(payload)?),
        "market" => catalog.markets.push(decode(payload)?),
        other => return Err(persistence(format!("unknown provider record kind {other}"))),
    }
    Ok(())
}

async fn open_pool(path: &Path) -> sqlx::Result<SqlitePool> {
    let options = SqliteConnectOptions::new()
        .filename(path)
        .create_if_missing(true)
        // Canonical reconciliation uses temporary relational working sets.
        // Keep them file-backed and cap each connection's page cache so a
        // million-row refresh cannot silently turn into a process-sized heap.
        .pragma("temp_store", "FILE")
        .pragma("cache_size", "-32768")
        .pragma("busy_timeout", "5000");
    let pool = SqlitePoolOptions::new()
        .max_connections(4)
        .connect_with(options)
        .await?;
    sqlx::query("PRAGMA journal_mode = WAL")
        .execute(&pool)
        .await?;
    sqlx::query("PRAGMA synchronous = NORMAL")
        .execute(&pool)
        .await?;
    sqlx::query("PRAGMA busy_timeout = 5000")
        .execute(&pool)
        .await?;
    let has_meta = sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='reference_meta'",
    )
    .fetch_one(&pool)
    .await?
        != 0;
    if has_meta {
        let version =
            sqlx::query_scalar::<_, i64>("SELECT schema_version FROM reference_meta WHERE id = 1")
                .fetch_optional(&pool)
                .await?
                .unwrap_or_default();
        if version < i64::from(kairos_reference_contract::REFERENCE_SQLITE_SCHEMA_VERSION) {
            // v4 changes canonical market identity. Old provider payloads and
            // projections cannot be renamed safely because provider markets
            // may now collapse into one venue market or no market at all.
            // Invalidate derived state and let configured providers rebuild it.
            sqlx::raw_sql(
                "DROP TABLE IF EXISTS reference_entities_current;
                 DROP TABLE IF EXISTS reference_assets_current;
                 DROP TABLE IF EXISTS reference_instruments_current;
                 DROP TABLE IF EXISTS reference_listings_current;
                 DROP TABLE IF EXISTS reference_markets_current;
                 DELETE FROM reference_provider_records;
                 DELETE FROM reference_provider_staging;
                 DELETE FROM reference_provider_pending_promotion;
                 DELETE FROM reference_lifecycle;
                 DELETE FROM reference_publication_outbox;
                 UPDATE reference_publication_state SET published_sequence=0 WHERE id=1;
                 UPDATE reference_meta SET schema_version=4,generation=0,event_sequence=0,committed_at_unix_nanos=0 WHERE id=1;",
            )
            .execute(&pool)
            .await?;
        }
    }
    sqlx::raw_sql(include_str!("../../schema.sql"))
        .execute(&pool)
        .await?;
    Ok(pool)
}

impl SqlxCatalogStore {
    pub(crate) async fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let pool = open_pool(path.as_ref()).await.map_err(persistence)?;
        Ok(Self { pool })
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use kairos_primitives::reference::{Exchange, InstrumentId, ListingId, MarketId, Symbol};

    use super::{SqlxCatalogStore, SqlxProviderSyncStore};
    use crate::domain::{
        Entity, Instrument, LifecycleEvent, Listing, Market, ProviderCatalog, ReferenceCatalog,
    };

    #[derive(Debug)]
    struct TestRefresh {
        generation: kairos_primitives::time::Generation,
        event_sequence: kairos_primitives::time::Sequence,
        market_count: usize,
        changed: bool,
        event_count: usize,
    }

    async fn reconcile_candidate(
        store: &mut SqlxCatalogStore,
        overlay: &ProviderCatalog,
        now: u64,
    ) -> crate::domain::ReferenceResult<TestRefresh> {
        let incoming = store.load_provider_candidate(overlay).await?;
        let mut catalog = store.load().await?.unwrap_or_default();
        let previous_generation = catalog.generation;
        let events = catalog.apply(incoming, now.into());
        let result = TestRefresh {
            generation: catalog.generation,
            event_sequence: catalog.event_sequence,
            market_count: catalog.markets.len(),
            changed: catalog.generation != previous_generation,
            event_count: events.len(),
        };
        let publications = crate::services::publication::encode_publications(&catalog, &events)?;
        store.save_refresh(&catalog, &events, &publications).await?;
        Ok(result)
    }

    #[tokio::test]
    async fn sqlx_catalog_round_trips_state_and_outbox() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let events = vec![LifecycleEvent {
            event_id: "reference:00000000000000000001".into(),
            event_type: "listed".into(),
            ..Default::default()
        }];
        let publications = vec![crate::services::publication::StoredPublication {
            event_id: events[0].event_id.clone(),
            sequence: 1,
            payload: vec![1, 2, 3],
        }];
        let catalog = ReferenceCatalog {
            lifecycle_events: events.clone(),
            generation: 1.into(),
            event_sequence: 1.into(),
            ..ReferenceCatalog::default()
        };
        {
            let mut store = SqlxCatalogStore::open(&path).await.unwrap();
            store
                .save_refresh(&catalog, &events, &publications)
                .await
                .unwrap();
        }

        let mut reopened = SqlxCatalogStore::open(&path).await.unwrap();
        assert_eq!(reopened.load().await.unwrap(), Some(catalog.clone()));
        assert_eq!(reopened.pending_event_count().await.unwrap(), 1);
        // Idempotent refresh persistence must not inflate the materialized
        // counter when the event ID already exists in the outbox.
        reopened
            .save_refresh(&catalog, &events, &publications)
            .await
            .unwrap();
        assert_eq!(reopened.pending_event_count().await.unwrap(), 1);
        assert_eq!(
            reopened.pending_publications(10).await.unwrap(),
            publications
        );
        reopened
            .acknowledge_publications(&["reference:unknown".into()])
            .await
            .unwrap();
        assert_eq!(reopened.pending_event_count().await.unwrap(), 1);
        reopened
            .acknowledge_publications(&["reference:00000000000000000001".into()])
            .await
            .unwrap();
        assert_eq!(reopened.pending_event_count().await.unwrap(), 0);
    }

    #[tokio::test]
    async fn initializes_current_schema_without_migration_metadata() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let store = SqlxCatalogStore::open(&path).await.unwrap();

        let migration_table_count = sqlx::query_scalar::<_, i64>(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='_sqlx_migrations'",
        )
        .fetch_one(&store.pool)
        .await
        .unwrap();

        assert_eq!(migration_table_count, 0);
    }

    #[tokio::test]
    async fn v4_open_invalidates_legacy_market_identity_and_access_tables() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let initialized = SqlxCatalogStore::open(&path).await.unwrap();
        initialized.pool.close().await;
        let url = format!("sqlite://{}?mode=rwc", path.display());
        let legacy = sqlx::SqlitePool::connect(&url).await.unwrap();
        sqlx::raw_sql(
            "DROP TABLE reference_markets_current;\
             CREATE TABLE reference_markets_current(\
               market_id TEXT PRIMARY KEY,source_id TEXT,market_key TEXT,\
               instrument_id TEXT,listing_id TEXT,exchange_id TEXT,market_type TEXT,\
               source_symbol TEXT,status TEXT,payload TEXT);\
             CREATE TABLE reference_market_data_accesses_current(id TEXT PRIMARY KEY);\
             CREATE TABLE reference_execution_accesses_current(id TEXT PRIMARY KEY);\
             INSERT INTO reference_markets_current(\
               market_id,source_id,market_key,instrument_id,listing_id,exchange_id,\
               market_type,source_symbol,status,payload\
             ) VALUES (\
               'market:kept','provider','kept','instrument:kept','','exchange:kept',\
               'spot','KEPT','active','{}'\
             );\
             UPDATE reference_meta SET schema_version=1 WHERE id=1",
        )
        .execute(&legacy)
        .await
        .unwrap();
        legacy.close().await;

        let store = SqlxCatalogStore::open(&path).await.unwrap();
        for removed in [
            "reference_market_data_accesses_current",
            "reference_execution_accesses_current",
        ] {
            let count = sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            )
            .bind(removed)
            .fetch_one(&store.pool)
            .await
            .unwrap();
            assert_eq!(count, 0, "obsolete table remains: {removed}");
        }
        let canonical_count =
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM reference_markets_current")
                .fetch_one(&store.pool)
                .await
                .unwrap();
        assert_eq!(canonical_count, 0);
    }

    #[tokio::test]
    async fn publication_outbox_preserves_each_committed_revision() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let asset = |status| crate::domain::Asset {
            asset_id: kairos_primitives::reference::AssetId::new("asset:BTC").unwrap(),
            code: kairos_primitives::reference::Symbol::new("BTC").unwrap(),
            asset_class: kairos_primitives::reference::AssetClass::Crypto,
            status,
            ..Default::default()
        };
        let mut catalog = ReferenceCatalog::default();
        let first = catalog.apply(
            ProviderCatalog {
                assets: vec![asset(kairos_primitives::reference::ReferenceStatus::Active)],
                ..Default::default()
            },
            10.into(),
        );
        let first_publication =
            crate::services::publication::encode_publications(&catalog, &first).unwrap();
        let mut store = SqlxCatalogStore::open(&path).await.unwrap();
        store
            .save_refresh(&catalog, &first, &first_publication)
            .await
            .unwrap();

        let second = catalog.apply(
            ProviderCatalog {
                assets: vec![asset(
                    kairos_primitives::reference::ReferenceStatus::Inactive,
                )],
                ..Default::default()
            },
            20.into(),
        );
        let second_publication =
            crate::services::publication::encode_publications(&catalog, &second).unwrap();
        store
            .save_refresh(&catalog, &second, &second_publication)
            .await
            .unwrap();

        let pending = store.pending_publications(10).await.unwrap();
        assert_eq!(pending.len(), 2);
        match kairos_reference_contract::decode_event(&pending[0].payload).unwrap() {
            kairos_reference_contract::ReferenceEvent::AssetUpserted(event) => {
                assert_eq!(event.asset().status().variant_name(), Some("ACTIVE"));
            },
            _ => panic!("unexpected first event kind"),
        }
        match kairos_reference_contract::decode_event(&pending[1].payload).unwrap() {
            kairos_reference_contract::ReferenceEvent::AssetUpdated(event) => {
                assert_eq!(event.asset().status().variant_name(), Some("INACTIVE"));
            },
            _ => panic!("unexpected second event kind"),
        }
    }

    #[tokio::test]
    async fn catalog_commit_is_immediately_readable_through_sqlite_contract() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let instrument_id = InstrumentId::new("instrument:btc").unwrap();
        let market_id = MarketId::new("market:binance:btc-usdt").unwrap();
        let instrument = Instrument {
            instrument_id: instrument_id.clone(),
            symbol: Symbol::new("BTC").unwrap(),
            instrument_type: kairos_primitives::reference::InstrumentKind::Spot,
            status: "active".into(),
            ..Default::default()
        };
        let market = Market {
            market_id: market_id.clone(),
            instrument_id: instrument_id.clone(),
            listing_id: Some(ListingId::new("listing:binance:btc-usdt").unwrap()),
            exchange_id: Exchange::new("binance").unwrap(),
            instrument_kind: kairos_primitives::reference::InstrumentKind::Spot,
            venue_symbol: Some(Symbol::new("BTCUSDT").unwrap()),
            status: "active".into(),
            ..Default::default()
        };
        let catalog = ReferenceCatalog {
            instruments: [(instrument_id, instrument)].into_iter().collect(),
            markets: [(market_id, market)].into_iter().collect(),
            generation: 3.into(),
            event_sequence: 5.into(),
            ..Default::default()
        };
        let mut store = SqlxCatalogStore::open(&path).await.unwrap();
        store.save_refresh(&catalog, &[], &[]).await.unwrap();
        sqlx::query("CREATE TABLE reconcile_updates(count INTEGER NOT NULL)")
            .execute(&store.pool)
            .await
            .unwrap();
        sqlx::query("CREATE TRIGGER track_market_update AFTER UPDATE ON reference_markets_current BEGIN INSERT INTO reconcile_updates(count) VALUES (1); END")
            .execute(&store.pool)
            .await
            .unwrap();
        store.save_refresh(&catalog, &[], &[]).await.unwrap();
        assert_eq!(
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM reconcile_updates")
                .fetch_one(&store.pool)
                .await
                .unwrap(),
            0,
            "an unchanged refresh must not rewrite current-state rows"
        );

        let reader = kairos_reference_contract::ReferenceSqliteReader::open(&path).unwrap();
        let stats = reader.stats().unwrap();
        assert_eq!(stats.markets, 1);
        assert_eq!(stats.active_markets, 1);
        assert_eq!(
            reader
                .records(kairos_reference_contract::ReferenceCollection::Markets, 10)
                .unwrap()
                .len(),
            1
        );
        assert!(reader.record("market:binance:btc-usdt").unwrap().is_some());
        let projection = reader
            .projection(&kairos_reference_contract::SqliteMarketQuery {
                venue_symbol: Some(kairos_primitives::reference::Symbol::new("BTCUSDT").unwrap()),
                limit: 100,
                ..Default::default()
            })
            .unwrap();
        assert_eq!(projection.watermark.generation, 3.into());
        assert_eq!(projection.watermark.event_sequence, 5.into());
        assert_eq!(projection.markets.len(), 1);
        assert_eq!(projection.instruments.len(), 1);
    }

    #[tokio::test]
    async fn sqlx_provider_state_survives_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let catalog = ProviderCatalog::default();
        {
            let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
            store
                .save_state("massive", Some("cursor-1"), Some(&catalog))
                .await
                .unwrap();
        }
        let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
        let (cursor, value) = reopened.load_state("massive").await.unwrap().unwrap();
        assert_eq!(cursor.as_deref(), Some("cursor-1"));
        assert!(value.is_none());
    }

    #[tokio::test]
    async fn provider_staging_advances_cursor_without_a_growing_accumulated_payload() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let first = ProviderCatalog {
            markets: vec![crate::domain::Market {
                market_id: kairos_primitives::reference::MarketId::new("market:first").unwrap(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let second = ProviderCatalog {
            markets: vec![crate::domain::Market {
                market_id: kairos_primitives::reference::MarketId::new("market:second").unwrap(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        store
            .append_staged_page("massive-options", Some("cursor-1"), &first)
            .await
            .unwrap();
        store
            .append_staged_page("massive-options", Some("cursor-2"), &second)
            .await
            .unwrap();

        let (cursor, accumulated) = store.load_state("massive-options").await.unwrap().unwrap();
        assert_eq!(cursor.as_deref(), Some("cursor-2"));
        assert!(accumulated.is_none());
        let pages = store.staged_pages("massive-options").await.unwrap();
        assert_eq!(pages.len(), 2);
        assert_eq!(pages[0], first);
        assert_eq!(pages[1], second);
        assert_eq!(
            sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM reference_provider_staging WHERE provider = ? AND record_kind = 'market'",
            )
            .bind("massive-options")
            .fetch_one(&store.pool)
            .await
            .unwrap(),
            2
        );

        store.clear_staged_pages("massive-options").await.unwrap();
        assert!(
            store
                .staged_pages("massive-options")
                .await
                .unwrap()
                .is_empty()
        );
        let (cursor, accumulated) = store.load_state("massive-options").await.unwrap().unwrap();
        assert!(cursor.is_none());
        assert!(accumulated.is_none());
    }

    #[tokio::test]
    async fn projection_version_change_restarts_only_unfinished_provider_scan() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let page = ProviderCatalog {
            markets: vec![crate::domain::Market {
                market_id: kairos_primitives::reference::MarketId::new(
                    "market:legacy-provider:equity:BCPC",
                )
                .unwrap(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        store
            .append_staged_page("massive-equity", Some("cursor-2"), &page)
            .await
            .unwrap();
        sqlx::query("INSERT INTO reference_provider_records(provider,record_kind,record_id,payload) VALUES ('massive-equity','entity','committed','{}')")
            .execute(&store.pool)
            .await
            .unwrap();

        assert!(store.prepare_projection("massive-equity").await.unwrap());
        assert!(
            store
                .staged_pages("massive-equity")
                .await
                .unwrap()
                .is_empty()
        );
        let (cursor, _) = store.load_state("massive-equity").await.unwrap().unwrap();
        assert!(cursor.is_none());
        assert_eq!(
            sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM reference_provider_records WHERE provider='massive-equity'",
            )
            .fetch_one(&store.pool)
            .await
            .unwrap(),
            1
        );
        assert!(!store.prepare_projection("massive-equity").await.unwrap());
    }

    #[tokio::test]
    async fn provider_last_good_is_stored_as_normalized_source_facts() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let catalog = ProviderCatalog {
            assets: vec![crate::domain::Asset {
                asset_id: kairos_primitives::reference::AssetId::new("asset:BTC").unwrap(),
                code: kairos_primitives::reference::Symbol::new("BTC").unwrap(),
                asset_class: kairos_primitives::reference::AssetClass::Crypto,
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        store.save_last_good("provider-a", &catalog).await.unwrap();
        assert!(store.load_last_good("provider-a").await.unwrap().is_none());
        let mut catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
        reconcile_candidate(&mut catalog_store, &ProviderCatalog::default(), 1)
            .await
            .unwrap();
        assert_eq!(
            store.load_last_good("provider-a").await.unwrap(),
            Some(catalog)
        );
        assert_eq!(
            sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM reference_provider_records WHERE provider = 'provider-a'",
            )
            .fetch_one(&store.pool)
            .await
            .unwrap(),
            1
        );
        let columns = sqlx::query_scalar::<_, String>(
            "SELECT name FROM pragma_table_info('reference_provider_sync') ORDER BY cid",
        )
        .fetch_all(&store.pool)
        .await
        .unwrap();
        assert_eq!(columns, ["provider", "cursor", "updated_at_unix_nanos"]);
    }

    #[tokio::test]
    async fn completed_staging_atomically_replaces_normalized_last_good() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let page = |id: &str, status: &str| ProviderCatalog {
            entities: vec![Entity {
                entity_id: id.into(),
                entity_type: "data_provider".into(),
                name: id.into(),
                status: status.into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        store
            .save_last_good("provider-a", &page("provider:old", "active"))
            .await
            .unwrap();
        let mut catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
        reconcile_candidate(&mut catalog_store, &ProviderCatalog::default(), 1)
            .await
            .unwrap();
        store
            .append_staged_page("provider-a", Some("next"), &page("provider:new", "active"))
            .await
            .unwrap();
        store
            .append_staged_page("provider-a", None, &page("provider:new", "inactive"))
            .await
            .unwrap();

        store.promote_staged("provider-a").await.unwrap();
        assert_eq!(
            store.load_last_good("provider-a").await.unwrap(),
            Some(page("provider:old", "active"))
        );
        assert!(!store.staged_pages("provider-a").await.unwrap().is_empty());
        reconcile_candidate(&mut catalog_store, &ProviderCatalog::default(), 2)
            .await
            .unwrap();
        assert_eq!(
            store.load_last_good("provider-a").await.unwrap(),
            Some(page("provider:new", "inactive"))
        );
        assert!(store.staged_pages("provider-a").await.unwrap().is_empty());
        let (cursor, accumulated) = store.load_state("provider-a").await.unwrap().unwrap();
        assert!(cursor.is_none());
        assert!(accumulated.is_none());
    }

    #[tokio::test]
    async fn normalized_provider_facts_reconcile_current_rows_and_lifecycle_atomically() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let instrument_id = InstrumentId::new("instrument:test").unwrap();
        let listing_id = ListingId::new("listing:test").unwrap();
        let market_id = MarketId::new("market:test").unwrap();
        let catalog = ProviderCatalog {
            entities: vec![Entity {
                entity_id: "exchange:test".into(),
                entity_type: "exchange".into(),
                name: "Test".into(),
                status: "active".into(),
                ..Default::default()
            }],
            instruments: vec![Instrument {
                instrument_id: instrument_id.clone(),
                symbol: Symbol::new("TEST").unwrap(),
                instrument_type: kairos_primitives::reference::InstrumentKind::Spot,
                status: "active".into(),
                ..Default::default()
            }],
            listings: vec![Listing {
                listing_id: listing_id.clone(),
                instrument_id: instrument_id.clone(),
                exchange_id: Exchange::new("exchange:test").unwrap(),
                exchange_symbol: Symbol::new("TEST").unwrap(),
                status: "active".into(),
                effective_from_unix_nanos: 1.into(),
                ..Default::default()
            }],
            markets: vec![Market {
                market_id,
                instrument_id,
                listing_id: Some(listing_id),
                exchange_id: Exchange::new("exchange:test").unwrap(),
                instrument_kind: kairos_primitives::reference::InstrumentKind::Spot,
                venue_symbol: Some(Symbol::new("TEST").unwrap()),
                status: "active".into(),
                effective_from_unix_nanos: 1.into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let mut provider_store = SqlxProviderSyncStore::open(&path).await.unwrap();
        provider_store
            .save_last_good("provider-a", &catalog)
            .await
            .unwrap();
        let mut catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
        let first = reconcile_candidate(&mut catalog_store, &ProviderCatalog::default(), 10)
            .await
            .unwrap();
        assert!(first.changed);
        assert_eq!(first.event_count, 4);
        assert_eq!(first.generation.get(), 1);
        assert_eq!(first.event_sequence.get(), 4);
        assert_eq!(first.market_count, 1);
        let publications = catalog_store.pending_publications(10).await.unwrap();
        assert_eq!(publications.len(), 4);
        assert!(publications.iter().all(|event| {
            event.event_id.starts_with("reference:")
                && kairos_reference_contract::decode_event(&event.payload).is_ok()
        }));

        let second = reconcile_candidate(&mut catalog_store, &ProviderCatalog::default(), 20)
            .await
            .unwrap();
        assert!(!second.changed);
        assert_eq!(second.event_count, 0);
        assert_eq!(second.generation.get(), 1);
        assert_eq!(second.event_sequence.get(), 4);
    }

    #[tokio::test]
    async fn canonical_conflict_rolls_back_provider_promotion_and_watermark() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let catalog = |status: &str| ProviderCatalog {
            entities: vec![Entity {
                entity_id: "provider:shared".into(),
                entity_type: "data_provider".into(),
                name: "Shared".into(),
                status: status.into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let mut provider_store = SqlxProviderSyncStore::open(&path).await.unwrap();
        let mut catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
        provider_store
            .save_last_good("provider-a", &catalog("active"))
            .await
            .unwrap();
        reconcile_candidate(&mut catalog_store, &ProviderCatalog::default(), 1)
            .await
            .unwrap();
        provider_store
            .save_last_good("provider-b", &catalog("inactive"))
            .await
            .unwrap();

        let error = reconcile_candidate(&mut catalog_store, &ProviderCatalog::default(), 2)
            .await
            .unwrap_err()
            .to_string();
        assert!(error.contains("irreconcilable canonical entity conflict"));
        let state = catalog_store.load_state().await.unwrap();
        assert_eq!(state.generation.get(), 1);
        assert_eq!(state.event_sequence.get(), 1);
        assert!(
            provider_store
                .load_last_good("provider-b")
                .await
                .unwrap()
                .is_none()
        );
        assert_eq!(
            sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM reference_provider_pending_promotion WHERE provider='provider-b'",
            )
            .fetch_one(&provider_store.pool)
            .await
            .unwrap(),
            1
        );
    }

    #[tokio::test]
    #[ignore = "million-row writer memory acceptance"]
    async fn million_record_normalized_refresh_stays_within_memory_budget() {
        const RECORDS: i64 = 1_000_000;
        const MAX_RSS_GROWTH_KIB: u64 = 256 * 1024;
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let mut provider_store = SqlxProviderSyncStore::open(&path).await.unwrap();
        sqlx::query("PRAGMA temp_store=FILE")
            .execute(&provider_store.pool)
            .await
            .unwrap();
        sqlx::query("PRAGMA cache_size=-32768")
            .execute(&provider_store.pool)
            .await
            .unwrap();
        sqlx::query(
            "WITH RECURSIVE n(value) AS (SELECT 1 UNION ALL SELECT value+1 FROM n WHERE value<?) \
             INSERT INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload) \
             SELECT 'scale',0,'asset',printf('asset:%07d',value),json_object('source_id',NULL,'asset_id',printf('asset:%07d',value),'code',printf('A%07d',value),'name',NULL,'asset_class','scale','status','active') FROM n",
        )
        .bind(RECORDS)
        .execute(&provider_store.pool)
        .await
        .unwrap();
        provider_store.promote_staged("scale").await.unwrap();
        let before = process_rss_kib();
        let started = std::time::Instant::now();
        let mut catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
        let result = reconcile_candidate(&mut catalog_store, &ProviderCatalog::default(), 1)
            .await
            .unwrap();
        let after = process_rss_kib();
        eprintln!(
            "million-row reconcile: elapsed={:?}, rss_growth_kib={}",
            started.elapsed(),
            after.saturating_sub(before)
        );
        assert_eq!(result.event_count, RECORDS as usize);
        assert!(after.saturating_sub(before) <= MAX_RSS_GROWTH_KIB);
        let reader = kairos_reference_contract::ReferenceSqliteReader::open(&path).unwrap();
        assert_eq!(reader.stats().unwrap().assets, RECORDS as u64);
        assert_eq!(
            reader
                .records(kairos_reference_contract::ReferenceCollection::Assets, 128)
                .unwrap()
                .len(),
            128
        );
    }

    fn process_rss_kib() -> u64 {
        let pid = std::process::id().to_string();
        let output = std::process::Command::new("ps")
            .args(["-o", "rss=", "-p", &pid])
            .output()
            .expect("read process RSS");
        String::from_utf8(output.stdout)
            .unwrap()
            .trim()
            .parse()
            .unwrap()
    }

    #[tokio::test]
    async fn provider_pause_control_survives_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        {
            let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
            store
                .set_source_paused("massive-options", true)
                .await
                .unwrap();
        }

        let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
        assert_eq!(
            reopened.paused_sources().await.unwrap(),
            vec!["massive-options"]
        );
        reopened
            .set_source_paused("massive-options", false)
            .await
            .unwrap();
        assert!(reopened.paused_sources().await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn option_coverage_survives_reopen_and_normalizes_by_key() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        {
            let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
            store
                .set_option_underlying("massive-options", "SPY", true)
                .await
                .unwrap();
            store
                .set_option_underlying("massive-options", "AAPL", true)
                .await
                .unwrap();
        }
        let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
        assert_eq!(
            reopened
                .option_underlyings("massive-options")
                .await
                .unwrap(),
            vec!["AAPL", "SPY"]
        );
        reopened
            .set_option_underlying("massive-options", "SPY", false)
            .await
            .unwrap();
        assert_eq!(
            reopened
                .option_underlyings("massive-options")
                .await
                .unwrap(),
            vec!["AAPL"]
        );
    }

    #[test]
    fn reference_catalog_golden_fixture_matches_rust_domain_contract() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../tests/fixtures/reference_catalog_empty.json");
        let payload = std::fs::read_to_string(path).unwrap();
        let catalog: ReferenceCatalog = serde_json::from_str(&payload).unwrap();
        assert_eq!(catalog, ReferenceCatalog::default());
    }
}

impl SqlxProviderSyncStore {
    pub(crate) async fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let pool = open_pool(path.as_ref()).await.map_err(persistence)?;
        Ok(Self {
            pool,
            normalized_promotion: true,
        })
    }

    #[cfg(test)]
    pub(crate) async fn open_legacy(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let pool = open_pool(path.as_ref()).await.map_err(persistence)?;
        Ok(Self {
            pool,
            normalized_promotion: false,
        })
    }
}

impl SqlxCatalogStore {
    async fn run<T, F, Fut>(&self, operation: F) -> ReferenceResult<T>
    where
        F: FnOnce(SqlitePool) -> Fut,
        Fut: Future<Output = sqlx::Result<T>>,
    {
        operation(self.pool.clone()).await.map_err(persistence)
    }
}

impl SqlxProviderSyncStore {
    async fn run<T, F, Fut>(&self, operation: F) -> ReferenceResult<T>
    where
        F: FnOnce(SqlitePool) -> Fut,
        Fut: Future<Output = sqlx::Result<T>>,
    {
        operation(self.pool.clone()).await.map_err(persistence)
    }
}

impl SqlxProviderSyncStore {
    pub(crate) fn supports_normalized_promotion(&self) -> bool {
        self.normalized_promotion
    }

    /// Prepare an incremental provider scan for the current canonical
    /// projection. A version change discards only unfinished normalized pages
    /// and their cursor; committed records remain authoritative until the new
    /// scan is complete and atomically promoted.
    pub(crate) async fn prepare_projection(&mut self, provider: &str) -> ReferenceResult<bool> {
        if !self.normalized_promotion {
            return Ok(false);
        }
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            let previous = sqlx::query_scalar::<_, i64>(
                "SELECT version FROM reference_provider_projection_version WHERE provider = ?",
            )
            .bind(&provider)
            .fetch_optional(&mut *tx)
            .await?;
            let reset = previous != Some(PROVIDER_PROJECTION_VERSION);
            if reset {
                sqlx::query("DELETE FROM reference_provider_staging WHERE provider = ?")
                    .bind(&provider)
                    .execute(&mut *tx)
                    .await?;
                sqlx::query(
                    "DELETE FROM reference_provider_pending_promotion WHERE provider = ?",
                )
                .bind(&provider)
                .execute(&mut *tx)
                .await?;
                sqlx::query("UPDATE reference_provider_sync SET cursor = NULL, updated_at_unix_nanos = ? WHERE provider = ?")
                    .bind(unix_nanos().get() as i64)
                    .bind(&provider)
                    .execute(&mut *tx)
                    .await?;
                sqlx::query("INSERT INTO reference_provider_projection_version(provider,version) VALUES (?,?) ON CONFLICT(provider) DO UPDATE SET version=excluded.version")
                    .bind(&provider)
                    .bind(PROVIDER_PROJECTION_VERSION)
                    .execute(&mut *tx)
                    .await?;
            }
            tx.commit().await?;
            Ok(reset)
        })
        .await
    }

    pub(crate) async fn load_state(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Option<(Option<String>, Option<ProviderCatalog>)>> {
        self.run(|pool| async move {
            let row = sqlx::query("SELECT cursor FROM reference_provider_sync WHERE provider = ?")
                .bind(provider)
                .fetch_optional(&pool)
                .await?;
            row.map(|row| {
                let cursor = row.try_get::<Option<String>, _>("cursor")?;
                Ok((cursor, None))
            })
            .transpose()
            .map_err(|error: sqlx::Error| error)
        })
        .await
    }

    #[cfg(test)]
    pub(crate) async fn save_state(
        &mut self,
        provider: &str,
        cursor: Option<&str>,
        accumulated: Option<&ProviderCatalog>,
    ) -> ReferenceResult<()> {
        let records = accumulated
            .map(provider_records)
            .transpose()?
            .unwrap_or_default();
        let provider = provider.to_owned();
        let cursor = cursor.map(ToOwned::to_owned);
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            if !records.is_empty() {
                sqlx::query("DELETE FROM reference_provider_staging WHERE provider=? AND ordinal=-1")
                    .bind(&provider).execute(&mut *tx).await?;
                for (kind,id,payload) in records {
                    sqlx::query("INSERT INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload) VALUES (?,-1,?,?,?)")
                        .bind(&provider).bind(kind).bind(id).bind(payload).execute(&mut *tx).await?;
                }
            }
            sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,?,?) ON CONFLICT(provider) DO UPDATE SET cursor=excluded.cursor,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
        .bind(&provider).bind(cursor).bind(unix_nanos().get() as i64).execute(&mut *tx).await?;
            tx.commit().await
        })
        .await
    }

    #[cfg(test)]
    pub(crate) async fn load_last_good(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        self.run(|pool| async move {
            let rows = sqlx::query(
                "SELECT record_kind, payload FROM reference_provider_records WHERE provider = ? ORDER BY record_kind, record_id",
            )
            .bind(provider)
            .fetch_all(&pool)
            .await?;
            if rows.is_empty() {
                return Ok(None);
            }
            let mut catalog = ProviderCatalog::default();
            for row in rows {
                push_provider_record(
                    &mut catalog,
                    row.try_get::<&str, _>("record_kind")?,
                    row.try_get("payload")?,
                )
                .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
            }
            Ok(Some(catalog))
        })
        .await
    }

    pub(crate) async fn has_last_good(&mut self, provider: &str) -> ReferenceResult<bool> {
        self.run(|pool| async move {
            Ok(sqlx::query_scalar::<_, i64>(
                "SELECT EXISTS(\
                    SELECT 1 FROM reference_provider_records \
                    WHERE provider = ? OR provider LIKE ? \
                    UNION ALL \
                    SELECT 1 \
                    FROM reference_provider_staging AS staging \
                    JOIN reference_provider_pending_promotion AS pending \
                      ON pending.provider = staging.provider \
                     AND pending.operation = 'promote' \
                    WHERE staging.provider = ? OR staging.provider LIKE ?\
                )",
            )
            .bind(provider)
            .bind(format!("{provider}:%"))
            .bind(provider)
            .bind(format!("{provider}:%"))
            .fetch_one(&pool)
            .await?
                != 0)
        })
        .await
    }

    pub(crate) async fn save_last_good(
        &mut self,
        provider: &str,
        catalog: &ProviderCatalog,
    ) -> ReferenceResult<()> {
        let records = provider_records(catalog)?;
        let provider = provider.to_owned();
        let normalized = self.normalized_promotion;
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            if normalized {
                sqlx::query("DELETE FROM reference_provider_staging WHERE provider = ?")
                    .bind(&provider)
                    .execute(&mut *tx)
                    .await?;
            } else {
                sqlx::query("DELETE FROM reference_provider_records WHERE provider = ?")
                    .bind(&provider)
                    .execute(&mut *tx)
                    .await?;
            }
            for (kind, id, payload) in records {
                if normalized {
                    sqlx::query("INSERT INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload) VALUES (?,0,?,?,?)")
                        .bind(&provider).bind(kind).bind(id).bind(payload).execute(&mut *tx).await?;
                } else {
                    sqlx::query("INSERT INTO reference_provider_records(provider,record_kind,record_id,payload) VALUES (?,?,?,?)")
                        .bind(&provider).bind(kind).bind(id).bind(payload).execute(&mut *tx).await?;
                }
            }
            if normalized {
                sqlx::query("INSERT INTO reference_provider_pending_promotion(provider,operation) VALUES (?,'promote') ON CONFLICT(provider) DO UPDATE SET operation='promote'")
                    .bind(&provider).execute(&mut *tx).await?;
            }
            sqlx::query("INSERT INTO reference_provider_sync(provider,updated_at_unix_nanos) VALUES (?,?) ON CONFLICT(provider) DO UPDATE SET updated_at_unix_nanos=excluded.updated_at_unix_nanos")
                .bind(&provider).bind(unix_nanos().get() as i64).execute(&mut *tx).await?;
            tx.commit().await
        })
        .await
    }

    pub(crate) async fn append_staged_page(
        &mut self,
        provider: &str,
        cursor: Option<&str>,
        catalog: &ProviderCatalog,
    ) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        let cursor = cursor.map(ToOwned::to_owned);
        let records = provider_records(catalog)?;
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            let ordinal = sqlx::query_scalar::<_, i64>(
                "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM reference_provider_staging WHERE provider = ?",
            )
            .bind(&provider)
            .fetch_one(&mut *tx)
            .await?;
            for (kind, id, payload) in records {
                sqlx::query("INSERT OR REPLACE INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload) VALUES (?,?,?,?,?)")
                    .bind(&provider).bind(ordinal).bind(kind).bind(id).bind(payload).execute(&mut *tx).await?;
            }
            sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,?,?) ON CONFLICT(provider) DO UPDATE SET cursor=excluded.cursor,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
                .bind(&provider)
                .bind(cursor)
                .bind(unix_nanos().get() as i64)
                .execute(&mut *tx)
                .await?;
            tx.commit().await
        })
        .await
    }

    pub(crate) async fn staged_pages(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Vec<ProviderCatalog>> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let rows = sqlx::query(
                "SELECT ordinal, record_kind, payload FROM reference_provider_staging WHERE provider = ? ORDER BY ordinal, record_kind, record_id",
            )
            .bind(provider)
            .fetch_all(&pool)
            .await?;
            let mut pages = Vec::new();
            let mut ordinal = None;
            for row in rows {
                let row_ordinal = row.try_get::<i64, _>("ordinal")?;
                if ordinal != Some(row_ordinal) {
                    pages.push(ProviderCatalog::default());
                    ordinal = Some(row_ordinal);
                }
                push_provider_record(
                    pages.last_mut().expect("page created for row"),
                    row.try_get::<&str, _>("record_kind")?,
                    row.try_get("payload")?,
                ).map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
            }
            Ok(pages)
        })
        .await
    }

    pub(crate) async fn clear_staged_pages(&mut self, provider: &str) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            sqlx::query("DELETE FROM reference_provider_staging WHERE provider = ?")
                .bind(&provider)
                .execute(&mut *tx)
                .await?;
            sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,NULL,?) ON CONFLICT(provider) DO UPDATE SET cursor=NULL,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
                .bind(&provider)
                .bind(unix_nanos().get() as i64)
                .execute(&mut *tx)
                .await?;
            tx.commit().await
        })
        .await
    }

    pub(crate) async fn promote_staged(&mut self, provider: &str) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            sqlx::query("INSERT INTO reference_provider_pending_promotion(provider,operation) VALUES (?,'promote') ON CONFLICT(provider) DO UPDATE SET operation='promote'")
                .bind(&provider).execute(&mut *tx).await?;
            sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,NULL,?) ON CONFLICT(provider) DO UPDATE SET cursor=NULL,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
                .bind(&provider)
                .bind(unix_nanos().get() as i64)
                .execute(&mut *tx)
                .await?;
            tx.commit().await
        })
        .await
    }

    pub(crate) async fn remove_last_good(&mut self, provider: &str) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            sqlx::query("INSERT INTO reference_provider_pending_promotion(provider,operation) VALUES (?,'delete') ON CONFLICT(provider) DO UPDATE SET operation='delete'")
                .bind(&provider).execute(&mut *tx).await?;
            tx.commit().await
        })
        .await
    }

    pub(crate) async fn paused_sources(&mut self) -> ReferenceResult<Vec<String>> {
        self.run(|pool| async move {
            sqlx::query_scalar::<_, String>(
                "SELECT provider FROM reference_provider_control WHERE paused = 1 ORDER BY provider",
            )
            .fetch_all(&pool)
            .await
        })
        .await
    }

    pub(crate) async fn set_source_paused(
        &mut self,
        provider: &str,
        paused: bool,
    ) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            sqlx::query("INSERT INTO reference_provider_control(provider, paused, updated_at_unix_nanos) VALUES (?, ?, ?) ON CONFLICT(provider) DO UPDATE SET paused = excluded.paused, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
                .bind(provider)
                .bind(i64::from(paused))
                .bind(unix_nanos().get() as i64)
                .execute(&pool)
                .await?;
            Ok(())
        })
        .await
    }

    pub(crate) async fn option_underlyings(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Vec<String>> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            sqlx::query_scalar::<_, String>(
                "SELECT underlying FROM reference_option_coverage WHERE provider = ? AND enabled = 1 ORDER BY underlying",
            )
            .bind(provider)
            .fetch_all(&pool)
            .await
        })
        .await
    }

    pub(crate) async fn set_option_underlying(
        &mut self,
        provider: &str,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        let underlying = underlying.to_owned();
        self.run(|pool| async move {
            sqlx::query("INSERT INTO reference_option_coverage(provider, underlying, enabled, updated_at_unix_nanos) VALUES (?, ?, ?, ?) ON CONFLICT(provider, underlying) DO UPDATE SET enabled = excluded.enabled, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
                .bind(provider)
                .bind(underlying)
                .bind(i64::from(enabled))
                .bind(unix_nanos().get() as i64)
                .execute(&pool)
                .await?;
            Ok(())
        })
        .await
    }
}

impl SqlxCatalogStore {
    pub(crate) async fn load_state(&mut self) -> ReferenceResult<CatalogState> {
        self.run(|pool| async move {
            let (generation, event_sequence, market_count) = sqlx::query_as::<_, (i64, i64, i64)>(
                "SELECT generation,event_sequence,(SELECT COUNT(*) FROM reference_markets_current) FROM reference_meta WHERE id=1",
            )
            .fetch_one(&pool)
            .await?;
            Ok(CatalogState {
                generation: (generation as u64).into(),
                event_sequence: (event_sequence as u64).into(),
                market_count: market_count as usize,
            })
        })
        .await
    }

    pub(crate) async fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
        self.run(|pool| async move {
            let meta =
                sqlx::query("SELECT generation,event_sequence FROM reference_meta WHERE id = 1")
                    .fetch_optional(&pool)
                    .await?;
            let Some(meta) = meta else {
                return Ok(None);
            };
            macro_rules! records {
                ($table:literal, $key:ident, $type:ty) => {{
                    let rows = sqlx::query(concat!("SELECT payload FROM ", $table, " ORDER BY 1"))
                        .fetch_all(&pool)
                        .await?;
                    rows.into_iter()
                        .map(|row| {
                            let value: $type = decode(row.try_get::<String, _>("payload")?)
                                .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                            Ok((value.$key.clone(), value))
                        })
                        .collect::<Result<_, sqlx::Error>>()?
                }};
            }
            let asset_rows =
                sqlx::query("SELECT payload FROM reference_assets_current ORDER BY asset_id")
                    .fetch_all(&pool)
                    .await?;
            let assets = asset_rows
                .into_iter()
                .map(|row| {
                    let value: Asset = decode(row.try_get::<String, _>("payload")?)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                    Ok((value.asset_id.to_string(), value))
                })
                .collect::<Result<_, sqlx::Error>>()?;
            let mut catalog = ReferenceCatalog {
                entities: records!("reference_entities_current", entity_id, Entity),
                assets,
                instruments: records!("reference_instruments_current", instrument_id, Instrument),
                listings: records!("reference_listings_current", listing_id, Listing),
                markets: records!("reference_markets_current", market_id, Market),
                generation: (meta.try_get::<i64, _>("generation")? as u64).into(),
                event_sequence: (meta.try_get::<i64, _>("event_sequence")? as u64).into(),
                lifecycle_events: Vec::new(),
            };
            let rows = sqlx::query(
                "SELECT payload FROM reference_lifecycle ORDER BY sequence DESC LIMIT ?",
            )
            .bind(LIFECYCLE_LIMIT)
            .fetch_all(&pool)
            .await?;
            catalog.lifecycle_events = rows
                .into_iter()
                .map(|row| {
                    decode(row.try_get("payload")?)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))
                })
                .collect::<Result<_, _>>()?;
            catalog.lifecycle_events.reverse();
            Ok(Some(catalog))
        })
        .await
    }

    pub(crate) async fn load_provider_candidate(
        &mut self,
        overlay: &ProviderCatalog,
    ) -> ReferenceResult<ProviderCatalog> {
        let catalogs = self
            .run(|pool| async move {
                let rows = sqlx::query(
                    "WITH effective AS ( \
                       SELECT r.provider,r.record_kind,r.record_id,r.payload \
                       FROM reference_provider_records r \
                       WHERE NOT EXISTS (SELECT 1 FROM reference_provider_pending_promotion p WHERE p.provider=r.provider) \
                       UNION ALL \
                       SELECT s.provider,s.record_kind,s.record_id,s.payload \
                       FROM reference_provider_staging s \
                       JOIN reference_provider_pending_promotion p ON p.provider=s.provider AND p.operation='promote' \
                       WHERE NOT EXISTS (SELECT 1 FROM reference_provider_staging newer \
                         WHERE newer.provider=s.provider AND newer.record_kind=s.record_kind \
                           AND newer.record_id=s.record_id AND newer.ordinal>s.ordinal) \
                     ) \
                     SELECT provider,record_kind,payload FROM effective \
                     ORDER BY provider,record_kind,record_id",
                )
                .fetch_all(&pool)
                .await?;
                let mut catalogs = std::collections::BTreeMap::<String, ProviderCatalog>::new();
                for row in rows {
                    let provider = row.try_get::<String, _>("provider")?;
                    let kind = row.try_get::<String, _>("record_kind")?;
                    let payload = row.try_get::<String, _>("payload")?;
                    push_provider_record(catalogs.entry(provider).or_default(), &kind, payload)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                }
                Ok(catalogs.into_values().collect::<Vec<_>>())
            })
            .await?;
        let mut inputs = catalogs.iter().collect::<Vec<_>>();
        inputs.push(overlay);
        ProviderCatalog::merge(inputs)
    }

    pub(crate) async fn save_refresh(
        &mut self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
        publications: &[StoredPublication],
    ) -> ReferenceResult<()> {
        let event_payloads = events
            .iter()
            .map(|event| serde_json::to_string(event).map(|payload| (event, payload)))
            .collect::<Result<Vec<_>, _>>()
            .map_err(persistence)?;
        let event_count = events.len() as u64;
        let result = self
            .run(|pool| async move {
            let mut tx = pool.begin().await?;
            commit_pending_provider_promotions(&mut tx).await?;
            replace_current_state(&mut tx, catalog).await?;
            for (offset, (event, payload)) in event_payloads.into_iter().enumerate() {
                let market_id = event.market_id.as_ref().map(ToString::to_string);
                let exchange_id = event.exchange_id.as_ref().map(ToString::to_string);
                let sequence = catalog
                    .event_sequence
                    .get()
                    .saturating_sub(events.len() as u64)
                    .saturating_add(offset as u64 + 1) as i64;
                sqlx::query("INSERT OR IGNORE INTO reference_lifecycle(sequence,event_type,record_kind,record_id,market_id,exchange_id,event_time_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?)")
                    .bind(sequence).bind(&event.event_type).bind(&event.record_kind).bind(&event.record_id).bind(market_id).bind(exchange_id).bind(event.event_time_unix_nanos.get() as i64).bind(&payload).execute(&mut *tx).await?;
            }
            for publication in publications {
                sqlx::query("INSERT OR IGNORE INTO reference_publication_outbox(sequence,event_id,payload) VALUES (?,?,?)")
                    .bind(publication.sequence as i64)
                    .bind(&publication.event_id)
                    .bind(&publication.payload)
                    .execute(&mut *tx)
                    .await?;
            }
            tx.commit().await
            })
            .await;
        if result.is_ok() {
            kairos_workspace::logging::record_counter("kairos.reference.refresh.commit", 1);
            if event_count > 0 {
                kairos_workspace::logging::record_counter(
                    "kairos.reference.lifecycle.append",
                    event_count,
                );
            }
        }
        result
    }

    pub(crate) async fn pending_publications(
        &mut self,
        limit: usize,
    ) -> ReferenceResult<Vec<StoredPublication>> {
        self.run(|pool| async move {
            let rows = sqlx::query(
                "SELECT sequence,event_id,payload FROM reference_publication_outbox ORDER BY sequence LIMIT ?",
            )
            .bind(limit as i64)
            .fetch_all(&pool)
            .await?;
            rows.into_iter()
                .map(|row| {
                    Ok(StoredPublication {
                        sequence: row.try_get::<i64, _>("sequence")? as u64,
                        event_id: row.try_get("event_id")?,
                        payload: row.try_get("payload")?,
                    })
                })
                .collect::<Result<Vec<_>, sqlx::Error>>()
        })
        .await
    }
    pub(crate) async fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        self.run(|pool| async move {
            Ok(
                sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM reference_publication_outbox")
                    .fetch_one(&pool)
                    .await? as usize,
            )
        })
        .await
    }
    pub(crate) async fn lifecycle_events(
        &mut self,
        from: Option<u64>,
        to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.lifecycle_payloads(from, to, None, None, limit).await
    }
    pub(crate) async fn lifecycle_events_filtered(
        &mut self,
        from: Option<u64>,
        to: Option<u64>,
        time_from: Option<u64>,
        time_to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.lifecycle_payloads(from, to, time_from, time_to, limit)
            .await
    }
    pub(crate) async fn acknowledge_publications(
        &mut self,
        event_ids: &[String],
    ) -> ReferenceResult<()> {
        let mut sequences = event_ids
            .iter()
            .filter_map(|id| id.rsplit(':').next()?.parse::<i64>().ok())
            .collect::<Vec<_>>();
        sequences.sort_unstable();
        sequences.dedup();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            let current = sqlx::query_scalar::<_, i64>(
                "SELECT published_sequence FROM reference_publication_state WHERE id = 1",
            )
            .fetch_one(&mut *tx)
            .await?;
            let mut next = current;
            for sequence in sequences {
                if sequence == next + 1 {
                    next = sequence;
                } else if sequence > next + 1 {
                    break;
                }
            }
            if next > current {
                sqlx::query(
                    "UPDATE reference_publication_state SET published_sequence = ? WHERE id = 1",
                )
                .bind(next)
                .execute(&mut *tx)
                .await?;
                sqlx::query("DELETE FROM reference_publication_outbox WHERE sequence <= ?")
                    .bind(next)
                    .execute(&mut *tx)
                    .await?;
            }
            tx.commit().await
        })
        .await
    }
}

async fn commit_pending_provider_promotions(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
) -> sqlx::Result<()> {
    sqlx::query(
        "DELETE FROM reference_provider_records WHERE provider IN (SELECT provider FROM reference_provider_pending_promotion)",
    )
    .execute(&mut **tx)
    .await?;
    sqlx::query(
        "INSERT INTO reference_provider_records(provider,record_kind,record_id,payload) \
         SELECT s.provider,s.record_kind,s.record_id,s.payload FROM reference_provider_staging s \
         JOIN reference_provider_pending_promotion p ON p.provider=s.provider AND p.operation='promote' \
         WHERE NOT EXISTS (SELECT 1 FROM reference_provider_staging newer \
           WHERE newer.provider=s.provider AND newer.record_kind=s.record_kind \
             AND newer.record_id=s.record_id AND newer.ordinal>s.ordinal)",
    )
    .execute(&mut **tx)
    .await?;
    sqlx::query(
        "DELETE FROM reference_provider_staging WHERE provider IN (SELECT provider FROM reference_provider_pending_promotion)",
    )
    .execute(&mut **tx)
    .await?;
    sqlx::query(
        "DELETE FROM reference_provider_sync WHERE provider IN (SELECT provider FROM reference_provider_pending_promotion WHERE operation='delete')",
    )
    .execute(&mut **tx)
    .await?;
    sqlx::query("DELETE FROM reference_provider_pending_promotion")
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn replace_current_state(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    catalog: &ReferenceCatalog,
) -> sqlx::Result<()> {
    sqlx::query("CREATE TEMP TABLE IF NOT EXISTS reference_reconcile_keys(record_kind TEXT NOT NULL, record_id TEXT NOT NULL, PRIMARY KEY(record_kind, record_id)) WITHOUT ROWID")
        .execute(&mut **tx)
        .await?;
    sqlx::query("DELETE FROM reference_reconcile_keys")
        .execute(&mut **tx)
        .await?;

    macro_rules! track {
        ($kind:literal, $key:expr) => {
            sqlx::query("INSERT INTO reference_reconcile_keys(record_kind,record_id) VALUES (?,?)")
                .bind($kind)
                .bind($key)
                .execute(&mut **tx)
                .await?;
        };
    }

    for entity in catalog.entities.values() {
        track!("entity", &entity.entity_id);
        sqlx::query("INSERT INTO reference_entities_current(entity_id,entity_type,status,payload) VALUES (?,?,?,?) ON CONFLICT(entity_id) DO UPDATE SET entity_type=excluded.entity_type,status=excluded.status,payload=excluded.payload WHERE reference_entities_current.payload<>excluded.payload")
            .bind(&entity.entity_id)
            .bind(entity.entity_type.as_str())
            .bind(entity.status.as_str())
            .bind(serde_json::to_string(entity).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
            .execute(&mut **tx)
            .await?;
    }
    for asset in catalog.assets.values() {
        track!("asset", asset.asset_id.as_str());
        sqlx::query("INSERT INTO reference_assets_current(asset_id,code,asset_class,status,payload) VALUES (?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET code=excluded.code,asset_class=excluded.asset_class,status=excluded.status,payload=excluded.payload WHERE reference_assets_current.payload<>excluded.payload")
            .bind(asset.asset_id.as_str())
            .bind(asset.code.as_str())
            .bind(asset.asset_class.as_str())
            .bind(asset.status.as_str())
            .bind(serde_json::to_string(asset).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
            .execute(&mut **tx)
            .await?;
    }
    for instrument in catalog.instruments.values() {
        track!("instrument", instrument.instrument_id.as_str());
        sqlx::query("INSERT INTO reference_instruments_current(instrument_id,symbol,instrument_type,product_family,underlying_instrument_id,expiry_unix_nanos,status,payload) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(instrument_id) DO UPDATE SET symbol=excluded.symbol,instrument_type=excluded.instrument_type,product_family=excluded.product_family,underlying_instrument_id=excluded.underlying_instrument_id,expiry_unix_nanos=excluded.expiry_unix_nanos,status=excluded.status,payload=excluded.payload WHERE reference_instruments_current.payload<>excluded.payload")
            .bind(instrument.instrument_id.as_str())
            .bind(instrument.symbol.as_str())
            .bind(instrument.instrument_type.as_str())
            .bind(Option::<String>::None)
            .bind(instrument.underlying_instrument_id.as_ref().map(|value| value.as_str()))
            .bind(instrument.expiry_unix_nanos.map(|value| value.get() as i64))
            .bind(instrument.status.as_str())
            .bind(serde_json::to_string(instrument).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
            .execute(&mut **tx)
            .await?;
    }
    for listing in catalog.listings.values() {
        track!("listing", listing.listing_id.as_str());
        sqlx::query("INSERT INTO reference_listings_current(listing_id,instrument_id,exchange_id,exchange_symbol,status,effective_to_unix_nanos,payload) VALUES (?,?,?,?,?,?,?) ON CONFLICT(listing_id) DO UPDATE SET instrument_id=excluded.instrument_id,exchange_id=excluded.exchange_id,exchange_symbol=excluded.exchange_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE reference_listings_current.payload<>excluded.payload")
            .bind(listing.listing_id.as_str())
            .bind(listing.instrument_id.as_str())
            .bind(listing.exchange_id.as_str())
            .bind(listing.exchange_symbol.as_str())
            .bind(listing.status.as_str())
            .bind(listing.effective_to_unix_nanos.map(|value| value.get() as i64))
            .bind(serde_json::to_string(listing).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
            .execute(&mut **tx)
            .await?;
    }
    for market in catalog.markets.values() {
        track!("market", market.market_id.as_str());
        sqlx::query("INSERT INTO reference_markets_current(market_id,instrument_id,listing_id,exchange_id,instrument_kind,asset_type,underlying_instrument_id,venue_symbol,status,effective_to_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(market_id) DO UPDATE SET instrument_id=excluded.instrument_id,listing_id=excluded.listing_id,exchange_id=excluded.exchange_id,instrument_kind=excluded.instrument_kind,asset_type=excluded.asset_type,underlying_instrument_id=excluded.underlying_instrument_id,venue_symbol=excluded.venue_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE reference_markets_current.payload<>excluded.payload")
            .bind(market.market_id.as_str())
            .bind(market.instrument_id.as_str())
            .bind(market.listing_id.as_ref().map(|value| value.as_str()))
            .bind(market.exchange_id.as_str())
            .bind(market.instrument_kind.as_str())
            .bind(market.asset_type.map(|value| value.as_str()))
            .bind(market.underlying_instrument_id.as_ref().map(|value| value.as_str()))
            .bind(market.venue_symbol.as_ref().map(|value| value.as_str()))
            .bind(market.status.as_str())
            .bind(market.effective_to_unix_nanos.map(|value| value.get() as i64))
            .bind(serde_json::to_string(market).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
            .execute(&mut **tx)
            .await?;
    }
    for statement in [
        "DELETE FROM reference_entities_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='entity' AND k.record_id=reference_entities_current.entity_id)",
        "DELETE FROM reference_assets_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='asset' AND k.record_id=reference_assets_current.asset_id)",
        "DELETE FROM reference_instruments_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='instrument' AND k.record_id=reference_instruments_current.instrument_id)",
        "DELETE FROM reference_listings_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='listing' AND k.record_id=reference_listings_current.listing_id)",
        "DELETE FROM reference_markets_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='market' AND k.record_id=reference_markets_current.market_id)",
    ] {
        sqlx::query(statement).execute(&mut **tx).await?;
    }
    sqlx::query(
        "UPDATE reference_meta SET schema_version = ?, generation = ?, \
         event_sequence = ?, committed_at_unix_nanos = ? WHERE id = 1",
    )
    .bind(i64::from(
        kairos_reference_contract::REFERENCE_SQLITE_SCHEMA_VERSION,
    ))
    .bind(catalog.generation.get() as i64)
    .bind(catalog.event_sequence.get() as i64)
    .bind(unix_nanos().get() as i64)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

impl SqlxCatalogStore {
    async fn lifecycle_payloads(
        &self,
        from: Option<u64>,
        to: Option<u64>,
        time_from: Option<u64>,
        time_to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.run(|pool| async move {
            let rows = sqlx::query("SELECT payload FROM reference_lifecycle WHERE sequence >= COALESCE(?, 1) AND sequence <= COALESCE(?, 9223372036854775807) AND (? IS NULL OR event_time_unix_nanos >= ?) AND (? IS NULL OR event_time_unix_nanos < ?) ORDER BY sequence LIMIT ?").bind(from.map(|v| v as i64)).bind(to.map(|v| v as i64)).bind(time_from.map(|v| v as i64)).bind(time_from.map(|v| v as i64)).bind(time_to.map(|v| v as i64)).bind(time_to.map(|v| v as i64)).bind(limit as i64).fetch_all(&pool).await?;
            rows.into_iter()
                .map(|row| {
                    decode(row.try_get("payload")?)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))
                })
                .collect()
        })
        .await
    }
}
