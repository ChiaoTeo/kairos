//! SQLx-backed Reference persistence running on the caller's Tokio runtime.

pub use super::storage::catalog_store::SqlxCatalogStore;

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use kairos_primitives::reference::{ExchangeId, InstrumentId, ListingId, MarketId, Symbol};

    use super::SqlxCatalogStore;
    use crate::domain::{
        Exchange, Instrument, LifecycleEvent, Listing, Market, ProviderCatalog, ReferenceCatalog,
        ReferenceSourceDefinition, SourceDesiredState, SourceScope, SourceSyncPolicy,
    };
    use crate::services::storage::provider_sync_store::SqlxProviderSyncStore;
    use crate::services::storage::publication_outbox_store::SqlxPublicationOutbox;

    #[derive(Debug)]
    struct TestRefresh {
        generation: kairos_primitives::time::Generation,
        event_sequence: kairos_primitives::time::Sequence,
        market_count: usize,
        changed: bool,
        event_count: usize,
    }

    async fn reconcile_candidate(
        catalog_store: &mut SqlxCatalogStore,
        provider_store: &mut SqlxProviderSyncStore,
        overlay: &ProviderCatalog,
        now: u64,
    ) -> crate::domain::ReferenceResult<TestRefresh> {
        let incoming = provider_store.load_provider_candidate(overlay).await?;
        let mut catalog = catalog_store.load().await?.unwrap_or_default();
        let previous_generation = catalog.generation;
        let events = catalog.apply(incoming, now.into());
        let result = TestRefresh {
            generation: catalog.generation,
            event_sequence: catalog.event_sequence,
            market_count: catalog.markets.len(),
            changed: catalog.generation != previous_generation,
            event_count: events.len(),
        };
        let publications = crate::services::publication::encode_publications(&catalog, &events, 1)?;
        catalog_store
            .save_refresh(&catalog, &events, &publications)
            .await?;
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
        let publications = vec![crate::services::publication::EncodedPublication {
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
        let mut outbox = SqlxPublicationOutbox::open(&path).await.unwrap();
        assert_eq!(reopened.load().await.unwrap(), Some(catalog.clone()));
        assert_eq!(outbox.pending_event_count().await.unwrap(), 1);
        // Idempotent refresh persistence must not inflate the materialized
        // counter when the event ID already exists in the outbox.
        reopened
            .save_refresh(&catalog, &events, &publications)
            .await
            .unwrap();
        assert_eq!(outbox.pending_event_count().await.unwrap(), 1);
        assert_eq!(outbox.pending_publications(10).await.unwrap(), publications);
        outbox
            .acknowledge_publications(&["reference:unknown".into()])
            .await
            .unwrap();
        assert_eq!(outbox.pending_event_count().await.unwrap(), 1);
        outbox
            .acknowledge_publications(&["reference:00000000000000000001".into()])
            .await
            .unwrap();
        assert_eq!(outbox.pending_event_count().await.unwrap(), 0);
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
    async fn open_rejects_unsupported_legacy_schema_version() {
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

        let error = match SqlxCatalogStore::open(&path).await {
            Ok(_) => panic!("legacy schema unexpectedly opened"),
            Err(error) => error.to_string(),
        };
        assert!(
            error.contains("unsupported Reference SQLite schema version 1; expected 6"),
            "unexpected error: {error}"
        );
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
            crate::services::publication::encode_publications(&catalog, &first, 1).unwrap();
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
            crate::services::publication::encode_publications(&catalog, &second, 1).unwrap();
        store
            .save_refresh(&catalog, &second, &second_publication)
            .await
            .unwrap();

        let mut outbox = SqlxPublicationOutbox::open(&path).await.unwrap();
        let pending = outbox.pending_publications(10).await.unwrap();
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
    async fn publication_outbox_can_ack_without_catalog_store_facade() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let events = vec![
            LifecycleEvent {
                event_id: "reference:00000000000000000001".into(),
                event_type: "listed".into(),
                ..Default::default()
            },
            LifecycleEvent {
                event_id: "reference:00000000000000000002".into(),
                event_type: "updated".into(),
                ..Default::default()
            },
        ];
        let publications = events
            .iter()
            .enumerate()
            .map(
                |(index, event)| crate::services::publication::EncodedPublication {
                    event_id: event.event_id.clone(),
                    sequence: index as u64 + 1,
                    payload: vec![index as u8],
                },
            )
            .collect::<Vec<_>>();
        let catalog = ReferenceCatalog {
            lifecycle_events: events.clone(),
            generation: 1.into(),
            event_sequence: 2.into(),
            ..ReferenceCatalog::default()
        };
        let mut catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
        catalog_store
            .save_refresh(&catalog, &events, &publications)
            .await
            .unwrap();

        let mut outbox = SqlxPublicationOutbox::open(&path).await.unwrap();
        assert_eq!(outbox.pending_event_count().await.unwrap(), 2);
        assert_eq!(outbox.pending_publications(10).await.unwrap(), publications);
        outbox
            .acknowledge_publications(&["reference:00000000000000000001".into()])
            .await
            .unwrap();
        assert_eq!(outbox.pending_event_count().await.unwrap(), 1);
        assert_eq!(
            outbox.pending_publications(10).await.unwrap()[0].event_id,
            "reference:00000000000000000002"
        );
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
            exchange_id: ExchangeId::new("binance").unwrap(),
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
        let first_outcome = store.save_refresh(&catalog, &[], &[]).await.unwrap();
        assert_eq!(first_outcome.write_mode.as_str(), "full_replace");
        sqlx::query("CREATE TABLE reconcile_updates(count INTEGER NOT NULL)")
            .execute(&store.pool)
            .await
            .unwrap();
        sqlx::query("CREATE TRIGGER track_market_update AFTER UPDATE ON reference_markets_current BEGIN INSERT INTO reconcile_updates(count) VALUES (1); END")
            .execute(&store.pool)
            .await
            .unwrap();
        let second_outcome = store.save_refresh(&catalog, &[], &[]).await.unwrap();
        assert_eq!(second_outcome.write_mode.as_str(), "affected_update");
        assert_eq!(
            sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM reconcile_updates")
                .fetch_one(&store.pool)
                .await
                .unwrap(),
            0,
            "an unchanged refresh must not rewrite current-state rows"
        );

        let reader = kairos_reference_contract::ReferenceCatalog::open(&path).unwrap();
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
        let catalog_page = reader
            .market_catalog(&kairos_reference_contract::MarketCatalogQuery {
                venue_symbol: Some(kairos_primitives::reference::Symbol::new("BTCUSDT").unwrap()),
                limit: 100,
                ..Default::default()
            })
            .unwrap();
        assert_eq!(catalog_page.watermark.generation, 3.into());
        assert_eq!(catalog_page.watermark.event_sequence, 5.into());
        assert_eq!(catalog_page.markets.len(), 1);
        assert_eq!(catalog_page.instruments.len(), 1);
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
    async fn startup_audit_resets_provider_scan_when_equity_listing_has_no_market() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let provider_catalog = ProviderCatalog {
            listings: vec![Listing {
                listing_id: ListingId::new("listing:nasdaq:equity:AAPL").unwrap(),
                instrument_id: InstrumentId::new("instrument:equity:US:AAPL:common").unwrap(),
                exchange_id: ExchangeId::new("exchange:nasdaq").unwrap(),
                exchange_symbol: Symbol::new("AAPL").unwrap(),
                status: "active".into(),
                effective_from_unix_nanos: 0.into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let staged_catalog = ProviderCatalog {
            markets: vec![Market {
                market_id: MarketId::new("market:staged").unwrap(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        SqlxProviderSyncStore::open_legacy(&path)
            .await
            .unwrap()
            .save_last_good("massive-equity", &provider_catalog)
            .await
            .unwrap();
        let mut provider_store = SqlxProviderSyncStore::open(&path).await.unwrap();
        provider_store
            .append_staged_page("massive-equity", Some("cursor-1"), &staged_catalog)
            .await
            .unwrap();
        provider_store
            .promote_staged("massive-equity")
            .await
            .unwrap();

        let mut catalog_store = SqlxCatalogStore::open(&path).await.unwrap();
        let report = catalog_store
            .audit_and_prepare_startup_repair()
            .await
            .unwrap();

        assert_eq!(report.reset_providers, vec!["massive-equity"]);
        assert_eq!(report.missing_provider_equity_markets.len(), 1);
        assert_eq!(
            report.missing_provider_equity_markets[0].expected_market_id,
            "market:nasdaq:equity:AAPL:USD"
        );
        assert_eq!(
            sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM reference_provider_staging WHERE provider='massive-equity'",
            )
            .fetch_one(&catalog_store.pool)
            .await
            .unwrap(),
            0
        );
        assert_eq!(
            sqlx::query_scalar::<_, i64>(
                "SELECT COUNT(*) FROM reference_provider_pending_promotion WHERE provider='massive-equity'",
            )
            .fetch_one(&catalog_store.pool)
            .await
            .unwrap(),
            0
        );
        assert_eq!(
            sqlx::query_scalar::<_, Option<String>>(
                "SELECT cursor FROM reference_provider_sync WHERE provider='massive-equity'",
            )
            .fetch_one(&catalog_store.pool)
            .await
            .unwrap()
            .as_deref(),
            None
        );
    }

    #[tokio::test]
    async fn scan_format_version_change_restarts_only_unfinished_provider_scan() {
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
        sqlx::query("INSERT INTO reference_provider_records(provider,record_kind,record_id,payload) VALUES ('massive-equity','exchange','committed','{}')")
            .execute(&store.pool)
            .await
            .unwrap();

        assert!(store.prepare_scan("massive-equity").await.unwrap());
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
        assert!(!store.prepare_scan("massive-equity").await.unwrap());
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
        reconcile_candidate(
            &mut catalog_store,
            &mut store,
            &ProviderCatalog::default(),
            1,
        )
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
            exchanges: vec![Exchange {
                exchange_id: ExchangeId::new(id).unwrap(),
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
        reconcile_candidate(
            &mut catalog_store,
            &mut store,
            &ProviderCatalog::default(),
            1,
        )
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
        reconcile_candidate(
            &mut catalog_store,
            &mut store,
            &ProviderCatalog::default(),
            2,
        )
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
    async fn promote_staged_reports_actual_provider_record_change_count() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let exchange = |id: &str, status: &str| Exchange {
            exchange_id: ExchangeId::new(id).unwrap(),
            name: id.into(),
            status: status.into(),
            ..Default::default()
        };
        let committed = ProviderCatalog {
            exchanges: vec![
                exchange("provider:kept", "active"),
                exchange("provider:removed", "active"),
            ],
            ..Default::default()
        };
        let staged_first = ProviderCatalog {
            exchanges: vec![exchange("provider:kept", "active")],
            ..Default::default()
        };
        let staged_latest = ProviderCatalog {
            exchanges: vec![
                exchange("provider:kept", "inactive"),
                exchange("exchange:added", "active"),
            ],
            ..Default::default()
        };
        let mut legacy_store = SqlxProviderSyncStore::open_legacy(&path).await.unwrap();
        legacy_store
            .save_last_good("provider-a", &committed)
            .await
            .unwrap();
        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        store
            .append_staged_page("provider-a", Some("cursor-2"), &staged_first)
            .await
            .unwrap();
        store
            .append_staged_page("provider-a", None, &staged_latest)
            .await
            .unwrap();

        let changed_count = store.promote_staged("provider-a").await.unwrap();

        assert_eq!(changed_count, 3);
    }

    #[tokio::test]
    async fn normalized_provider_facts_reconcile_current_rows_and_lifecycle_atomically() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let instrument_id = InstrumentId::new("instrument:test").unwrap();
        let listing_id = ListingId::new("listing:test").unwrap();
        let market_id = MarketId::new("market:test").unwrap();
        let catalog = ProviderCatalog {
            exchanges: vec![Exchange {
                exchange_id: ExchangeId::new("exchange:test").unwrap(),
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
                exchange_id: ExchangeId::new("exchange:test").unwrap(),
                exchange_symbol: Symbol::new("TEST").unwrap(),
                status: "active".into(),
                effective_from_unix_nanos: 1.into(),
                ..Default::default()
            }],
            markets: vec![Market {
                market_id,
                instrument_id,
                listing_id: Some(listing_id),
                exchange_id: ExchangeId::new("exchange:test").unwrap(),
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
        let first = reconcile_candidate(
            &mut catalog_store,
            &mut provider_store,
            &ProviderCatalog::default(),
            10,
        )
        .await
        .unwrap();
        assert!(first.changed);
        assert_eq!(first.event_count, 4);
        assert_eq!(first.generation.get(), 1);
        assert_eq!(first.event_sequence.get(), 4);
        assert_eq!(first.market_count, 1);
        let mut outbox = SqlxPublicationOutbox::open(&path).await.unwrap();
        let publications = outbox.pending_publications(10).await.unwrap();
        assert_eq!(publications.len(), 4);
        assert!(publications.iter().all(|event| {
            event.event_id.starts_with("reference:")
                && kairos_reference_contract::decode_event(&event.payload).is_ok()
        }));

        let second = reconcile_candidate(
            &mut catalog_store,
            &mut provider_store,
            &ProviderCatalog::default(),
            20,
        )
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
            exchanges: vec![Exchange {
                exchange_id: ExchangeId::new("exchange:shared").unwrap(),
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
        reconcile_candidate(
            &mut catalog_store,
            &mut provider_store,
            &ProviderCatalog::default(),
            1,
        )
        .await
        .unwrap();
        provider_store
            .save_last_good("provider-b", &catalog("inactive"))
            .await
            .unwrap();

        let error = reconcile_candidate(
            &mut catalog_store,
            &mut provider_store,
            &ProviderCatalog::default(),
            2,
        )
        .await
        .unwrap_err()
        .to_string();
        assert!(error.contains("irreconcilable canonical exchange conflict"));
        let state = catalog_store.load_runtime_snapshot().await.unwrap();
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
        let result = reconcile_candidate(
            &mut catalog_store,
            &mut provider_store,
            &ProviderCatalog::default(),
            1,
        )
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
        let reader = kairos_reference_contract::ReferenceCatalog::open(&path).unwrap();
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
    async fn provider_desired_state_control_survives_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        {
            let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
            store
                .set_source_desired_state("massive-options", SourceDesiredState::Paused)
                .await
                .unwrap();
        }

        let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
        assert_eq!(
            reopened.source_desired_states().await.unwrap(),
            vec![("massive-options".to_owned(), SourceDesiredState::Paused)]
        );
        reopened
            .set_source_desired_state("massive-options", SourceDesiredState::Enabled)
            .await
            .unwrap();
        assert_eq!(
            reopened.source_desired_states().await.unwrap(),
            vec![("massive-options".to_owned(), SourceDesiredState::Enabled)]
        );
        reopened
            .set_source_desired_state("massive-options", SourceDesiredState::Disabled)
            .await
            .unwrap();
        assert_eq!(
            reopened.source_desired_states().await.unwrap(),
            vec![("massive-options".to_owned(), SourceDesiredState::Disabled)]
        );
    }

    #[tokio::test]
    async fn source_definition_registry_survives_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let definition = ReferenceSourceDefinition {
            source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-options")
                .unwrap(),
            provider_id: kairos_primitives::market::Provider::new("massive").unwrap(),
            scope: SourceScope::underlying_instrument("instrument:equity:US:SPY:common"),
            desired_state: SourceDesiredState::Paused,
            credential_binding: Some(
                crate::domain::SourceCredentialBinding::new("massive.default").unwrap(),
            ),
            sync_policy: SourceSyncPolicy::ScopedSnapshot,
        };
        {
            let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
            store
                .upsert_source_definition(definition.clone())
                .await
                .unwrap();
        }

        let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
        assert_eq!(
            reopened.source_definitions().await.unwrap(),
            vec![definition]
        );
        assert_eq!(
            reopened.source_desired_states().await.unwrap(),
            vec![("massive-options".to_owned(), SourceDesiredState::Paused)]
        );
    }

    #[tokio::test]
    async fn provider_control_desired_state_migrates_from_legacy_paused_flag() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        {
            let pool = sqlx::sqlite::SqlitePoolOptions::new()
                .max_connections(1)
                .connect_with(
                    sqlx::sqlite::SqliteConnectOptions::new()
                        .filename(&path)
                        .create_if_missing(true),
                )
                .await
                .unwrap();
            sqlx::query(
                "CREATE TABLE reference_meta(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, generation INTEGER NOT NULL, event_sequence INTEGER NOT NULL, committed_at_unix_nanos INTEGER NOT NULL)",
            )
            .execute(&pool)
            .await
            .unwrap();
            sqlx::query(
                "INSERT INTO reference_meta(id, schema_version, generation, event_sequence, committed_at_unix_nanos) VALUES (1, 6, 0, 0, 0)",
            )
            .execute(&pool)
            .await
            .unwrap();
            sqlx::query(
                "CREATE TABLE reference_provider_control(provider TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)), updated_at_unix_nanos INTEGER NOT NULL)",
            )
            .execute(&pool)
            .await
            .unwrap();
            sqlx::query(
                "INSERT INTO reference_provider_control(provider, paused, updated_at_unix_nanos) VALUES ('massive-options', 1, 1)",
            )
            .execute(&pool)
            .await
            .unwrap();
            pool.close().await;
        }

        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        assert_eq!(
            store.source_desired_states().await.unwrap(),
            vec![("massive-options".to_owned(), SourceDesiredState::Paused)]
        );
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
