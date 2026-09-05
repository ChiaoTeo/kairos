use std::collections::BTreeSet;
use std::future::Future;
use std::path::Path;

use kairos_primitives::reference::{InstrumentId, ListingId, MarketId, VenueId};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use kairos_reference_contract::{
    ListingRole as ContractListingRole, TradingRules as ContractTradingRules,
    Venue as ContractVenue, VenueKind as ContractVenueKind, VenueListing as ContractVenueListing,
    VenueMarket as ContractVenueMarket, VenueRole as ContractVenueRole,
};
use sqlx::{Row, Sqlite, SqlitePool};

use crate::domain::{
    AffectedReferenceSet, Asset, Exchange, Instrument, LifecycleEvent, Listing, Market,
    ProviderCatalogMembership, ReferenceCatalog, ReferenceError, ReferenceResult, Venue,
    VenueListing, VenueMarket,
};
use crate::services::publication::EncodedPublication;
use crate::services::storage::provider_sync::{finalize_source_changes, reset_provider_scan_tx};
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

    pub(crate) async fn load_runtime_metrics(&mut self) -> ReferenceResult<CatalogRuntimeMetrics> {
        self.run(|pool| async move { load_runtime_metrics(&pool).await })
            .await
    }

    pub(crate) async fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
        self.run(|pool| async move { load(&pool).await }).await
    }

    pub(crate) async fn save_refresh(
        &mut self,
        catalog: &ReferenceCatalog,
        events: impl ExactSizeIterator<Item = LifecycleEvent> + Clone,
        publications: impl IntoIterator<Item = ReferenceResult<EncodedPublication>>,
        source_changes: Option<&crate::services::sources::SourceChanges>,
    ) -> ReferenceResult<CatalogSaveOutcome> {
        let event_count = events.len() as u64;
        let result = self
            .run(|pool| async move {
                save_refresh(&pool, catalog, events, publications, source_changes).await
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
}

#[derive(Clone, Copy, Default)]
#[cfg_attr(test, allow(dead_code))]
pub(crate) struct CatalogRuntimeMetrics {
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub committed_at_unix_nanos: UnixNanos,
    pub exchange_count: usize,
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
    pub coverage_count: usize,
    pub usable_coverage_count: usize,
    pub stale_coverage_count: usize,
    pub unavailable_coverage_count: usize,
    pub unresolved_venue_mapping_count: usize,
    pub v2_unprojectable_market_count: usize,
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
    pub exchange_count: usize,
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
            exchange_count: affected.exchanges.len(),
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
    pub affected_exchange_count: usize,
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
                affected_exchange_count: outcome.affected.exchange_count,
                affected_asset_count: outcome.affected.asset_count,
                affected_instrument_count: outcome.affected.instrument_count,
                affected_listing_count: outcome.affected.listing_count,
                affected_market_count: outcome.affected.market_count,
            },
            None => Self {
                affected_write_mode: affected.write_mode(),
                affected_total_count: affected.total_count(),
                affected_exchange_count: affected.exchanges.len(),
                affected_asset_count: affected.assets.len(),
                affected_instrument_count: affected.instruments.len(),
                affected_listing_count: affected.listings.len(),
                affected_market_count: affected.markets.len(),
            },
        }
    }
}

pub(crate) async fn load_runtime_metrics(pool: &SqlitePool) -> sqlx::Result<CatalogRuntimeMetrics> {
    let (
        generation,
        event_sequence,
        committed_at_unix_nanos,
        exchange_count,
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
            i64, i64, i64, i64, i64, i64, i64, i64, i64, i64, i64, i64, i64, i64,
            i64,
        ),
    >(
        "SELECT generation,
                event_sequence,
                committed_at_unix_nanos,
                (SELECT COUNT(*) FROM reference_exchanges_current),
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
    let (
        coverage_count,
        usable_coverage_count,
        stale_coverage_count,
        unavailable_coverage_count,
        unresolved_venue_mapping_count,
        v2_unprojectable_market_count,
    ) = sqlx::query_as::<_, (i64, i64, i64, i64, i64, i64)>(
        "SELECT
            (SELECT COUNT(*) FROM reference_coverage_current),
            (SELECT COUNT(*) FROM reference_coverage_current WHERE state = 'usable'),
            (SELECT COUNT(*) FROM reference_coverage_current WHERE state = 'stale'),
            (SELECT COUNT(*) FROM reference_coverage_current WHERE state IN ('unavailable','retry_waiting')),
            (SELECT COUNT(*) FROM reference_venue_markets_current market
             LEFT JOIN reference_venues_current venue ON venue.venue_id = market.execution_venue_id
             WHERE venue.venue_id IS NULL),
            (SELECT COUNT(*) FROM reference_venue_markets_current market
             LEFT JOIN reference_venues_current venue ON venue.venue_id = market.execution_venue_id
             WHERE venue.venue_id IS NULL OR venue.venue_kind <> 'regulated_exchange')",
    )
    .fetch_one(pool)
    .await?;
    Ok(CatalogRuntimeMetrics {
        generation: (generation as u64).into(),
        event_sequence: (event_sequence as u64).into(),
        committed_at_unix_nanos: (committed_at_unix_nanos as u64).into(),
        exchange_count: exchange_count as usize,
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
        coverage_count: coverage_count as usize,
        usable_coverage_count: usable_coverage_count as usize,
        stale_coverage_count: stale_coverage_count as usize,
        unavailable_coverage_count: unavailable_coverage_count as usize,
        unresolved_venue_mapping_count: unresolved_venue_mapping_count as usize,
        v2_unprojectable_market_count: v2_unprojectable_market_count as usize,
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
    let membership_rows = sqlx::query(
        "SELECT payload FROM reference_provider_catalog_memberships_current \
         ORDER BY source_id,instrument_id",
    )
    .fetch_all(pool)
    .await?;
    let provider_catalog_memberships = membership_rows
        .into_iter()
        .map(|row| {
            let value: ProviderCatalogMembership = decode(row.try_get::<String, _>("payload")?)
                .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
            Ok((
                (value.source_id.clone(), value.instrument_id.clone()),
                value,
            ))
        })
        .collect::<Result<_, sqlx::Error>>()?;
    let mapping_rows = sqlx::query(
        "SELECT payload FROM reference_venue_identifier_mappings_current
         ORDER BY provider,provider_product,identifier_kind,identifier",
    )
    .fetch_all(pool)
    .await?;
    let venue_identifier_mappings = mapping_rows
        .into_iter()
        .map(|row| {
            let value: crate::domain::VenueIdentifierMapping =
                decode(row.try_get::<String, _>("payload")?)
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
            Ok((
                (
                    value.provider.to_string(),
                    value.provider_product.clone(),
                    value.identifier_kind.as_str().to_owned(),
                    value.identifier.clone(),
                ),
                value,
            ))
        })
        .collect::<Result<_, sqlx::Error>>()?;
    let mut catalog = ReferenceCatalog {
        venues: records!("reference_venues_current", venue_id, Venue),
        exchanges: records!("reference_exchanges_current", exchange_id, Exchange),
        assets,
        instruments: records!("reference_instruments_current", instrument_id, Instrument),
        listings: records!("reference_listings_current", listing_id, Listing),
        markets: records!("reference_markets_current", market_id, Market),
        venue_listings: records!("reference_venue_listings_current", listing_id, VenueListing),
        venue_markets: records!("reference_venue_markets_current", market_id, VenueMarket),
        provider_catalog_memberships,
        venue_identifier_mappings,
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
    events: impl ExactSizeIterator<Item = LifecycleEvent> + Clone,
    publications: impl IntoIterator<Item = ReferenceResult<EncodedPublication>>,
    source_changes: Option<&crate::services::sources::SourceChanges>,
) -> sqlx::Result<CatalogSaveOutcome> {
    let event_count = events.len();
    let mut tx = pool.begin().await?;
    let affected = stage_affected_keys(&mut tx, events.clone()).await?;
    if let Some(source_changes) = source_changes {
        finalize_source_changes(
            &mut tx,
            catalog.generation,
            catalog.event_sequence,
            source_changes,
        )
        .await?;
    }
    let write_mode;
    if affected.requires_full_replace
        || (affected.total_count == 0 && current_catalog_is_empty(&mut tx).await?)
    {
        write_mode = CatalogWriteMode::FullReplace;
        replace_current_state(&mut tx, catalog).await?;
    } else {
        write_mode = CatalogWriteMode::AffectedUpdate;
        update_staged_current_state(&mut tx, catalog).await?;
        replace_venue_identifier_mappings(&mut tx, catalog).await?;
        update_catalog_meta(&mut tx, catalog).await?;
    }
    for (offset, event) in events.enumerate() {
        let payload = serde_json::to_string(&event).map_err(protocol)?;
        let market_id = event.market_id.as_ref().map(ToString::to_string);
        let exchange_id = event.exchange_id.as_ref().map(ToString::to_string);
        let sequence = catalog
            .event_sequence
            .get()
            .saturating_sub(event_count as u64)
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
        let publication = publication.map_err(protocol)?;
        sqlx::query("INSERT OR IGNORE INTO reference_publication_outbox(sequence,event_id,payload) VALUES (?,?,?)")
            .bind(publication.sequence as i64)
            .bind(&publication.event_id)
            .bind(&publication.payload)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(CatalogSaveOutcome {
        affected,
        write_mode,
    })
}

async fn stage_affected_keys(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    events: impl Iterator<Item = LifecycleEvent>,
) -> sqlx::Result<AffectedReferenceSetSummary> {
    sqlx::query("CREATE TEMP TABLE IF NOT EXISTS reference_reconcile_keys(record_kind TEXT NOT NULL, record_id TEXT NOT NULL, PRIMARY KEY(record_kind, record_id)) WITHOUT ROWID")
        .execute(&mut **tx)
        .await?;
    sqlx::query("DELETE FROM reference_reconcile_keys")
        .execute(&mut **tx)
        .await?;
    let mut summary = AffectedReferenceSetSummary {
        total_count: 0,
        exchange_count: 0,
        asset_count: 0,
        instrument_count: 0,
        listing_count: 0,
        market_count: 0,
        requires_full_replace: false,
    };
    for event in events {
        match (event.record_kind.as_deref(), event.record_id.as_deref()) {
            (
                Some(kind @ ("exchange" | "asset" | "instrument" | "listing" | "market")),
                Some(id),
            ) => {
                sqlx::query("INSERT OR IGNORE INTO reference_reconcile_keys(record_kind,record_id) VALUES (?,?)")
                    .bind(kind)
                    .bind(id)
                    .execute(&mut **tx)
                    .await?;
            },
            _ => summary.requires_full_replace = true,
        }
    }
    let counts: Vec<(String, i64)> = sqlx::query_as(
        "SELECT record_kind,COUNT(*) FROM reference_reconcile_keys GROUP BY record_kind",
    )
    .fetch_all(&mut **tx)
    .await?;
    for (kind, count) in counts {
        let count = usize::try_from(count).map_err(protocol)?;
        summary.total_count += count;
        match kind.as_str() {
            "exchange" => summary.exchange_count = count,
            "asset" => summary.asset_count = count,
            "instrument" => summary.instrument_count = count,
            "listing" => summary.listing_count = count,
            "market" => summary.market_count = count,
            _ => unreachable!("only recognized record kinds are staged"),
        }
    }
    Ok(summary)
}

async fn update_staged_current_state(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    catalog: &ReferenceCatalog,
) -> sqlx::Result<()> {
    let mut cursor = (String::new(), String::new());
    loop {
        let keys: Vec<(String, String)> = sqlx::query_as(
            "SELECT record_kind,record_id FROM reference_reconcile_keys \
             WHERE (record_kind,record_id) > (?,?) ORDER BY record_kind,record_id LIMIT 512",
        )
        .bind(&cursor.0)
        .bind(&cursor.1)
        .fetch_all(&mut **tx)
        .await?;
        let Some(last) = keys.last() else { break };
        cursor = last.clone();
        let mut affected = AffectedReferenceSet::default();
        for (kind, id) in keys {
            let set = match kind.as_str() {
                "exchange" => &mut affected.exchanges,
                "asset" => &mut affected.assets,
                "instrument" => &mut affected.instruments,
                "listing" => &mut affected.listings,
                "market" => &mut affected.markets,
                _ => unreachable!("only recognized record kinds are staged"),
            };
            set.insert(id);
        }
        update_affected_current_state(tx, catalog, &affected).await?;
    }
    Ok(())
}

async fn current_catalog_is_empty(tx: &mut sqlx::Transaction<'_, Sqlite>) -> sqlx::Result<bool> {
    let count = sqlx::query_scalar::<_, i64>(
        "SELECT \
           (SELECT COUNT(*) FROM reference_exchanges_current) + \
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

    for exchange in catalog.exchanges.values() {
        track!("exchange", exchange.exchange_id.as_str());
        sqlx::query("INSERT INTO reference_exchanges_current(exchange_id,status,payload) VALUES (?,?,?) ON CONFLICT(exchange_id) DO UPDATE SET status=excluded.status,payload=excluded.payload WHERE reference_exchanges_current.payload<>excluded.payload")
            .bind(exchange.exchange_id.as_str())
            .bind(exchange.status.as_str())
            .bind(serde_json::to_string(exchange).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
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
        "DELETE FROM reference_exchanges_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='exchange' AND k.record_id=reference_exchanges_current.exchange_id)",
        "DELETE FROM reference_assets_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='asset' AND k.record_id=reference_assets_current.asset_id)",
        "DELETE FROM reference_instruments_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='instrument' AND k.record_id=reference_instruments_current.instrument_id)",
        "DELETE FROM reference_listings_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='listing' AND k.record_id=reference_listings_current.listing_id)",
        "DELETE FROM reference_markets_current WHERE NOT EXISTS (SELECT 1 FROM reference_reconcile_keys k WHERE k.record_kind='market' AND k.record_id=reference_markets_current.market_id)",
    ] {
        sqlx::query(statement).execute(&mut **tx).await?;
    }
    replace_venue_current_tables(tx, catalog).await?;
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
    for id in &affected.exchanges {
        if let Some(exchange) = catalog.exchanges.get(id) {
            upsert_exchange(tx, exchange).await?;
        } else {
            sqlx::query("DELETE FROM reference_exchanges_current WHERE exchange_id = ?")
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
    update_legacy_mapped_venue_tables(tx, catalog, affected).await?;
    Ok(())
}

async fn replace_venue_current_tables(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    catalog: &ReferenceCatalog,
) -> sqlx::Result<()> {
    sqlx::query("DELETE FROM reference_venues_current")
        .execute(&mut **tx)
        .await?;
    sqlx::query("DELETE FROM reference_venue_listings_current")
        .execute(&mut **tx)
        .await?;
    sqlx::query("DELETE FROM reference_venue_markets_current")
        .execute(&mut **tx)
        .await?;
    sqlx::query("DELETE FROM reference_venue_identifier_mappings_current")
        .execute(&mut **tx)
        .await?;
    for exchange in catalog.exchanges.values() {
        upsert_v3_venue(tx, catalog, &exchange.exchange_id).await?;
    }
    for listing in catalog.listings.values() {
        upsert_v3_listing(tx, listing).await?;
    }
    for market in catalog.markets.values() {
        upsert_v3_market(tx, market).await?;
    }
    for venue in catalog.venues.values() {
        upsert_canonical_venue(tx, venue).await?;
    }
    for listing in catalog.venue_listings.values() {
        upsert_canonical_venue_listing(tx, listing).await?;
    }
    for market in catalog.venue_markets.values() {
        upsert_canonical_venue_market(tx, market).await?;
    }
    replace_venue_identifier_mappings(tx, catalog).await?;
    Ok(())
}

async fn replace_venue_identifier_mappings(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    catalog: &ReferenceCatalog,
) -> sqlx::Result<()> {
    sqlx::query("DELETE FROM reference_venue_identifier_mappings_current")
        .execute(&mut **tx)
        .await?;
    for mapping in catalog.venue_identifier_mappings.values() {
        let mapping_key = format!(
            "{}|{}|{}|{}",
            mapping.provider,
            mapping.provider_product,
            mapping.identifier_kind.as_str(),
            mapping.identifier
        );
        let payload = serde_json::to_string(mapping).map_err(protocol)?;
        sqlx::query("INSERT INTO reference_venue_identifier_mappings_current(mapping_key,source_id,provider,provider_product,identifier_kind,identifier,venue_id,status,payload) VALUES (?,?,?,?,?,?,?,?,?)")
            .bind(mapping_key)
            .bind(mapping.source_id.as_str())
            .bind(mapping.provider.as_str())
            .bind(&mapping.provider_product)
            .bind(mapping.identifier_kind.as_str())
            .bind(&mapping.identifier)
            .bind(mapping.venue_id.as_str())
            .bind(mapping.status.as_str())
            .bind(payload)
            .execute(&mut **tx)
            .await?;
    }
    Ok(())
}

async fn upsert_canonical_venue(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    venue: &Venue,
) -> sqlx::Result<()> {
    let payload = serde_json::to_string(venue).map_err(protocol)?;
    sqlx::query("INSERT INTO reference_venues_current(venue_id,venue_kind,mic,operating_mic,parent_venue_id,jurisdiction,status,payload) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(venue_id) DO UPDATE SET venue_kind=excluded.venue_kind,mic=excluded.mic,operating_mic=excluded.operating_mic,parent_venue_id=excluded.parent_venue_id,jurisdiction=excluded.jurisdiction,status=excluded.status,payload=excluded.payload")
        .bind(venue.venue_id.as_str())
        .bind(venue.venue_kind.as_str())
        .bind(venue.mic.as_ref().map(|value| value.as_str()))
        .bind(venue.operating_mic.as_ref().map(|value| value.as_str()))
        .bind(venue.parent_venue_id.as_ref().map(|value| value.as_str()))
        .bind(venue.jurisdiction.as_ref().map(|value| value.as_str()))
        .bind(venue.status.as_str())
        .bind(payload)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn upsert_canonical_venue_listing(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    listing: &VenueListing,
) -> sqlx::Result<()> {
    let payload = serde_json::to_string(listing).map_err(protocol)?;
    sqlx::query("INSERT INTO reference_venue_listings_current(listing_id,instrument_id,listing_venue_id,market_segment_id,listing_symbol,listing_role,status,effective_to_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(listing_id) DO UPDATE SET instrument_id=excluded.instrument_id,listing_venue_id=excluded.listing_venue_id,market_segment_id=excluded.market_segment_id,listing_symbol=excluded.listing_symbol,listing_role=excluded.listing_role,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload")
        .bind(listing.listing_id.as_str())
        .bind(listing.instrument_id.as_str())
        .bind(listing.listing_venue_id.as_str())
        .bind(listing.market_segment_id.as_ref().map(|value| value.as_str()))
        .bind(listing.listing_symbol.as_str())
        .bind(listing.listing_role.as_str())
        .bind(listing.status.as_str())
        .bind(listing.effective_to_unix_nanos.map(|value| value.get() as i64))
        .bind(payload)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn upsert_canonical_venue_market(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    market: &VenueMarket,
) -> sqlx::Result<()> {
    let payload = serde_json::to_string(market).map_err(protocol)?;
    sqlx::query("INSERT INTO reference_venue_markets_current(market_id,instrument_id,execution_venue_id,origin_listing_id,market_segment_id,venue_symbol,status,effective_to_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(market_id) DO UPDATE SET instrument_id=excluded.instrument_id,execution_venue_id=excluded.execution_venue_id,origin_listing_id=excluded.origin_listing_id,market_segment_id=excluded.market_segment_id,venue_symbol=excluded.venue_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload")
        .bind(market.market_id.as_str())
        .bind(market.instrument_id.as_str())
        .bind(market.execution_venue_id.as_str())
        .bind(market.origin_listing_id.as_ref().map(|value| value.as_str()))
        .bind(market.market_segment_id.as_ref().map(|value| value.as_str()))
        .bind(market.venue_symbol.as_ref().map(|value| value.as_str()))
        .bind(market.status.as_str())
        .bind(market.effective_to_unix_nanos.map(|value| value.get() as i64))
        .bind(payload)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn update_legacy_mapped_venue_tables(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    catalog: &ReferenceCatalog,
    affected: &AffectedReferenceSet,
) -> sqlx::Result<()> {
    let mut affected_exchanges = affected.exchanges.clone();
    for id in &affected.listings {
        let listing_id = ListingId::new(id).map_err(protocol)?;
        if let Some(listing) = catalog.listings.get(&listing_id) {
            affected_exchanges.insert(listing.exchange_id.to_string());
            upsert_v3_listing(tx, listing).await?;
        } else {
            sqlx::query("DELETE FROM reference_venue_listings_current WHERE listing_id = ?")
                .bind(id)
                .execute(&mut **tx)
                .await?;
        }
    }
    for id in &affected.markets {
        let market_id = MarketId::new(id).map_err(protocol)?;
        if let Some(market) = catalog.markets.get(&market_id) {
            affected_exchanges.insert(market.exchange_id.to_string());
            upsert_v3_market(tx, market).await?;
        } else {
            sqlx::query("DELETE FROM reference_venue_markets_current WHERE market_id = ?")
                .bind(id)
                .execute(&mut **tx)
                .await?;
        }
    }
    for exchange_id in affected_exchanges {
        let exchange_id =
            kairos_primitives::reference::ExchangeId::new(exchange_id).map_err(protocol)?;
        if catalog.exchanges.contains_key(&exchange_id) {
            upsert_v3_venue(tx, catalog, &exchange_id).await?;
        } else {
            sqlx::query("DELETE FROM reference_venues_current WHERE venue_id = ?")
                .bind(venue_id_from_exchange(&exchange_id)?.as_str())
                .execute(&mut **tx)
                .await?;
        }
    }
    Ok(())
}

async fn upsert_v3_venue(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    catalog: &ReferenceCatalog,
    exchange_id: &kairos_primitives::reference::ExchangeId,
) -> sqlx::Result<()> {
    let Some(exchange) = catalog.exchanges.get(exchange_id) else {
        return Ok(());
    };
    let mut roles = BTreeSet::new();
    if catalog
        .listings
        .values()
        .any(|listing| &listing.exchange_id == exchange_id)
    {
        roles.insert(ContractVenueRole::Listing);
    }
    if catalog
        .markets
        .values()
        .any(|market| &market.exchange_id == exchange_id)
    {
        roles.insert(ContractVenueRole::Execution);
    }
    // An unreferenced legacy Exchange cannot satisfy the v3 non-empty-role
    // invariant. Keep it only in the explicit v2 compatibility records.
    if roles.is_empty() {
        sqlx::query("DELETE FROM reference_venues_current WHERE venue_id = ?")
            .bind(venue_id_from_exchange(exchange_id)?.as_str())
            .execute(&mut **tx)
            .await?;
        return Ok(());
    }
    let venue = ContractVenue {
        venue_id: venue_id_from_exchange(exchange_id)?,
        name: exchange.name.clone(),
        venue_kind: ContractVenueKind::RegulatedExchange,
        roles,
        mic: None,
        operating_mic: None,
        parent_venue_id: None,
        jurisdiction: None,
        status: exchange.status,
    };
    let payload = serde_json::to_string(&venue).map_err(protocol)?;
    sqlx::query("INSERT INTO reference_venues_current(venue_id,venue_kind,mic,operating_mic,parent_venue_id,jurisdiction,status,payload) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(venue_id) DO UPDATE SET venue_kind=excluded.venue_kind,mic=excluded.mic,operating_mic=excluded.operating_mic,parent_venue_id=excluded.parent_venue_id,jurisdiction=excluded.jurisdiction,status=excluded.status,payload=excluded.payload")
        .bind(venue.venue_id.as_str())
        .bind(venue.venue_kind.as_str())
        .bind(Option::<String>::None)
        .bind(Option::<String>::None)
        .bind(Option::<String>::None)
        .bind(Option::<String>::None)
        .bind(venue.status.as_str())
        .bind(payload)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn upsert_v3_listing(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    listing: &Listing,
) -> sqlx::Result<()> {
    let listing = ContractVenueListing {
        listing_id: listing.listing_id.clone(),
        instrument_id: listing.instrument_id.clone(),
        listing_venue_id: venue_id_from_exchange(&listing.exchange_id)?,
        market_segment_id: None,
        listing_symbol: listing.exchange_symbol.clone(),
        listing_role: ContractListingRole::Unknown,
        status: listing.status,
        effective_from_unix_nanos: listing.effective_from_unix_nanos,
        effective_to_unix_nanos: listing.effective_to_unix_nanos,
    };
    let payload = serde_json::to_string(&listing).map_err(protocol)?;
    sqlx::query("INSERT INTO reference_venue_listings_current(listing_id,instrument_id,listing_venue_id,market_segment_id,listing_symbol,listing_role,status,effective_to_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(listing_id) DO UPDATE SET instrument_id=excluded.instrument_id,listing_venue_id=excluded.listing_venue_id,market_segment_id=excluded.market_segment_id,listing_symbol=excluded.listing_symbol,listing_role=excluded.listing_role,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload")
        .bind(listing.listing_id.as_str())
        .bind(listing.instrument_id.as_str())
        .bind(listing.listing_venue_id.as_str())
        .bind(Option::<String>::None)
        .bind(listing.listing_symbol.as_str())
        .bind(listing.listing_role.as_str())
        .bind(listing.status.as_str())
        .bind(listing.effective_to_unix_nanos.map(|value| value.get() as i64))
        .bind(payload)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn upsert_v3_market(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    market: &Market,
) -> sqlx::Result<()> {
    let market = ContractVenueMarket {
        market_id: market.market_id.clone(),
        instrument_id: market.instrument_id.clone(),
        execution_venue_id: venue_id_from_exchange(&market.exchange_id)?,
        origin_listing_id: market.listing_id.clone(),
        market_segment_id: None,
        venue_symbol: market.venue_symbol.clone(),
        trading_calendar_id: None,
        trading_session_ids: Vec::new(),
        base_asset_id: market.base_asset_id.clone(),
        quote_asset_id: market.quote_asset_id.clone(),
        status: market.status,
        trading_rules: ContractTradingRules {
            price_tick: market.price_tick,
            quantity_tick: market.quantity_tick,
            price_precision: market.price_precision,
            quantity_precision: market.quantity_precision,
            minimum_quantity: market.minimum_quantity,
            minimum_notional: market.minimum_notional,
            contract_size: market.contract_size,
        },
        effective_from_unix_nanos: market.effective_from_unix_nanos,
        effective_to_unix_nanos: market.effective_to_unix_nanos,
    };
    let payload = serde_json::to_string(&market).map_err(protocol)?;
    sqlx::query("INSERT INTO reference_venue_markets_current(market_id,instrument_id,execution_venue_id,origin_listing_id,market_segment_id,venue_symbol,status,effective_to_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(market_id) DO UPDATE SET instrument_id=excluded.instrument_id,execution_venue_id=excluded.execution_venue_id,origin_listing_id=excluded.origin_listing_id,market_segment_id=excluded.market_segment_id,venue_symbol=excluded.venue_symbol,status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload")
        .bind(market.market_id.as_str())
        .bind(market.instrument_id.as_str())
        .bind(market.execution_venue_id.as_str())
        .bind(market.origin_listing_id.as_ref().map(|value| value.as_str()))
        .bind(Option::<String>::None)
        .bind(market.venue_symbol.as_ref().map(|value| value.as_str()))
        .bind(market.status.as_str())
        .bind(market.effective_to_unix_nanos.map(|value| value.get() as i64))
        .bind(payload)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

fn venue_id_from_exchange(
    exchange_id: &kairos_primitives::reference::ExchangeId,
) -> sqlx::Result<VenueId> {
    let key = exchange_id
        .as_str()
        .strip_prefix("exchange:")
        .unwrap_or_else(|| exchange_id.as_str());
    VenueId::new(format!("venue:{key}")).map_err(protocol)
}

fn protocol(error: impl std::fmt::Display) -> sqlx::Error {
    sqlx::Error::Protocol(error.to_string())
}

async fn upsert_exchange(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    exchange: &Exchange,
) -> sqlx::Result<()> {
    sqlx::query("INSERT INTO reference_exchanges_current(exchange_id,status,payload) VALUES (?,?,?) ON CONFLICT(exchange_id) DO UPDATE SET status=excluded.status,payload=excluded.payload WHERE reference_exchanges_current.payload<>excluded.payload")
        .bind(exchange.exchange_id.as_str())
        .bind(exchange.status.as_str())
        .bind(serde_json::to_string(exchange).map_err(|error| sqlx::Error::Protocol(error.to_string()))?)
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
    use super::{
        AffectedReferenceSetSummary, CatalogReconcileSummary, CatalogSaveOutcome, CatalogWriteMode,
    };
    use crate::domain::{AffectedReferenceSet, LifecycleEvent};

    #[tokio::test]
    async fn staged_affected_keys_deduplicate_and_reset_without_losing_unknown_events() {
        let pool = sqlx::sqlite::SqlitePoolOptions::new()
            .max_connections(1)
            .connect("sqlite::memory:")
            .await
            .unwrap();
        let mut tx = pool.begin().await.unwrap();
        let events = (0..1025).flat_map(|index| {
            let event = LifecycleEvent {
                record_kind: Some("asset".into()),
                record_id: Some(format!("asset:{index:04}")),
                ..Default::default()
            };
            [event.clone(), event]
        });
        let summary = super::stage_affected_keys(&mut tx, events).await.unwrap();
        assert_eq!(summary.total_count, 1025);
        assert_eq!(summary.asset_count, 1025);
        assert!(!summary.requires_full_replace);

        let summary = super::stage_affected_keys(
            &mut tx,
            std::iter::once(LifecycleEvent {
                record_kind: Some("venue".into()),
                record_id: Some("venue:unknown".into()),
                ..Default::default()
            }),
        )
        .await
        .unwrap();
        assert_eq!(summary.total_count, 0);
        assert!(summary.requires_full_replace);
        let summary = super::stage_affected_keys(&mut tx, std::iter::empty())
            .await
            .unwrap();
        assert_eq!(summary.total_count, 0);
        assert!(!summary.requires_full_replace);
        tx.rollback().await.unwrap();
    }

    #[tokio::test]
    async fn staged_current_updates_cross_page_boundaries_and_delete_missing_rows() {
        let directory = tempfile::tempdir().unwrap();
        let store = super::SqlxCatalogStore::open(directory.path().join("reference.sqlite"))
            .await
            .unwrap();
        let mut catalog = super::ReferenceCatalog::default();
        for index in 0..1025 {
            let id = format!("asset:{index:04}");
            catalog.assets.insert(
                id.clone(),
                super::Asset {
                    asset_id: kairos_primitives::reference::AssetId::new(id).unwrap(),
                    code: kairos_primitives::reference::Symbol::new(format!("A{index}")).unwrap(),
                    ..Default::default()
                },
            );
        }
        let mut tx = store.pool.begin().await.unwrap();
        super::stage_affected_keys(
            &mut tx,
            catalog.assets.keys().map(|id| LifecycleEvent {
                record_kind: Some("asset".into()),
                record_id: Some(id.clone()),
                ..Default::default()
            }),
        )
        .await
        .unwrap();
        super::update_staged_current_state(&mut tx, &catalog)
            .await
            .unwrap();
        let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM reference_assets_current")
            .fetch_one(&mut *tx)
            .await
            .unwrap();
        assert_eq!(count, 1025);
        for index in [0, 511, 512, 1024] {
            catalog.assets.remove(&format!("asset:{index:04}"));
        }
        super::update_staged_current_state(&mut tx, &catalog)
            .await
            .unwrap();
        let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM reference_assets_current")
            .fetch_one(&mut *tx)
            .await
            .unwrap();
        assert_eq!(count, 1021);
        tx.rollback().await.unwrap();
        let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM reference_assets_current")
            .fetch_one(&store.pool)
            .await
            .unwrap();
        assert_eq!(count, 0);
    }

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
                exchange_count: 1,
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
