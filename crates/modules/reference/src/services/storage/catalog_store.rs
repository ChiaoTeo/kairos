use std::collections::BTreeSet;
use std::future::Future;
use std::path::Path;

use kairos_primitives::reference::{InstrumentId, ListingId, MarketId};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use sqlx::{Row, Sqlite, SqlitePool};

use crate::domain::{
    AffectedReferenceSet, Asset, Entity, Instrument, LifecycleEvent, Listing, Market,
    ReferenceCatalog, ReferenceError, ReferenceResult,
};
use crate::services::publication::EncodedPublication;
use crate::services::storage::provider_sync::{
    commit_pending_provider_promotions, reset_provider_scan_tx,
};
use crate::services::storage::sqlite::{
    open_pool, operation_lock, persistence as sqlite_persistence,
};
use crate::services::storage::startup_audit::{StartupAuditReport, startup_audit};
use crate::services::time::unix_nanos;

const LIFECYCLE_LIMIT: i64 = 4096;

pub struct SqlxCatalogStore {
    pub(crate) pool: SqlitePool,
}

impl SqlxCatalogStore {
    pub(crate) async fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let pool = open_pool(path.as_ref()).await.map_err(sqlite_persistence)?;
        Ok(Self { pool })
    }

    pub(crate) async fn audit_and_prepare_startup_repair(
        &mut self,
    ) -> ReferenceResult<StartupAuditReport> {
        let mut report = self.startup_audit().await?;
        if report.missing_provider_equity_markets.is_empty() {
            return Ok(report);
        }

        let providers = report
            .missing_provider_equity_markets
            .iter()
            .map(|value| value.provider.clone())
            .collect::<BTreeSet<_>>();
        for provider in &providers {
            self.reset_provider_scan(provider).await?;
        }
        report.reset_providers = providers.into_iter().collect();
        Ok(report)
    }

    pub(crate) async fn startup_audit(&mut self) -> ReferenceResult<StartupAuditReport> {
        self.run(|pool| async move { startup_audit(&pool).await })
            .await
    }

    async fn reset_provider_scan(&mut self, provider: &str) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            reset_provider_scan_tx(&mut tx, &provider).await?;
            tx.commit().await
        })
        .await
    }

    async fn run<T, F, Fut>(&self, operation: F) -> ReferenceResult<T>
    where
        F: FnOnce(SqlitePool) -> Fut,
        Fut: Future<Output = sqlx::Result<T>>,
    {
        let _guard = operation_lock().lock().await;
        operation(self.pool.clone())
            .await
            .map_err(sqlite_persistence)
    }

    pub(crate) async fn load_runtime_snapshot(
        &mut self,
    ) -> ReferenceResult<CatalogRuntimeSnapshot> {
        self.run(|pool| async move { load_runtime_snapshot(&pool).await })
            .await
    }

    pub(crate) async fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
        self.run(|pool| async move { load(&pool).await }).await
    }

    pub(crate) async fn save_refresh(
        &mut self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
        publications: &[EncodedPublication],
    ) -> ReferenceResult<CatalogSaveOutcome> {
        let event_payloads = events
            .iter()
            .map(|event| serde_json::to_string(event).map(|payload| (event, payload)))
            .collect::<Result<Vec<_>, _>>()
            .map_err(persistence)?;
        let event_count = events.len() as u64;
        let result = self
            .run(|pool| async move {
                save_refresh(&pool, catalog, events.len(), event_payloads, publications).await
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

    async fn lifecycle_payloads(
        &self,
        from: Option<u64>,
        to: Option<u64>,
        time_from: Option<u64>,
        time_to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.run(|pool| async move {
            lifecycle_payloads(&pool, from, to, time_from, time_to, limit).await
        })
        .await
    }
}

#[derive(Clone, Copy, Default)]
#[cfg_attr(test, allow(dead_code))]
pub(crate) struct CatalogRuntimeSnapshot {
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub committed_at_unix_nanos: UnixNanos,
    pub entity_count: usize,
    pub asset_count: usize,
    pub instrument_count: usize,
    pub listing_count: usize,
    pub market_count: usize,
    pub active_market_count: usize,
    pub lifecycle_event_count: usize,
    pub missing_equity_market_count: usize,
    pub legacy_exchange_market_id_count: usize,
    pub legacy_exchange_listing_id_count: usize,
    pub option_listing_count: usize,
    pub option_market_count: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum CatalogWriteMode {
    AffectedUpdate,
    FullReplace,
}

impl CatalogWriteMode {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::AffectedUpdate => "affected_update",
            Self::FullReplace => "full_replace",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct AffectedReferenceSetSummary {
    pub total_count: usize,
    pub entity_count: usize,
    pub asset_count: usize,
    pub instrument_count: usize,
    pub listing_count: usize,
    pub market_count: usize,
    pub requires_full_replace: bool,
}

impl From<&AffectedReferenceSet> for AffectedReferenceSetSummary {
    fn from(affected: &AffectedReferenceSet) -> Self {
        Self {
            total_count: affected.total_count(),
            entity_count: affected.entities.len(),
            asset_count: affected.assets.len(),
            instrument_count: affected.instruments.len(),
            listing_count: affected.listings.len(),
            market_count: affected.markets.len(),
            requires_full_replace: affected.requires_full_replace,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct CatalogSaveOutcome {
    pub affected: AffectedReferenceSetSummary,
    pub write_mode: CatalogWriteMode,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct CatalogReconcileSummary {
    pub affected_write_mode: &'static str,
    pub affected_total_count: usize,
    pub affected_entity_count: usize,
    pub affected_asset_count: usize,
    pub affected_instrument_count: usize,
    pub affected_listing_count: usize,
    pub affected_market_count: usize,
}

impl CatalogReconcileSummary {
    pub(crate) fn from_save_outcome(
        outcome: Option<&CatalogSaveOutcome>,
        affected: &AffectedReferenceSet,
    ) -> Self {
        match outcome {
            Some(outcome) => Self {
                affected_write_mode: outcome.write_mode.as_str(),
                affected_total_count: outcome.affected.total_count,
                affected_entity_count: outcome.affected.entity_count,
                affected_asset_count: outcome.affected.asset_count,
                affected_instrument_count: outcome.affected.instrument_count,
                affected_listing_count: outcome.affected.listing_count,
                affected_market_count: outcome.affected.market_count,
            },
            None => Self {
                affected_write_mode: affected.write_mode(),
                affected_total_count: affected.total_count(),
                affected_entity_count: affected.entities.len(),
                affected_asset_count: affected.assets.len(),
                affected_instrument_count: affected.instruments.len(),
                affected_listing_count: affected.listings.len(),
                affected_market_count: affected.markets.len(),
            },
        }
    }
}

pub(crate) async fn load_runtime_snapshot(
    pool: &SqlitePool,
) -> sqlx::Result<CatalogRuntimeSnapshot> {
    let (
        generation,
        event_sequence,
        committed_at_unix_nanos,
        entity_count,
        asset_count,
        instrument_count,
        listing_count,
        market_count,
        active_market_count,
        lifecycle_event_count,
        missing_equity_market_count,
        legacy_exchange_market_id_count,
        legacy_exchange_listing_id_count,
        option_listing_count,
        option_market_count,
    ) = sqlx::query_as::<
        _,
        (
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
            i64,
        ),
    >(
        "SELECT generation,
                event_sequence,
                committed_at_unix_nanos,
                (SELECT COUNT(*) FROM reference_entities_current),
                (SELECT COUNT(*) FROM reference_assets_current),
                (SELECT COUNT(*) FROM reference_instruments_current),
                (SELECT COUNT(*) FROM reference_listings_current),
                (SELECT COUNT(*) FROM reference_markets_current),
                (SELECT COUNT(*) FROM reference_markets_current WHERE status IN ('active','trading')),
                (SELECT COUNT(*) FROM reference_lifecycle),
                (SELECT COUNT(*)
                 FROM reference_listings_current AS listing
                 WHERE listing.status IN ('active','trading')
                   AND listing.listing_id LIKE '%:equity:%'
                   AND NOT EXISTS (
                     SELECT 1 FROM reference_markets_current AS market
                     WHERE market.listing_id = listing.listing_id
                   )),
                (SELECT COUNT(*) FROM reference_markets_current
                 WHERE market_id LIKE 'market:exchange:%'),
                (SELECT COUNT(*) FROM reference_listings_current
                 WHERE listing_id LIKE 'listing:exchange:%'),
                (SELECT COUNT(*) FROM reference_listings_current
                 WHERE listing_id LIKE '%:option:%'),
                (SELECT COUNT(*) FROM reference_markets_current
                 WHERE instrument_kind = 'option')
         FROM reference_meta
         WHERE id=1",
    )
    .fetch_one(pool)
    .await?;
    Ok(CatalogRuntimeSnapshot {
        generation: (generation as u64).into(),
        event_sequence: (event_sequence as u64).into(),
        committed_at_unix_nanos: (committed_at_unix_nanos as u64).into(),
        entity_count: entity_count as usize,
        asset_count: asset_count as usize,
        instrument_count: instrument_count as usize,
        listing_count: listing_count as usize,
        market_count: market_count as usize,
        active_market_count: active_market_count as usize,
        lifecycle_event_count: lifecycle_event_count as usize,
        missing_equity_market_count: missing_equity_market_count as usize,
        legacy_exchange_market_id_count: legacy_exchange_market_id_count as usize,
        legacy_exchange_listing_id_count: legacy_exchange_listing_id_count as usize,
        option_listing_count: option_listing_count as usize,
        option_market_count: option_market_count as usize,
    })
}

pub(crate) async fn load(pool: &SqlitePool) -> sqlx::Result<Option<ReferenceCatalog>> {
    let meta = sqlx::query("SELECT generation,event_sequence FROM reference_meta WHERE id = 1")
        .fetch_optional(pool)
        .await?;
    let Some(meta) = meta else {
        return Ok(None);
    };
    macro_rules! records {
        ($table:literal, $key:ident, $type:ty) => {{
            let rows = sqlx::query(concat!("SELECT payload FROM ", $table, " ORDER BY 1"))
                .fetch_all(pool)
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
    let asset_rows = sqlx::query("SELECT payload FROM reference_assets_current ORDER BY asset_id")
        .fetch_all(pool)
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
    catalog.lifecycle_events = recent_lifecycle_payloads(pool).await?;
    Ok(Some(catalog))
}

pub(crate) async fn save_refresh(
    pool: &SqlitePool,
    catalog: &ReferenceCatalog,
    events_len: usize,
    event_payloads: Vec<(&LifecycleEvent, String)>,
    publications: &[EncodedPublication],
) -> sqlx::Result<CatalogSaveOutcome> {
    let affected = AffectedReferenceSet::from_events(
        event_payloads
            .iter()
            .map(|(event, _)| *event)
            .collect::<Vec<_>>(),
    );
    let mut tx = pool.begin().await?;
    commit_pending_provider_promotions(&mut tx).await?;
    let write_mode;
    if affected.requires_full_replace
        || (affected.is_empty() && current_projection_is_empty(&mut tx).await?)
    {
        write_mode = CatalogWriteMode::FullReplace;
        replace_current_state(&mut tx, catalog).await?;
    } else {
        write_mode = CatalogWriteMode::AffectedUpdate;
        update_affected_current_state(&mut tx, catalog, &affected).await?;
        update_catalog_meta(&mut tx, catalog).await?;
    }
    for (offset, (event, payload)) in event_payloads.into_iter().enumerate() {
        let market_id = event.market_id.as_ref().map(ToString::to_string);
        let exchange_id = event.exchange_id.as_ref().map(ToString::to_string);
        let sequence = catalog
            .event_sequence
            .get()
            .saturating_sub(events_len as u64)
            .saturating_add(offset as u64 + 1) as i64;
        sqlx::query("INSERT OR IGNORE INTO reference_lifecycle(sequence,event_type,record_kind,record_id,market_id,exchange_id,event_time_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?)")
            .bind(sequence)
            .bind(&event.event_type)
            .bind(&event.record_kind)
            .bind(&event.record_id)
            .bind(market_id)
            .bind(exchange_id)
            .bind(event.event_time_unix_nanos.get() as i64)
            .bind(&payload)
            .execute(&mut *tx)
            .await?;
    }
    for publication in publications {
        sqlx::query("INSERT OR IGNORE INTO reference_publication_outbox(sequence,event_id,payload) VALUES (?,?,?)")
            .bind(publication.sequence as i64)
            .bind(&publication.event_id)
            .bind(&publication.payload)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(CatalogSaveOutcome {
        affected: AffectedReferenceSetSummary::from(&affected),
        write_mode,
    })
}

async fn current_projection_is_empty(tx: &mut sqlx::Transaction<'_, Sqlite>) -> sqlx::Result<bool> {
    let count = sqlx::query_scalar::<_, i64>(
        "SELECT \
           (SELECT COUNT(*) FROM reference_entities_current) + \
           (SELECT COUNT(*) FROM reference_assets_current) + \
           (SELECT COUNT(*) FROM reference_instruments_current) + \
           (SELECT COUNT(*) FROM reference_listings_current) + \
           (SELECT COUNT(*) FROM reference_markets_current)",
    )
    .fetch_one(&mut **tx)
    .await?;
    Ok(count == 0)
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

async fn update_affected_current_state(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    catalog: &ReferenceCatalog,
    affected: &AffectedReferenceSet,
) -> sqlx::Result<()> {
    for id in &affected.entities {
        if let Some(entity) = catalog.entities.get(id) {
            upsert_entity(tx, entity).await?;
        } else {
            sqlx::query("DELETE FROM reference_entities_current WHERE entity_id = ?")
                .bind(id)
                .execute(&mut **tx)
                .await?;
        }
    }
    for id in &affected.assets {
        if let Some(asset) = catalog.assets.get(id) {
            upsert_asset(tx, asset).await?;
        } else {
            sqlx::query("DELETE FROM reference_assets_current WHERE asset_id = ?")
                .bind(id)
                .execute(&mut **tx)
                .await?;
        }
    }
    for id in &affected.instruments {
        let instrument_id =
            InstrumentId::new(id).map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        if let Some(instrument) = catalog.instruments.get(&instrument_id) {
            upsert_instrument(tx, instrument).await?;
        } else {
            sqlx::query("DELETE FROM reference_instruments_current WHERE instrument_id = ?")
                .bind(id)
                .execute(&mut **tx)
                .await?;
        }
    }
    for id in &affected.listings {
        let listing_id =
            ListingId::new(id).map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        if let Some(listing) = catalog.listings.get(&listing_id) {
            upsert_listing(tx, listing).await?;
        } else {
            sqlx::query("DELETE FROM reference_listings_current WHERE listing_id = ?")
                .bind(id)
                .execute(&mut **tx)
                .await?;
        }
    }
    for id in &affected.markets {
        let market_id =
            MarketId::new(id).map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        if let Some(market) = catalog.markets.get(&market_id) {
            upsert_market(tx, market).await?;
        } else {
            sqlx::query("DELETE FROM reference_markets_current WHERE market_id = ?")
                .bind(id)
                .execute(&mut **tx)
                .await?;
        }
    }
    Ok(())
}

async fn upsert_entity(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    entity: &Entity,
) -> sqlx::Result<()> {
    sqlx::query("INSERT INTO reference_entities_current(entity_id,entity_type,status,payload) VALUES (?,?,?,?) ON CONFLICT(entity_id) DO UPDATE SET entity_type=excluded.entity_type,status=excluded.status,payload=excluded.payload WHERE reference_entities_current.payload<>excluded.payload")
        .bind(&entity.entity_id)
        .bind(entity.entity_type.as_str())
        .bind(entity.status.as_str())
        .bind(serde_json::to_string(entity).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn upsert_asset(tx: &mut sqlx::Transaction<'_, Sqlite>, asset: &Asset) -> sqlx::Result<()> {
    sqlx::query("INSERT INTO reference_assets_current(asset_id,code,asset_class,status,payload) VALUES (?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET code=excluded.code,asset_class=excluded.asset_class,status=excluded.status,payload=excluded.payload WHERE reference_assets_current.payload<>excluded.payload")
        .bind(asset.asset_id.as_str())
        .bind(asset.code.as_str())
        .bind(asset.asset_class.as_str())
        .bind(asset.status.as_str())
        .bind(serde_json::to_string(asset).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn upsert_instrument(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    instrument: &Instrument,
) -> sqlx::Result<()> {
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
    Ok(())
}

async fn upsert_listing(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    listing: &Listing,
) -> sqlx::Result<()> {
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
    Ok(())
}

async fn upsert_market(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    market: &Market,
) -> sqlx::Result<()> {
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
    Ok(())
}

async fn update_catalog_meta(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    catalog: &ReferenceCatalog,
) -> sqlx::Result<()> {
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

pub(crate) async fn lifecycle_payloads(
    pool: &SqlitePool,
    from: Option<u64>,
    to: Option<u64>,
    time_from: Option<u64>,
    time_to: Option<u64>,
    limit: usize,
) -> sqlx::Result<Vec<LifecycleEvent>> {
    let rows = sqlx::query("SELECT payload FROM reference_lifecycle WHERE sequence >= COALESCE(?, 1) AND sequence <= COALESCE(?, 9223372036854775807) AND (? IS NULL OR event_time_unix_nanos >= ?) AND (? IS NULL OR event_time_unix_nanos < ?) ORDER BY sequence LIMIT ?")
        .bind(from.map(|value| value as i64))
        .bind(to.map(|value| value as i64))
        .bind(time_from.map(|value| value as i64))
        .bind(time_from.map(|value| value as i64))
        .bind(time_to.map(|value| value as i64))
        .bind(time_to.map(|value| value as i64))
        .bind(limit as i64)
        .fetch_all(pool)
        .await?;
    rows.into_iter()
        .map(|row| {
            decode(row.try_get("payload")?)
                .map_err(|error| sqlx::Error::Protocol(error.to_string()))
        })
        .collect()
}

pub(crate) async fn recent_lifecycle_payloads(
    pool: &SqlitePool,
) -> sqlx::Result<Vec<LifecycleEvent>> {
    let rows =
        sqlx::query("SELECT payload FROM reference_lifecycle ORDER BY sequence DESC LIMIT ?")
            .bind(LIFECYCLE_LIMIT)
            .fetch_all(pool)
            .await?;
    let mut events = rows
        .into_iter()
        .map(|row| {
            decode(row.try_get("payload")?)
                .map_err(|error| sqlx::Error::Protocol(error.to_string()))
        })
        .collect::<Result<Vec<_>, _>>()?;
    events.reverse();
    Ok(events)
}

fn decode<T: serde::de::DeserializeOwned>(payload: String) -> ReferenceResult<T> {
    serde_json::from_str(&payload).map_err(persistence)
}

fn persistence(error: impl std::fmt::Display) -> ReferenceError {
    ReferenceError::Persistence(error.to_string())
}

#[cfg(test)]
mod tests {
    use crate::domain::{AffectedReferenceSet, LifecycleEvent};

    use super::{
        AffectedReferenceSetSummary, CatalogReconcileSummary, CatalogSaveOutcome, CatalogWriteMode,
    };

    #[test]
    fn reconcile_summary_prefers_committed_save_outcome() {
        let mut affected = AffectedReferenceSet::default();
        affected
            .markets
            .insert("market:binance:spot:btc-usdt".into());
        let outcome = CatalogSaveOutcome {
            write_mode: CatalogWriteMode::FullReplace,
            affected: AffectedReferenceSetSummary {
                total_count: 5,
                entity_count: 1,
                asset_count: 1,
                instrument_count: 1,
                listing_count: 1,
                market_count: 1,
                requires_full_replace: true,
            },
        };

        let summary = CatalogReconcileSummary::from_save_outcome(Some(&outcome), &affected);

        assert_eq!(summary.affected_write_mode, "full_replace");
        assert_eq!(summary.affected_total_count, 5);
        assert_eq!(summary.affected_market_count, 1);
    }

    #[test]
    fn reconcile_summary_falls_back_to_domain_affected_set() {
        let events = [LifecycleEvent {
            record_kind: Some("asset".into()),
            record_id: Some("asset:BTC".into()),
            ..LifecycleEvent::default()
        }];
        let affected = AffectedReferenceSet::from_events(events.iter());

        let summary = CatalogReconcileSummary::from_save_outcome(None, &affected);

        assert_eq!(summary.affected_write_mode, "affected_update");
        assert_eq!(summary.affected_total_count, 1);
        assert_eq!(summary.affected_asset_count, 1);
    }
}
