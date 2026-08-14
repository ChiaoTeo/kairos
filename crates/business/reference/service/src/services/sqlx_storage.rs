//! SQLx-backed Reference persistence running on the caller's Tokio runtime.

use std::future::Future;
use std::path::Path;

use sqlx::{
    sqlite::{SqliteConnectOptions, SqlitePoolOptions},
    Row, Sqlite, SqlitePool,
};

use super::store::{CatalogState, CatalogStore, NormalizedRefresh, ProviderSyncStore};
use crate::domain::{
    Asset, Entity, ExecutionAccess, FinancialProduct, Instrument, LifecycleEvent, Listing, Market,
    ProviderCatalog, ReferenceCatalog, ReferenceError, ReferenceResult,
};

const LIFECYCLE_LIMIT: i64 = 4096;

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
            + catalog.markets.len()
            + catalog.financial_products.len()
            + catalog.execution_accesses.len(),
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
    push_records!(
        "financial_product",
        &catalog.financial_products,
        |value: &FinancialProduct| value.product_id.clone()
    );
    push_records!(
        "execution_access",
        &catalog.execution_accesses,
        |value: &ExecutionAccess| value.access_id.to_string()
    );
    push_records!(
        "market_data_access",
        &catalog.market_data_accesses,
        |value: &crate::domain::MarketDataAccess| value.access_id.clone()
    );
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
        "financial_product" => catalog.financial_products.push(decode(payload)?),
        "execution_access" => catalog.execution_accesses.push(decode(payload)?),
        "market_data_access" => catalog.market_data_accesses.push(decode(payload)?),
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
    sqlx::migrate!("./migrations").run(&pool).await?;
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
    use super::{CatalogStore, ProviderSyncStore, SqlxCatalogStore, SqlxProviderSyncStore};
    use crate::domain::{
        Entity, Instrument, LifecycleEvent, Listing, Market, ProviderCatalog, ReferenceCatalog,
    };
    use kairos_domain_types::{Exchange, InstrumentId, ListingId, MarketId, Symbol};

    #[tokio::test]
    async fn sqlx_catalog_round_trips_state_and_outbox() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let events = vec![LifecycleEvent {
            event_id: "reference:00000000000000000001".into(),
            event_type: "listed".into(),
            ..Default::default()
        }];
        let catalog = ReferenceCatalog {
            lifecycle_events: events.clone(),
            generation: 1.into(),
            event_sequence: 1.into(),
            ..ReferenceCatalog::default()
        };
        {
            let mut store = SqlxCatalogStore::open(&path).await.unwrap();
            store.save_refresh(&catalog, &events).await.unwrap();
        }

        let mut reopened = SqlxCatalogStore::open(&path).await.unwrap();
        assert_eq!(reopened.load().await.unwrap(), Some(catalog.clone()));
        assert_eq!(reopened.pending_event_count().await.unwrap(), 1);
        // Idempotent refresh persistence must not inflate the materialized
        // counter when the event ID already exists in the outbox.
        reopened.save_refresh(&catalog, &events).await.unwrap();
        assert_eq!(reopened.pending_event_count().await.unwrap(), 1);
        assert_eq!(reopened.pending_events(10).await.unwrap(), events);
        reopened
            .acknowledge_pending_events(&["reference:unknown".into()])
            .await
            .unwrap();
        assert_eq!(reopened.pending_event_count().await.unwrap(), 1);
        reopened
            .acknowledge_pending_events(&["reference:00000000000000000001".into()])
            .await
            .unwrap();
        assert_eq!(reopened.pending_event_count().await.unwrap(), 0);
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
            instrument_type: "spot".into(),
            status: "active".into(),
            ..Default::default()
        };
        let market = Market {
            source_id: Some("binance-spot".into()),
            market_id: market_id.clone(),
            market_key: "btc-usdt".into(),
            instrument_id: instrument_id.clone(),
            listing_id: ListingId::new("listing:binance:btc-usdt").unwrap(),
            exchange_id: Exchange::new("binance").unwrap(),
            market_type: "spot".into(),
            source_symbol: Symbol::new("BTCUSDT").unwrap(),
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
        store.save_refresh(&catalog, &[]).await.unwrap();
        sqlx::query("CREATE TABLE reconcile_updates(count INTEGER NOT NULL)")
            .execute(&store.pool)
            .await
            .unwrap();
        sqlx::query("CREATE TRIGGER track_market_update AFTER UPDATE ON reference_markets_current BEGIN INSERT INTO reconcile_updates(count) VALUES (1); END")
            .execute(&store.pool)
            .await
            .unwrap();
        store.save_refresh(&catalog, &[]).await.unwrap();
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
                source_id: Some("binance-spot".into()),
                limit: 100,
                ..Default::default()
            })
            .unwrap();
        assert_eq!(projection.watermark.generation, 3);
        assert_eq!(projection.watermark.event_sequence, 5);
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
                market_id: kairos_domain_types::MarketId::new("market:first").unwrap(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let second = ProviderCatalog {
            markets: vec![crate::domain::Market {
                market_id: kairos_domain_types::MarketId::new("market:second").unwrap(),
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
        assert!(store
            .staged_pages("massive-options")
            .await
            .unwrap()
            .is_empty());
        let (cursor, accumulated) = store.load_state("massive-options").await.unwrap().unwrap();
        assert!(cursor.is_none());
        assert!(accumulated.is_none());
    }

    #[tokio::test]
    async fn provider_last_good_is_stored_as_normalized_source_facts() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let catalog = ProviderCatalog {
            assets: vec![crate::domain::Asset {
                asset_id: kairos_domain_types::AssetId::new("asset:BTC").unwrap(),
                code: "BTC".into(),
                asset_class: "crypto".into(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        store.save_last_good("provider-a", &catalog).await.unwrap();
        assert!(store.load_last_good("provider-a").await.unwrap().is_none());
        let mut catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
        catalog_store
            .reconcile_provider_facts(&ProviderCatalog::default(), 1.into())
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
        catalog_store
            .reconcile_provider_facts(&ProviderCatalog::default(), 1.into())
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
        catalog_store
            .reconcile_provider_facts(&ProviderCatalog::default(), 2.into())
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
                instrument_type: "spot".into(),
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
                market_key: "test.TEST".into(),
                instrument_id,
                listing_id,
                exchange_id: Exchange::new("exchange:test").unwrap(),
                market_type: "spot".into(),
                source_symbol: Symbol::new("TEST").unwrap(),
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
        let first = catalog_store
            .reconcile_provider_facts(&ProviderCatalog::default(), 10.into())
            .await
            .unwrap()
            .unwrap();
        assert!(first.changed);
        assert_eq!(first.event_count, 4);
        assert_eq!(first.generation.get(), 1);
        assert_eq!(first.event_sequence.get(), 4);
        assert_eq!(first.market_count, 1);
        let events = catalog_store.pending_events(10).await.unwrap();
        assert_eq!(events.len(), 4);
        assert!(events.iter().all(|event| {
            event.operation.as_deref() == Some("upsert")
                && event.generation.get() == 1
                && event.record_payload_json.is_some()
        }));

        let second = catalog_store
            .reconcile_provider_facts(&ProviderCatalog::default(), 20.into())
            .await
            .unwrap()
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
        catalog_store
            .reconcile_provider_facts(&ProviderCatalog::default(), 1.into())
            .await
            .unwrap();
        provider_store
            .save_last_good("provider-b", &catalog("inactive"))
            .await
            .unwrap();

        let error = catalog_store
            .reconcile_provider_facts(&ProviderCatalog::default(), 2.into())
            .await
            .unwrap_err()
            .to_string();
        assert!(error.contains("irreconcilable canonical entity conflict"));
        let state = catalog_store.load_state().await.unwrap();
        assert_eq!(state.generation.get(), 1);
        assert_eq!(state.event_sequence.get(), 1);
        assert!(provider_store
            .load_last_good("provider-b")
            .await
            .unwrap()
            .is_none());
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
        let result = catalog_store
            .reconcile_provider_facts(&ProviderCatalog::default(), 1.into())
            .await
            .unwrap()
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
            .join("../../../../tests/fixtures/reference_catalog_empty.json");
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

impl ProviderSyncStore for SqlxProviderSyncStore {
    fn supports_normalized_promotion(&self) -> bool {
        self.normalized_promotion
    }

    async fn load_state(
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

    async fn save_state(
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
                .bind(&provider).bind(cursor).bind(crate::domain::unix_nanos().get() as i64).execute(&mut *tx).await?;
            tx.commit().await
        })
        .await
    }

    async fn load_last_good(&mut self, provider: &str) -> ReferenceResult<Option<ProviderCatalog>> {
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

    async fn has_last_good(&mut self, provider: &str) -> ReferenceResult<bool> {
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

    async fn save_last_good(
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
                .bind(&provider).bind(crate::domain::unix_nanos().get() as i64).execute(&mut *tx).await?;
            tx.commit().await
        })
        .await
    }

    async fn append_staged_page(
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
                .bind(crate::domain::unix_nanos().get() as i64)
                .execute(&mut *tx)
                .await?;
            tx.commit().await
        })
        .await
    }

    async fn staged_pages(&mut self, provider: &str) -> ReferenceResult<Vec<ProviderCatalog>> {
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

    async fn clear_staged_pages(&mut self, provider: &str) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            sqlx::query("DELETE FROM reference_provider_staging WHERE provider = ?")
                .bind(&provider)
                .execute(&mut *tx)
                .await?;
            sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,NULL,?) ON CONFLICT(provider) DO UPDATE SET cursor=NULL,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
                .bind(&provider)
                .bind(crate::domain::unix_nanos().get() as i64)
                .execute(&mut *tx)
                .await?;
            tx.commit().await
        })
        .await
    }

    async fn promote_staged(&mut self, provider: &str) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            sqlx::query("INSERT INTO reference_provider_pending_promotion(provider,operation) VALUES (?,'promote') ON CONFLICT(provider) DO UPDATE SET operation='promote'")
                .bind(&provider).execute(&mut *tx).await?;
            sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,NULL,?) ON CONFLICT(provider) DO UPDATE SET cursor=NULL,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
                .bind(&provider)
                .bind(crate::domain::unix_nanos().get() as i64)
                .execute(&mut *tx)
                .await?;
            tx.commit().await
        })
        .await
    }

    async fn remove_last_good(&mut self, provider: &str) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            sqlx::query("INSERT INTO reference_provider_pending_promotion(provider,operation) VALUES (?,'delete') ON CONFLICT(provider) DO UPDATE SET operation='delete'")
                .bind(&provider).execute(&mut *tx).await?;
            tx.commit().await
        })
        .await
    }

    async fn paused_sources(&mut self) -> ReferenceResult<Vec<String>> {
        self.run(|pool| async move {
            sqlx::query_scalar::<_, String>(
                "SELECT provider FROM reference_provider_control WHERE paused = 1 ORDER BY provider",
            )
            .fetch_all(&pool)
            .await
        })
        .await
    }

    async fn set_source_paused(&mut self, provider: &str, paused: bool) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            sqlx::query("INSERT INTO reference_provider_control(provider, paused, updated_at_unix_nanos) VALUES (?, ?, ?) ON CONFLICT(provider) DO UPDATE SET paused = excluded.paused, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
                .bind(provider)
                .bind(i64::from(paused))
                .bind(crate::domain::unix_nanos().get() as i64)
                .execute(&pool)
                .await?;
            Ok(())
        })
        .await
    }

    async fn option_underlyings(&mut self, provider: &str) -> ReferenceResult<Vec<String>> {
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

    async fn set_option_underlying(
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
                .bind(crate::domain::unix_nanos().get() as i64)
                .execute(&pool)
                .await?;
            Ok(())
        })
        .await
    }
}

impl CatalogStore for SqlxCatalogStore {
    async fn load_state(&mut self) -> ReferenceResult<CatalogState> {
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

    async fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
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
                financial_products: records!(
                    "reference_financial_products_current",
                    product_id,
                    FinancialProduct
                ),
                execution_accesses: records!(
                    "reference_execution_accesses_current",
                    access_id,
                    ExecutionAccess
                ),
                market_data_accesses: records!(
                    "reference_market_data_accesses_current",
                    access_id,
                    crate::domain::MarketDataAccess
                ),
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

    async fn save_refresh(
        &mut self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
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

    async fn reconcile_provider_facts(
        &mut self,
        overlay: &ProviderCatalog,
        now: kairos_domain_types::UnixNanos,
    ) -> ReferenceResult<Option<NormalizedRefresh>> {
        let overlay = provider_records(overlay)?;
        self.run(|pool| async move {
            reconcile_normalized_provider_facts(&pool, overlay, now.get() as i64).await
        })
        .await
        .map(Some)
    }

    async fn pending_events(&mut self, limit: usize) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.payloads(
            "SELECT payload FROM reference_lifecycle WHERE sequence > (SELECT published_sequence FROM reference_publication_state WHERE id = 1) ORDER BY sequence LIMIT ?",
            limit as i64,
        )
        .await
    }
    async fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        self.run(|pool| async move {
            Ok(sqlx::query_scalar::<_, i64>("SELECT MAX(0, (SELECT event_sequence FROM reference_meta WHERE id = 1) - published_sequence) FROM reference_publication_state WHERE id = 1")
                .fetch_one(&pool)
                .await? as usize)
        })
        .await
    }
    async fn lifecycle_events(
        &mut self,
        from: Option<u64>,
        to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.lifecycle_payloads(from, to, None, None, limit).await
    }
    async fn lifecycle_events_filtered(
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
    async fn acknowledge_pending_events(&mut self, event_ids: &[String]) -> ReferenceResult<()> {
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
            }
            tx.commit().await
        })
        .await
    }
}

async fn reconcile_normalized_provider_facts(
    pool: &SqlitePool,
    overlay: Vec<(&'static str, String, String)>,
    now: i64,
) -> sqlx::Result<NormalizedRefresh> {
    let mut tx = pool.begin().await?;
    for statement in [
        "CREATE TEMP TABLE IF NOT EXISTS reference_canonical_candidate(record_kind TEXT NOT NULL,record_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(record_kind,record_id)) WITHOUT ROWID",
        "CREATE TEMP TABLE IF NOT EXISTS reference_current_records(record_kind TEXT NOT NULL,record_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(record_kind,record_id)) WITHOUT ROWID",
        "CREATE TEMP TABLE IF NOT EXISTS reference_reconcile_changes(record_kind TEXT NOT NULL,record_id TEXT NOT NULL,operation TEXT NOT NULL,previous_payload TEXT,next_payload TEXT,PRIMARY KEY(record_kind,record_id)) WITHOUT ROWID",
        "DELETE FROM reference_canonical_candidate",
        "DELETE FROM reference_current_records",
        "DELETE FROM reference_reconcile_changes",
        "INSERT INTO reference_current_records SELECT 'entity',entity_id,payload FROM reference_entities_current",
        "INSERT INTO reference_current_records SELECT 'asset',asset_id,payload FROM reference_assets_current",
        "INSERT INTO reference_current_records SELECT 'instrument',instrument_id,payload FROM reference_instruments_current",
        "INSERT INTO reference_current_records SELECT 'listing',listing_id,payload FROM reference_listings_current",
        "INSERT INTO reference_current_records SELECT 'market',market_id,payload FROM reference_markets_current",
        "INSERT INTO reference_current_records SELECT 'financial_product',product_id,payload FROM reference_financial_products_current",
        "INSERT INTO reference_current_records SELECT 'execution_access',access_id,payload FROM reference_execution_accesses_current",
        "INSERT INTO reference_current_records SELECT 'market_data_access',access_id,payload FROM reference_market_data_accesses_current",
    ] {
        sqlx::query(statement).execute(&mut *tx).await?;
    }

    // Consume completed provider scans inside the canonical commit. Until
    // this transaction succeeds, prior last-known-good facts remain visible.
    sqlx::query(
        "DELETE FROM reference_provider_records WHERE provider IN (SELECT provider FROM reference_provider_pending_promotion)",
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query(
        "INSERT INTO reference_provider_records(provider,record_kind,record_id,payload) \
         SELECT s.provider,s.record_kind,s.record_id,s.payload FROM reference_provider_staging s \
         JOIN reference_provider_pending_promotion p ON p.provider=s.provider AND p.operation='promote' \
         WHERE NOT EXISTS (SELECT 1 FROM reference_provider_staging newer \
           WHERE newer.provider=s.provider AND newer.record_kind=s.record_kind \
             AND newer.record_id=s.record_id AND newer.ordinal>s.ordinal)",
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query(
        "DELETE FROM reference_provider_staging WHERE provider IN (SELECT provider FROM reference_provider_pending_promotion)",
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query(
        "DELETE FROM reference_provider_sync WHERE provider IN (SELECT provider FROM reference_provider_pending_promotion WHERE operation='delete')",
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query("DELETE FROM reference_provider_pending_promotion")
        .execute(&mut *tx)
        .await?;

    let conflict = sqlx::query(
        "SELECT record_kind,record_id FROM reference_provider_records \
         WHERE record_kind <> 'instrument' GROUP BY record_kind,record_id \
         HAVING COUNT(DISTINCT payload) > 1 LIMIT 1",
    )
    .fetch_optional(&mut *tx)
    .await?;
    if let Some(row) = conflict {
        return Err(sqlx::Error::Protocol(format!(
            "irreconcilable canonical {} conflict for {}",
            row.try_get::<String, _>("record_kind")?,
            row.try_get::<String, _>("record_id")?
        )));
    }
    let instrument_conflict = sqlx::query(
        "SELECT record_id FROM reference_provider_records WHERE record_kind='instrument' \
         GROUP BY record_id HAVING COUNT(DISTINCT json_remove(payload,'$.source_id','$.status')) > 1 LIMIT 1",
    )
    .fetch_optional(&mut *tx)
    .await?;
    if let Some(row) = instrument_conflict {
        return Err(sqlx::Error::Protocol(format!(
            "irreconcilable canonical instrument conflict for {}",
            row.try_get::<String, _>("record_id")?
        )));
    }

    sqlx::query(
        "INSERT INTO reference_canonical_candidate(record_kind,record_id,payload) \
         SELECT record_kind,record_id,MIN(payload) FROM reference_provider_records \
         WHERE record_kind <> 'instrument' GROUP BY record_kind,record_id",
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query(
        "INSERT INTO reference_canonical_candidate(record_kind,record_id,payload) \
         SELECT 'instrument',record_id,json_set(MIN(payload),'$.source_id',NULL,'$.status', \
           CASE \
             WHEN SUM(json_extract(payload,'$.status') IN ('active','trading')) > 0 THEN 'active' \
             WHEN COUNT(DISTINCT json_extract(payload,'$.status')) = 1 THEN MIN(json_extract(payload,'$.status')) \
             WHEN SUM(json_extract(payload,'$.status') = 'unknown') > 0 AND COUNT(DISTINCT json_extract(payload,'$.status')) = 2 \
               THEN MAX(CASE WHEN json_extract(payload,'$.status') <> 'unknown' THEN json_extract(payload,'$.status') END) \
             ELSE 'inactive' END) \
         FROM reference_provider_records WHERE record_kind='instrument' GROUP BY record_id",
    )
    .execute(&mut *tx)
    .await?;
    for (kind, id, payload) in overlay {
        sqlx::query("INSERT INTO reference_canonical_candidate(record_kind,record_id,payload) VALUES (?,?,?) ON CONFLICT(record_kind,record_id) DO UPDATE SET payload=excluded.payload")
            .bind(kind).bind(id).bind(payload).execute(&mut *tx).await?;
    }

    // Markets remain resolvable after disappearance. The first missing scan
    // turns them into tombstoned delisted rows; later scans preserve them.
    sqlx::query(
        "INSERT OR IGNORE INTO reference_canonical_candidate(record_kind,record_id,payload) \
         SELECT 'market',record_id,CASE WHEN json_extract(payload,'$.status')='delisted' THEN payload \
           ELSE json_set(payload,'$.status','delisted','$.effective_to_unix_nanos',?) END \
         FROM reference_current_records WHERE record_kind='market'",
    )
    .bind(now)
    .execute(&mut *tx)
    .await?;

    let invalid = sqlx::query_scalar::<_, String>(
        "SELECT problem FROM ( \
          SELECT 'listing missing instrument: '||record_id AS problem FROM reference_canonical_candidate c WHERE record_kind='listing' AND NOT EXISTS (SELECT 1 FROM reference_canonical_candidate i WHERE i.record_kind='instrument' AND i.record_id=json_extract(c.payload,'$.instrument_id')) \
          UNION ALL SELECT 'listing missing exchange: '||record_id FROM reference_canonical_candidate c WHERE record_kind='listing' AND NOT EXISTS (SELECT 1 FROM reference_canonical_candidate e WHERE e.record_kind='entity' AND e.record_id=json_extract(c.payload,'$.exchange_id')) \
          UNION ALL SELECT 'listing invalid interval: '||record_id FROM reference_canonical_candidate c WHERE record_kind='listing' AND json_extract(c.payload,'$.effective_to_unix_nanos') IS NOT NULL AND json_extract(c.payload,'$.effective_to_unix_nanos')<=json_extract(c.payload,'$.effective_from_unix_nanos') \
          UNION ALL SELECT 'instrument missing underlying: '||record_id FROM reference_canonical_candidate c WHERE record_kind='instrument' AND json_extract(c.payload,'$.underlying_instrument_id') IS NOT NULL AND (json_extract(c.payload,'$.underlying_instrument_id')=record_id OR NOT EXISTS (SELECT 1 FROM reference_canonical_candidate i WHERE i.record_kind='instrument' AND i.record_id=json_extract(c.payload,'$.underlying_instrument_id'))) \
          UNION ALL SELECT 'option instrument incomplete: '||record_id FROM reference_canonical_candidate c WHERE record_kind='instrument' AND lower(json_extract(c.payload,'$.instrument_type')) IN ('option','options') AND (json_extract(c.payload,'$.expiry_unix_nanos') IS NULL OR json_extract(c.payload,'$.strike') IS NULL OR lower(json_extract(c.payload,'$.option_right')) NOT IN ('call','put','c','p')) \
          UNION ALL SELECT 'market missing instrument/listing/exchange: '||record_id FROM reference_canonical_candidate c WHERE record_kind='market' AND (NOT EXISTS (SELECT 1 FROM reference_canonical_candidate i WHERE i.record_kind='instrument' AND i.record_id=json_extract(c.payload,'$.instrument_id')) OR NOT EXISTS (SELECT 1 FROM reference_canonical_candidate l WHERE l.record_kind='listing' AND l.record_id=json_extract(c.payload,'$.listing_id')) OR NOT EXISTS (SELECT 1 FROM reference_canonical_candidate e WHERE e.record_kind='entity' AND e.record_id=json_extract(c.payload,'$.exchange_id'))) \
          UNION ALL SELECT 'market disagrees with listing: '||c.record_id FROM reference_canonical_candidate c JOIN reference_canonical_candidate l ON l.record_kind='listing' AND l.record_id=json_extract(c.payload,'$.listing_id') WHERE c.record_kind='market' AND json_extract(c.payload,'$.instrument_id')<>json_extract(l.payload,'$.instrument_id') \
          UNION ALL SELECT 'market missing underlying: '||record_id FROM reference_canonical_candidate c WHERE record_kind='market' AND json_extract(c.payload,'$.underlying_instrument_id') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM reference_canonical_candidate i WHERE i.record_kind='instrument' AND i.record_id=json_extract(c.payload,'$.underlying_instrument_id')) \
          UNION ALL SELECT 'market missing base asset: '||record_id FROM reference_canonical_candidate c WHERE record_kind='market' AND json_extract(c.payload,'$.base_asset_id') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM reference_canonical_candidate a WHERE a.record_kind='asset' AND a.record_id=json_extract(c.payload,'$.base_asset_id')) \
          UNION ALL SELECT 'market missing quote asset: '||record_id FROM reference_canonical_candidate c WHERE record_kind='market' AND json_extract(c.payload,'$.quote_asset_id') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM reference_canonical_candidate a WHERE a.record_kind='asset' AND a.record_id=json_extract(c.payload,'$.quote_asset_id')) \
          UNION ALL SELECT 'market invalid interval: '||record_id FROM reference_canonical_candidate c WHERE record_kind='market' AND json_extract(c.payload,'$.effective_to_unix_nanos') IS NOT NULL AND json_extract(c.payload,'$.effective_to_unix_nanos')<=json_extract(c.payload,'$.effective_from_unix_nanos') \
          UNION ALL SELECT 'financial product missing asset: '||record_id FROM reference_canonical_candidate c WHERE record_kind='financial_product' AND NOT EXISTS (SELECT 1 FROM reference_canonical_candidate a WHERE a.record_kind='asset' AND a.record_id=json_extract(c.payload,'$.asset_id')) \
          UNION ALL SELECT 'financial product missing currency asset: '||record_id FROM reference_canonical_candidate c WHERE record_kind='financial_product' AND json_extract(c.payload,'$.currency_asset_id') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM reference_canonical_candidate a WHERE a.record_kind='asset' AND a.record_id=json_extract(c.payload,'$.currency_asset_id')) \
          UNION ALL SELECT 'direct execution access missing market: '||record_id FROM reference_canonical_candidate c WHERE record_kind='execution_access' AND COALESCE(json_extract(c.payload,'$.routing_mode'),'direct')='direct' AND NOT EXISTS (SELECT 1 FROM reference_canonical_candidate m WHERE m.record_kind='market' AND m.record_id=COALESCE(json_extract(c.payload,'$.destination_market_id'),json_extract(c.payload,'$.market_id'))) \
          UNION ALL SELECT 'smart execution access missing instrument: '||record_id FROM reference_canonical_candidate c WHERE record_kind='execution_access' AND json_extract(c.payload,'$.routing_mode')='smart' AND NOT EXISTS (SELECT 1 FROM reference_canonical_candidate i WHERE i.record_kind='instrument' AND i.record_id=json_extract(c.payload,'$.instrument_id')) \
          UNION ALL SELECT 'execution access missing settlement asset: '||record_id FROM reference_canonical_candidate c WHERE record_kind='execution_access' AND json_extract(c.payload,'$.settlement_asset_id') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM reference_canonical_candidate a WHERE a.record_kind='asset' AND a.record_id=json_extract(c.payload,'$.settlement_asset_id')) \
        ) LIMIT 1",
    )
    .fetch_optional(&mut *tx)
    .await?;
    if let Some(problem) = invalid {
        return Err(sqlx::Error::Protocol(problem));
    }

    sqlx::query(
        "INSERT INTO reference_reconcile_changes(record_kind,record_id,operation,previous_payload,next_payload) \
         SELECT n.record_kind,n.record_id,'upsert',c.payload,n.payload FROM reference_canonical_candidate n \
         LEFT JOIN reference_current_records c USING(record_kind,record_id) WHERE c.payload IS NULL OR c.payload<>n.payload",
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query(
        "INSERT INTO reference_reconcile_changes(record_kind,record_id,operation,previous_payload,next_payload) \
         SELECT c.record_kind,c.record_id,'delete',c.payload,NULL FROM reference_current_records c \
         LEFT JOIN reference_canonical_candidate n USING(record_kind,record_id) WHERE n.payload IS NULL",
    )
    .execute(&mut *tx)
    .await?;

    let event_count =
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM reference_reconcile_changes")
            .fetch_one(&mut *tx)
            .await?;
    let (old_generation, old_sequence) = sqlx::query_as::<_, (i64, i64)>(
        "SELECT generation,event_sequence FROM reference_meta WHERE id=1",
    )
    .fetch_one(&mut *tx)
    .await?;
    let generation = old_generation + i64::from(event_count > 0);
    let event_sequence = old_sequence + event_count;

    if event_count > 0 {
        sqlx::query(
            "WITH ordered AS ( \
               SELECT *,ROW_NUMBER() OVER (ORDER BY CASE record_kind WHEN 'entity' THEN 1 WHEN 'asset' THEN 2 WHEN 'instrument' THEN 3 WHEN 'listing' THEN 4 WHEN 'financial_product' THEN 5 WHEN 'execution_access' THEN 6 WHEN 'market' THEN 7 ELSE 8 END,record_id) AS offset \
               FROM reference_reconcile_changes) \
             INSERT INTO reference_lifecycle(sequence,event_type,record_kind,record_id,market_id,exchange_id,event_time_unix_nanos,payload) \
             SELECT ?+offset, \
               CASE WHEN record_kind='market' THEN CASE WHEN previous_payload IS NULL THEN 'listed' WHEN json_extract(previous_payload,'$.source_symbol')<>json_extract(next_payload,'$.source_symbol') THEN 'symbol_changed' WHEN json_extract(previous_payload,'$.status')<>json_extract(next_payload,'$.status') THEN 'status_changed' ELSE 'market_changed' END \
                    ELSE record_kind||CASE WHEN operation='delete' THEN '_removed' WHEN previous_payload IS NULL THEN '_added' ELSE '_changed' END END, \
               record_kind,record_id,CASE WHEN record_kind='market' THEN record_id END, \
               CASE WHEN record_kind='market' THEN json_extract(COALESCE(next_payload,previous_payload),'$.exchange_id') END,?, \
               json_object('event_id',printf('reference:%020d',?+offset),'event_type',CASE WHEN record_kind='market' THEN CASE WHEN previous_payload IS NULL THEN 'listed' WHEN json_extract(previous_payload,'$.source_symbol')<>json_extract(next_payload,'$.source_symbol') THEN 'symbol_changed' WHEN json_extract(previous_payload,'$.status')<>json_extract(next_payload,'$.status') THEN 'status_changed' ELSE 'market_changed' END ELSE record_kind||CASE WHEN operation='delete' THEN '_removed' WHEN previous_payload IS NULL THEN '_added' ELSE '_changed' END END,'event_time_unix_nanos',?,'record_kind',record_kind,'record_id',record_id,'market_id',CASE WHEN record_kind='market' THEN record_id END,'instrument_id',CASE WHEN record_kind='market' THEN json_extract(COALESCE(next_payload,previous_payload),'$.instrument_id') END,'listing_id',CASE WHEN record_kind='market' THEN json_extract(COALESCE(next_payload,previous_payload),'$.listing_id') END,'exchange_id',CASE WHEN record_kind='market' THEN json_extract(COALESCE(next_payload,previous_payload),'$.exchange_id') END,'source_symbol',CASE WHEN record_kind='market' THEN json_extract(COALESCE(next_payload,previous_payload),'$.source_symbol') END,'previous_status',CASE WHEN record_kind='market' THEN json_extract(previous_payload,'$.status') END,'current_status',CASE WHEN record_kind='market' THEN json_extract(next_payload,'$.status') END,'previous_symbol',CASE WHEN record_kind='market' THEN json_extract(previous_payload,'$.source_symbol') END,'current_symbol',CASE WHEN record_kind='market' THEN json_extract(next_payload,'$.source_symbol') END,'operation',operation,'generation',?,'record_payload_json',next_payload) \
             FROM ordered",
        )
        .bind(old_sequence)
        .bind(now)
        .bind(old_sequence)
        .bind(now)
        .bind(generation)
        .execute(&mut *tx)
        .await?;
    }

    for statement in [
        "INSERT INTO reference_entities_current(entity_id,entity_type,status,payload) SELECT record_id,json_extract(payload,'$.entity_type'),json_extract(payload,'$.status'),payload FROM reference_canonical_candidate WHERE record_kind='entity' ON CONFLICT(entity_id) DO UPDATE SET entity_type=excluded.entity_type,status=excluded.status,payload=excluded.payload WHERE payload<>excluded.payload",
        "INSERT INTO reference_assets_current(asset_id,code,asset_class,status,payload) SELECT record_id,json_extract(payload,'$.code'),json_extract(payload,'$.asset_class'),json_extract(payload,'$.status'),payload FROM reference_canonical_candidate WHERE record_kind='asset' ON CONFLICT(asset_id) DO UPDATE SET code=excluded.code,asset_class=excluded.asset_class,status=excluded.status,payload=excluded.payload WHERE payload<>excluded.payload",
        "INSERT INTO reference_instruments_current(instrument_id,symbol,instrument_type,product_family,underlying_instrument_id,expiry_unix_nanos,status,payload) SELECT record_id,json_extract(payload,'$.symbol'),json_extract(payload,'$.instrument_type'),json_extract(payload,'$.product_family'),json_extract(payload,'$.underlying_instrument_id'),json_extract(payload,'$.expiry_unix_nanos'),json_extract(payload,'$.status'),payload FROM reference_canonical_candidate WHERE record_kind='instrument' ON CONFLICT(instrument_id) DO UPDATE SET symbol=excluded.symbol,instrument_type=excluded.instrument_type,product_family=excluded.product_family,underlying_instrument_id=excluded.underlying_instrument_id,expiry_unix_nanos=excluded.expiry_unix_nanos,status=excluded.status,payload=excluded.payload WHERE payload<>excluded.payload",
        "INSERT INTO reference_listings_current(listing_id,instrument_id,exchange_id,exchange_symbol,status,effective_to_unix_nanos,payload) SELECT record_id,json_extract(payload,'$.instrument_id'),json_extract(payload,'$.exchange_id'),json_extract(payload,'$.exchange_symbol'),json_extract(payload,'$.status'),json_extract(payload,'$.effective_to_unix_nanos'),payload FROM reference_canonical_candidate WHERE record_kind='listing' ON CONFLICT(listing_id) DO UPDATE SET instrument_id=excluded.instrument_id,exchange_id=excluded.exchange_id,exchange_symbol=excluded.exchange_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE payload<>excluded.payload",
        "INSERT INTO reference_markets_current(market_id,source_id,market_key,instrument_id,listing_id,exchange_id,market_type,asset_type,underlying_instrument_id,source_symbol,status,effective_to_unix_nanos,payload) SELECT record_id,json_extract(payload,'$.source_id'),json_extract(payload,'$.market_key'),json_extract(payload,'$.instrument_id'),json_extract(payload,'$.listing_id'),json_extract(payload,'$.exchange_id'),json_extract(payload,'$.market_type'),json_extract(payload,'$.asset_type'),json_extract(payload,'$.underlying_instrument_id'),json_extract(payload,'$.source_symbol'),json_extract(payload,'$.status'),json_extract(payload,'$.effective_to_unix_nanos'),payload FROM reference_canonical_candidate WHERE record_kind='market' ON CONFLICT(market_id) DO UPDATE SET source_id=excluded.source_id,market_key=excluded.market_key,instrument_id=excluded.instrument_id,listing_id=excluded.listing_id,exchange_id=excluded.exchange_id,market_type=excluded.market_type,asset_type=excluded.asset_type,underlying_instrument_id=excluded.underlying_instrument_id,source_symbol=excluded.source_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE payload<>excluded.payload",
        "INSERT INTO reference_financial_products_current(product_id,provider_id,provider_product_id,asset_id,product_type,status,effective_to_unix_nanos,payload) SELECT record_id,json_extract(payload,'$.provider_id'),json_extract(payload,'$.provider_product_id'),json_extract(payload,'$.asset_id'),json_extract(payload,'$.product_type'),json_extract(payload,'$.status'),json_extract(payload,'$.effective_to_unix_nanos'),payload FROM reference_canonical_candidate WHERE record_kind='financial_product' ON CONFLICT(product_id) DO UPDATE SET provider_id=excluded.provider_id,provider_product_id=excluded.provider_product_id,asset_id=excluded.asset_id,product_type=excluded.product_type,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE payload<>excluded.payload",
        "INSERT INTO reference_execution_accesses_current(access_id,market_id,provider_id,product_family,provider_symbol,status,effective_to_unix_nanos,payload) SELECT record_id,json_extract(payload,'$.market_id'),json_extract(payload,'$.provider_id'),json_extract(payload,'$.product_family'),json_extract(payload,'$.provider_symbol'),json_extract(payload,'$.status'),json_extract(payload,'$.effective_to_unix_nanos'),payload FROM reference_canonical_candidate WHERE record_kind='execution_access' ON CONFLICT(access_id) DO UPDATE SET market_id=excluded.market_id,provider_id=excluded.provider_id,product_family=excluded.product_family,provider_symbol=excluded.provider_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE payload<>excluded.payload",
        "INSERT INTO reference_market_data_accesses_current(access_id,market_id,provider_id,product_family,provider_symbol,status,effective_to_unix_nanos,payload) SELECT record_id,json_extract(payload,'$.market_id'),json_extract(payload,'$.provider_id'),json_extract(payload,'$.product_family'),json_extract(payload,'$.provider_symbol'),json_extract(payload,'$.status'),json_extract(payload,'$.effective_to_unix_nanos'),payload FROM reference_canonical_candidate WHERE record_kind='market_data_access' ON CONFLICT(access_id) DO UPDATE SET market_id=excluded.market_id,provider_id=excluded.provider_id,product_family=excluded.product_family,provider_symbol=excluded.provider_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE payload<>excluded.payload",
        "DELETE FROM reference_entities_current WHERE NOT EXISTS(SELECT 1 FROM reference_canonical_candidate WHERE record_kind='entity' AND record_id=entity_id)",
        "DELETE FROM reference_assets_current WHERE NOT EXISTS(SELECT 1 FROM reference_canonical_candidate WHERE record_kind='asset' AND record_id=asset_id)",
        "DELETE FROM reference_instruments_current WHERE NOT EXISTS(SELECT 1 FROM reference_canonical_candidate WHERE record_kind='instrument' AND record_id=instrument_id)",
        "DELETE FROM reference_listings_current WHERE NOT EXISTS(SELECT 1 FROM reference_canonical_candidate WHERE record_kind='listing' AND record_id=listing_id)",
        "DELETE FROM reference_financial_products_current WHERE NOT EXISTS(SELECT 1 FROM reference_canonical_candidate WHERE record_kind='financial_product' AND record_id=product_id)",
        "DELETE FROM reference_execution_accesses_current WHERE NOT EXISTS(SELECT 1 FROM reference_canonical_candidate WHERE record_kind='execution_access' AND record_id=access_id)",
        "DELETE FROM reference_market_data_accesses_current WHERE NOT EXISTS(SELECT 1 FROM reference_canonical_candidate WHERE record_kind='market_data_access' AND record_id=access_id)",
    ] {
        sqlx::query(statement).execute(&mut *tx).await?;
    }
    sqlx::query("UPDATE reference_meta SET schema_version=?,generation=?,event_sequence=?,committed_at_unix_nanos=? WHERE id=1")
        .bind(i64::from(kairos_reference_contract::REFERENCE_SQLITE_SCHEMA_VERSION))
        .bind(generation).bind(event_sequence).bind(now).execute(&mut *tx).await?;
    let market_count =
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM reference_markets_current")
            .fetch_one(&mut *tx)
            .await?;
    tx.commit().await?;
    Ok(NormalizedRefresh {
        generation: (generation as u64).into(),
        event_sequence: (event_sequence as u64).into(),
        market_count: market_count as usize,
        changed: event_count > 0,
        event_count: event_count as usize,
    })
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
            .bind(&entity.entity_type)
            .bind(entity.status.as_str())
            .bind(serde_json::to_string(entity).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
            .execute(&mut **tx)
            .await?;
    }
    for asset in catalog.assets.values() {
        track!("asset", asset.asset_id.as_str());
        sqlx::query("INSERT INTO reference_assets_current(asset_id,code,asset_class,status,payload) VALUES (?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET code=excluded.code,asset_class=excluded.asset_class,status=excluded.status,payload=excluded.payload WHERE reference_assets_current.payload<>excluded.payload")
            .bind(asset.asset_id.as_str())
            .bind(&asset.code)
            .bind(&asset.asset_class)
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
            .bind(&instrument.instrument_type)
            .bind(&instrument.product_family)
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
        sqlx::query("INSERT INTO reference_markets_current(market_id,source_id,market_key,instrument_id,listing_id,exchange_id,market_type,asset_type,underlying_instrument_id,source_symbol,status,effective_to_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(market_id) DO UPDATE SET source_id=excluded.source_id,market_key=excluded.market_key,instrument_id=excluded.instrument_id,listing_id=excluded.listing_id,exchange_id=excluded.exchange_id,market_type=excluded.market_type,asset_type=excluded.asset_type,underlying_instrument_id=excluded.underlying_instrument_id,source_symbol=excluded.source_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE reference_markets_current.payload<>excluded.payload")
            .bind(market.market_id.as_str())
            .bind(&market.source_id)
            .bind(&market.market_key)
            .bind(market.instrument_id.as_str())
            .bind(market.listing_id.as_str())
            .bind(market.exchange_id.as_str())
            .bind(&market.market_type)
            .bind(&market.asset_type)
            .bind(market.underlying_instrument_id.as_ref().map(|value| value.as_str()))
            .bind(market.source_symbol.as_str())
            .bind(market.status.as_str())
            .bind(market.effective_to_unix_nanos.map(|value| value.get() as i64))
            .bind(serde_json::to_string(market).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
            .execute(&mut **tx)
            .await?;
    }
    for product in catalog.financial_products.values() {
        track!("financial_product", &product.product_id);
        sqlx::query("INSERT INTO reference_financial_products_current(product_id,provider_id,provider_product_id,asset_id,product_type,status,effective_to_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET provider_id=excluded.provider_id,provider_product_id=excluded.provider_product_id,asset_id=excluded.asset_id,product_type=excluded.product_type,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE reference_financial_products_current.payload<>excluded.payload")
            .bind(&product.product_id)
            .bind(&product.provider_id)
            .bind(&product.provider_product_id)
            .bind(product.asset_id.as_str())
            .bind(&product.product_type)
            .bind(product.status.as_str())
            .bind(product.effective_to_unix_nanos.map(|value| value.get() as i64))
            .bind(serde_json::to_string(product).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
            .execute(&mut **tx)
            .await?;
    }
    for access in catalog.execution_accesses.values() {
        track!("execution_access", access.access_id.as_str());
        sqlx::query("INSERT INTO reference_execution_accesses_current(access_id,market_id,provider_id,product_family,provider_symbol,status,effective_to_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(access_id) DO UPDATE SET market_id=excluded.market_id,provider_id=excluded.provider_id,product_family=excluded.product_family,provider_symbol=excluded.provider_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE reference_execution_accesses_current.payload<>excluded.payload")
            .bind(access.access_id.as_str())
            .bind(access.market_id.as_ref().map(|value| value.as_str()))
            .bind(&access.provider_id)
            .bind(&access.product_family)
            .bind(access.provider_symbol.as_str())
            .bind(access.status.as_str())
            .bind(access.effective_to_unix_nanos.map(|value| value.get() as i64))
            .bind(serde_json::to_string(access).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
            .execute(&mut **tx)
            .await?;
    }
    for access in catalog.market_data_accesses.values() {
        track!("market_data_access", access.access_id.as_str());
        sqlx::query("INSERT INTO reference_market_data_accesses_current(access_id,market_id,provider_id,product_family,provider_symbol,status,effective_to_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(access_id) DO UPDATE SET market_id=excluded.market_id,provider_id=excluded.provider_id,product_family=excluded.product_family,provider_symbol=excluded.provider_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload WHERE reference_market_data_accesses_current.payload<>excluded.payload")
            .bind(access.access_id.as_str())
            .bind(access.market_id.as_str())
            .bind(&access.provider_id)
            .bind(&access.product_family)
            .bind(access.provider_symbol.as_str())
            .bind(access.status.as_str())
            .bind(access.effective_to_unix_nanos.map(|value| value.get() as i64))
            .bind(serde_json::to_string(access).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
            .execute(&mut **tx)
            .await?;
    }
    for statement in [
        "DELETE FROM reference_entities_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='entity' AND k.record_id=reference_entities_current.entity_id)",
        "DELETE FROM reference_assets_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='asset' AND k.record_id=reference_assets_current.asset_id)",
        "DELETE FROM reference_instruments_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='instrument' AND k.record_id=reference_instruments_current.instrument_id)",
        "DELETE FROM reference_listings_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='listing' AND k.record_id=reference_listings_current.listing_id)",
        "DELETE FROM reference_markets_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='market' AND k.record_id=reference_markets_current.market_id)",
        "DELETE FROM reference_financial_products_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='financial_product' AND k.record_id=reference_financial_products_current.product_id)",
        "DELETE FROM reference_execution_accesses_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='execution_access' AND k.record_id=reference_execution_accesses_current.access_id)",
        "DELETE FROM reference_market_data_accesses_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='market_data_access' AND k.record_id=reference_market_data_accesses_current.access_id)",
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
    .bind(crate::domain::unix_nanos().get() as i64)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

impl SqlxCatalogStore {
    async fn payloads(
        &self,
        query: &'static str,
        limit: i64,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.run(|pool| async move {
            let rows = sqlx::query(query).bind(limit).fetch_all(&pool).await?;
            rows.into_iter()
                .map(|row| {
                    decode(row.try_get("payload")?)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))
                })
                .collect()
        })
        .await
    }
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
