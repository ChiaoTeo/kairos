use futures_util::TryStreamExt;
use kairos_primitives::reference::{InstrumentId, ReferenceCoverageId, ReferenceSourceId};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use kairos_reference_contract::{
    CoverageCompleteness, CoverageState, ProviderCatalogMembership, ReferenceCoverage,
    ReferenceCoverageScope,
};
use sqlx::{Row, Sqlite, SqlitePool};

use crate::domain::{
    Asset, Exchange, Instrument, Listing, Market, ProviderCatalog, ReferenceError, ReferenceResult,
    ReferenceSourceDefinition, SourceScopeKind, SourceSyncPolicy, Venue, VenueIdentifierMapping,
    VenueListing, VenueMarket,
};
use crate::services::providers::ReferenceSourceBinding;
use crate::services::sources::SourceChanges;
use crate::services::time::unix_nanos;

pub(crate) const PROVIDER_SCAN_FORMAT_VERSION: i64 = 8;

fn source_selection_json(changes: &SourceChanges) -> sqlx::Result<String> {
    if !changes.completed_scans.is_disjoint(&changes.removed_scans) {
        return Err(sqlx::Error::Protocol(
            "a source scan cannot be completed and removed in one commit".into(),
        ));
    }
    serde_json::to_string(changes).map_err(|error| sqlx::Error::Protocol(error.to_string()))
}

pub(crate) type ProviderRecord = (&'static str, String, String);

pub(crate) fn provider_records(catalog: &ProviderCatalog) -> ReferenceResult<Vec<ProviderRecord>> {
    let mut records = Vec::with_capacity(
        catalog.exchanges.len()
            + catalog.assets.len()
            + catalog.instruments.len()
            + catalog.listings.len()
            + catalog.markets.len()
            + catalog.venues.len()
            + catalog.venue_listings.len()
            + catalog.venue_markets.len()
            + catalog.venue_identifier_mappings.len(),
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
    push_records!("exchange", &catalog.exchanges, |value: &Exchange| value
        .exchange_id
        .to_string());
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
    push_records!("venue", &catalog.venues, |value: &Venue| value
        .venue_id
        .to_string());
    push_records!(
        "venue_listing",
        &catalog.venue_listings,
        |value: &VenueListing| { value.listing_id.to_string() }
    );
    push_records!(
        "venue_market",
        &catalog.venue_markets,
        |value: &VenueMarket| { value.market_id.to_string() }
    );
    push_records!(
        "venue_identifier_mapping",
        &catalog.venue_identifier_mappings,
        |value: &VenueIdentifierMapping| format!(
            "{}|{}|{}|{}",
            value.provider,
            value.provider_product,
            value.identifier_kind.as_str(),
            value.identifier
        )
    );
    Ok(records)
}

pub(crate) fn push_provider_record(
    catalog: &mut ProviderCatalog,
    kind: &str,
    payload: String,
) -> ReferenceResult<()> {
    match kind {
        "exchange" => catalog.exchanges.push(decode(payload)?),
        "asset" => catalog.assets.push(decode(payload)?),
        "instrument" => catalog.instruments.push(decode(payload)?),
        "listing" => catalog.listings.push(decode(payload)?),
        "market" => catalog.markets.push(decode(payload)?),
        "venue" => catalog.venues.push(decode(payload)?),
        "venue_listing" => catalog.venue_listings.push(decode(payload)?),
        "venue_market" => catalog.venue_markets.push(decode(payload)?),
        "venue_identifier_mapping" => catalog.venue_identifier_mappings.push(decode(payload)?),
        other => return Err(persistence(format!("unknown provider record kind {other}"))),
    }
    Ok(())
}

/// Prepare an incremental provider scan for the current canonical catalog.
/// A version change discards only unfinished normalized pages and their cursor;
/// committed records remain authoritative until the new scan is complete and
/// atomically promoted.
pub(crate) async fn prepare_scan(pool: &SqlitePool, provider: &str) -> sqlx::Result<bool> {
    let mut tx = pool.begin().await?;
    let previous = sqlx::query_scalar::<_, i64>(
        "SELECT version FROM reference_provider_scan_format WHERE provider = ?",
    )
    .bind(provider)
    .fetch_optional(&mut *tx)
    .await?;
    let reset = previous != Some(PROVIDER_SCAN_FORMAT_VERSION);
    if reset {
        sqlx::query("DELETE FROM reference_provider_staging WHERE provider = ?")
            .bind(provider)
            .execute(&mut *tx)
            .await?;
        sqlx::query("UPDATE reference_provider_sync SET cursor = NULL, updated_at_unix_nanos = ? WHERE provider = ?")
            .bind(unix_nanos().get() as i64)
            .bind(provider)
            .execute(&mut *tx)
            .await?;
        sqlx::query("INSERT INTO reference_provider_scan_format(provider,version) VALUES (?,?) ON CONFLICT(provider) DO UPDATE SET version=excluded.version")
            .bind(provider)
            .bind(PROVIDER_SCAN_FORMAT_VERSION)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(reset)
}

pub(crate) async fn load_state(
    pool: &SqlitePool,
    provider: &str,
) -> sqlx::Result<Option<(Option<String>, Option<ProviderCatalog>)>> {
    let row = sqlx::query("SELECT cursor FROM reference_provider_sync WHERE provider = ?")
        .bind(provider)
        .fetch_optional(pool)
        .await?;
    row.map(|row| {
        let cursor = row.try_get::<Option<String>, _>("cursor")?;
        Ok((cursor, None))
    })
    .transpose()
}

pub(crate) struct ProviderCandidateSelection {
    pub catalog: ProviderCatalog,
    pub accepted: SourceChanges,
    pub rejected: Vec<(Vec<ReferenceSourceId>, ReferenceError)>,
}

struct CandidateConflict {
    scans: Vec<ReferenceSourceId>,
    error: ReferenceError,
}

pub(crate) async fn select_provider_candidate(
    pool: &SqlitePool,
    overlay: &ProviderCatalog,
    source_changes: &SourceChanges,
) -> sqlx::Result<ProviderCandidateSelection> {
    let mut accepted = source_changes.clone();
    let mut rejected = Vec::new();
    loop {
        match read_provider_candidate(pool, overlay, &accepted).await? {
            Ok(catalog) => {
                return Ok(ProviderCandidateSelection {
                    catalog,
                    accepted,
                    rejected,
                });
            },
            Err(conflicts) => {
                for conflict in conflicts {
                    for scan in &conflict.scans {
                        accepted.completed_scans.remove(scan);
                    }
                    rejected.push((conflict.scans, conflict.error));
                }
            },
        }
    }
}

#[cfg(test)]
pub(crate) async fn load_provider_candidate(
    pool: &SqlitePool,
    overlay: &ProviderCatalog,
    source_changes: &SourceChanges,
) -> sqlx::Result<ProviderCatalog> {
    read_provider_candidate(pool, overlay, source_changes)
        .await?
        .map_err(|conflicts| {
            sqlx::Error::Protocol(
                conflicts
                    .into_iter()
                    .next()
                    .expect("nonempty conflicts")
                    .error
                    .to_string(),
            )
        })
}

async fn read_provider_candidate(
    pool: &SqlitePool,
    overlay: &ProviderCatalog,
    source_changes: &SourceChanges,
) -> sqlx::Result<Result<ProviderCatalog, Vec<CandidateConflict>>> {
    let mut tx = pool.begin().await?;
    let mut rows = sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')), effective AS ( \
           SELECT r.provider,r.record_kind,r.record_id,r.payload \
           FROM reference_provider_records r \
           WHERE NOT EXISTS (SELECT 1 FROM selected p WHERE p.provider=r.provider) \
           UNION ALL \
           SELECT s.provider,s.record_kind,s.record_id,s.payload \
           FROM reference_provider_staging s \
           JOIN selected p ON p.provider=s.provider AND p.operation='promote' \
           WHERE NOT EXISTS (SELECT 1 FROM reference_provider_staging newer \
             WHERE newer.provider=s.provider AND newer.record_kind=s.record_kind \
               AND newer.record_id=s.record_id AND newer.ordinal>s.ordinal) \
         ) \
         SELECT provider,record_kind,record_id,payload FROM effective \
         ORDER BY record_kind,record_id,provider")
    .bind(source_selection_json(source_changes)?)
    .fetch(&mut *tx);
    let mut candidate = ProviderCatalog::default();
    let mut group = Vec::new();
    let mut group_sources = Vec::new();
    let mut conflicts = Vec::new();
    let mut previous_key = None;
    while let Some(row) = rows.try_next().await? {
        let kind = row.try_get::<String, _>("record_kind")?;
        let key = (kind.clone(), row.try_get::<String, _>("record_id")?);
        if previous_key
            .as_ref()
            .is_some_and(|previous| previous != &key)
        {
            if let Err(error) = append_merged_record_group(&mut candidate, &group) {
                record_candidate_conflict(
                    &mut conflicts,
                    classify_candidate_conflict(error, &group_sources, source_changes)?,
                );
            }
            group.clear();
            group_sources.clear();
        }
        let payload = row.try_get::<String, _>("payload")?;
        let mut assertion = ProviderCatalog::default();
        push_provider_record(&mut assertion, &kind, payload)
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        group.push(assertion);
        group_sources.push(row.try_get::<String, _>("provider")?);
        previous_key = Some(key);
    }
    drop(rows);
    if let Err(error) = append_merged_record_group(&mut candidate, &group) {
        record_candidate_conflict(
            &mut conflicts,
            classify_candidate_conflict(error, &group_sources, source_changes)?,
        );
    }
    if !conflicts.is_empty() {
        tx.commit().await?;
        return Ok(Err(conflicts));
    }
    let catalog = if overlay.venues.is_empty()
        && overlay.exchanges.is_empty()
        && overlay.assets.is_empty()
        && overlay.instruments.is_empty()
        && overlay.listings.is_empty()
        && overlay.markets.is_empty()
        && overlay.venue_listings.is_empty()
        && overlay.venue_markets.is_empty()
        && overlay.provider_catalog_memberships.is_empty()
        && overlay.venue_identifier_mappings.is_empty()
    {
        candidate
    } else {
        ProviderCatalog::merge([&candidate, overlay])
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?
    };
    tx.commit().await?;
    Ok(Ok(catalog))
}

fn record_candidate_conflict(conflicts: &mut Vec<CandidateConflict>, conflict: CandidateConflict) {
    // Inspect every identity before excluding sources, so connected conflicts
    // cannot pick a winner by record traversal order. Retain diagnostics only
    // when they add a rejected scan, bounding evidence by selected scan count.
    if conflict
        .scans
        .iter()
        .any(|scan| !conflicts.iter().any(|known| known.scans.contains(scan)))
    {
        conflicts.push(conflict);
    }
}

fn classify_candidate_conflict(
    error: ReferenceError,
    sources: &[String],
    changes: &SourceChanges,
) -> sqlx::Result<CandidateConflict> {
    if !matches!(error, ReferenceError::CanonicalConflict { .. }) {
        return Err(sqlx::Error::Protocol(error.to_string()));
    }
    let scans = changes
        .completed_scans
        .iter()
        .filter(|scan| sources.iter().any(|source| source == scan.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if scans.is_empty() {
        return Err(sqlx::Error::Protocol(error.to_string()));
    }
    Ok(CandidateConflict { scans, error })
}

fn append_merged_record_group(
    candidate: &mut ProviderCatalog,
    assertions: &[ProviderCatalog],
) -> ReferenceResult<()> {
    let merged = ProviderCatalog::merge(assertions)?;
    macro_rules! append {
        ($($field:ident),+ $(,)?) => {
            $(candidate.$field.extend(merged.$field);)+
        };
    }
    append!(
        venues,
        exchanges,
        assets,
        instruments,
        listings,
        markets,
        venue_listings,
        venue_markets,
        provider_catalog_memberships,
        venue_identifier_mappings
    );
    Ok(())
}

#[cfg(test)]
pub(crate) async fn save_state(
    pool: &SqlitePool,
    provider: &str,
    cursor: Option<String>,
    records: Vec<ProviderRecord>,
) -> sqlx::Result<()> {
    let mut tx = pool.begin().await?;
    if !records.is_empty() {
        sqlx::query("DELETE FROM reference_provider_staging WHERE provider=? AND ordinal=-1")
            .bind(provider)
            .execute(&mut *tx)
            .await?;
        for (kind, id, payload) in records {
            sqlx::query("INSERT INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload) VALUES (?,-1,?,?,?)")
                .bind(provider)
                .bind(kind)
                .bind(id)
                .bind(payload)
                .execute(&mut *tx)
                .await?;
        }
    }
    sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,?,?) ON CONFLICT(provider) DO UPDATE SET cursor=excluded.cursor,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
        .bind(provider)
        .bind(cursor)
        .bind(unix_nanos().get() as i64)
        .execute(&mut *tx)
        .await?;
    tx.commit().await
}

#[cfg(test)]
pub(crate) async fn load_last_good(
    pool: &SqlitePool,
    provider: &str,
) -> sqlx::Result<Option<ProviderCatalog>> {
    let rows = sqlx::query(
        "SELECT record_kind, payload FROM reference_provider_records WHERE provider = ? ORDER BY record_kind, record_id",
    )
    .bind(provider)
    .fetch_all(pool)
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
}

pub(crate) async fn has_last_good(pool: &SqlitePool, provider: &str) -> sqlx::Result<bool> {
    Ok(sqlx::query_scalar::<_, i64>(
        "SELECT EXISTS(
            SELECT 1 FROM reference_provider_records
            WHERE provider=?1 OR substr(provider,1,length(?1)+1)=?1||':'
            UNION ALL
            SELECT 1 FROM reference_coverage_current
            WHERE has_last_known_good=1 AND
                (source_id=?1 OR substr(source_id,1,length(?1)+1)=?1||':')
        )",
    )
    .bind(provider)
    .fetch_one(pool)
    .await?
        != 0)
}

pub(crate) async fn save_last_good(
    pool: &SqlitePool,
    provider: &str,
    records: Vec<ProviderRecord>,
    normalized: bool,
) -> sqlx::Result<()> {
    let mut tx = pool.begin().await?;
    if normalized {
        sqlx::query("DELETE FROM reference_provider_staging WHERE provider = ?")
            .bind(provider)
            .execute(&mut *tx)
            .await?;
    } else {
        sqlx::query("DELETE FROM reference_provider_records WHERE provider = ?")
            .bind(provider)
            .execute(&mut *tx)
            .await?;
    }
    for (kind, id, payload) in records {
        if normalized {
            sqlx::query("INSERT INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload) VALUES (?,0,?,?,?)")
                .bind(provider)
                .bind(kind)
                .bind(id)
                .bind(payload)
                .execute(&mut *tx)
                .await?;
        } else {
            sqlx::query("INSERT INTO reference_provider_records(provider,record_kind,record_id,payload) VALUES (?,?,?,?)")
                .bind(provider)
                .bind(kind)
                .bind(id)
                .bind(payload)
                .execute(&mut *tx)
                .await?;
        }
    }
    sqlx::query("INSERT INTO reference_provider_sync(provider,updated_at_unix_nanos) VALUES (?,?) ON CONFLICT(provider) DO UPDATE SET updated_at_unix_nanos=excluded.updated_at_unix_nanos")
        .bind(provider)
        .bind(unix_nanos().get() as i64)
        .execute(&mut *tx)
        .await?;
    tx.commit().await
}

pub(crate) async fn append_staged_page(
    pool: &SqlitePool,
    provider: &str,
    cursor: Option<String>,
    records: Vec<ProviderRecord>,
) -> sqlx::Result<()> {
    let mut tx = pool.begin().await?;
    let ordinal = sqlx::query_scalar::<_, i64>(
        "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM reference_provider_staging WHERE provider = ?",
    )
    .bind(provider)
    .fetch_one(&mut *tx)
    .await?;
    for (kind, id, payload) in records {
        sqlx::query("INSERT OR REPLACE INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload) VALUES (?,?,?,?,?)")
            .bind(provider)
            .bind(ordinal)
            .bind(kind)
            .bind(id)
            .bind(payload)
            .execute(&mut *tx)
            .await?;
    }
    sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,?,?) ON CONFLICT(provider) DO UPDATE SET cursor=excluded.cursor,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
        .bind(provider)
        .bind(cursor)
        .bind(unix_nanos().get() as i64)
        .execute(&mut *tx)
        .await?;
    tx.commit().await
}

pub(crate) async fn staged_pages(
    pool: &SqlitePool,
    provider: &str,
) -> sqlx::Result<Vec<ProviderCatalog>> {
    let rows = sqlx::query(
        "SELECT ordinal, record_kind, payload FROM reference_provider_staging WHERE provider = ? ORDER BY ordinal, record_kind, record_id",
    )
    .bind(provider)
    .fetch_all(pool)
    .await?;
    let mut pages = Vec::new();
    let mut ordinal = None;
    for row in rows {
        let row_ordinal = row.try_get::<i64, _>("ordinal")?;
        if ordinal != Some(row_ordinal) {
            pages.push(ProviderCatalog::default());
            ordinal = Some(row_ordinal);
        }
        let page = pages
            .last_mut()
            .ok_or_else(|| sqlx::Error::Protocol("provider staging row has no page".to_string()))?;
        push_provider_record(
            page,
            row.try_get::<&str, _>("record_kind")?,
            row.try_get("payload")?,
        )
        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
    }
    Ok(pages)
}

pub(crate) async fn clear_staged_pages(pool: &SqlitePool, provider: &str) -> sqlx::Result<()> {
    let mut tx = pool.begin().await?;
    sqlx::query("DELETE FROM reference_provider_staging WHERE provider = ?")
        .bind(provider)
        .execute(&mut *tx)
        .await?;
    sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,NULL,?) ON CONFLICT(provider) DO UPDATE SET cursor=NULL,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
        .bind(provider)
        .bind(unix_nanos().get() as i64)
        .execute(&mut *tx)
        .await?;
    tx.commit().await
}

pub(crate) async fn staged_change_count(pool: &SqlitePool, provider: &str) -> sqlx::Result<u64> {
    let mut tx = pool.begin().await?;
    let count = staged_change_count_tx(&mut tx, provider).await?;
    tx.commit().await?;
    Ok(count)
}

async fn staged_change_count_tx(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    provider: &str,
) -> sqlx::Result<u64> {
    let count = sqlx::query_scalar::<_, i64>(
        "WITH latest AS ( \
           SELECT s.provider,s.record_kind,s.record_id,s.payload \
           FROM reference_provider_staging s \
           WHERE s.provider = ? \
             AND NOT EXISTS (SELECT 1 FROM reference_provider_staging newer \
               WHERE newer.provider=s.provider AND newer.record_kind=s.record_kind \
                 AND newer.record_id=s.record_id AND newer.ordinal>s.ordinal) \
         ), changed_or_added AS ( \
           SELECT latest.record_kind,latest.record_id \
           FROM latest \
           LEFT JOIN reference_provider_records r \
             ON r.provider=latest.provider \
            AND r.record_kind=latest.record_kind \
            AND r.record_id=latest.record_id \
           WHERE r.payload IS NULL OR r.payload<>latest.payload \
         ), removed AS ( \
           SELECT r.record_kind,r.record_id \
           FROM reference_provider_records r \
           WHERE r.provider = ? \
             AND NOT EXISTS (SELECT 1 FROM latest \
               WHERE latest.record_kind=r.record_kind AND latest.record_id=r.record_id) \
         ) \
         SELECT (SELECT COUNT(*) FROM changed_or_added) + (SELECT COUNT(*) FROM removed)",
    )
    .bind(provider)
    .bind(provider)
    .fetch_one(&mut **tx)
    .await?;
    Ok(count as u64)
}

pub(crate) async fn finalize_source_changes(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    generation: Generation,
    event_sequence: Sequence,
    source_changes: &SourceChanges,
) -> sqlx::Result<()> {
    sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) DELETE FROM reference_provider_records WHERE provider IN (SELECT provider FROM selected)")
    .bind(source_selection_json(source_changes)?)
    .execute(&mut **tx)
    .await?;
    sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) INSERT INTO reference_provider_records(provider,record_kind,record_id,payload) \
         SELECT s.provider,s.record_kind,s.record_id,s.payload FROM reference_provider_staging s \
         JOIN selected p ON p.provider=s.provider AND p.operation='promote' \
         WHERE NOT EXISTS (SELECT 1 FROM reference_provider_staging newer \
           WHERE newer.provider=s.provider AND newer.record_kind=s.record_kind \
             AND newer.record_id=s.record_id AND newer.ordinal>s.ordinal)")
    .bind(source_selection_json(source_changes)?)
    .execute(&mut **tx)
    .await?;
    refresh_provider_catalog_memberships(tx, source_changes).await?;
    let pending_transition_event_count = pending_transition_event_count(tx, source_changes).await?;
    refresh_reference_coverages(
        tx,
        generation,
        event_sequence,
        pending_transition_event_count,
        source_changes,
    )
    .await?;
    apply_pending_coverage_transitions(tx, generation, event_sequence, source_changes).await?;
    sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) DELETE FROM reference_provider_staging WHERE provider IN (SELECT provider FROM selected)")
    .bind(source_selection_json(source_changes)?)
    .execute(&mut **tx)
    .await?;
    sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) DELETE FROM reference_provider_sync WHERE provider IN (SELECT provider FROM selected WHERE operation='delete')")
    .bind(source_selection_json(source_changes)?)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

pub(crate) async fn pending_coverage_state_changes(
    pool: &SqlitePool,
    generation: Generation,
    first_event_sequence: Sequence,
    source_changes: &SourceChanges,
) -> sqlx::Result<Vec<(CoverageState, ReferenceCoverage)>> {
    let rows = sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) SELECT p.provider,r.payload,c.payload AS current_payload
         FROM selected p
         JOIN reference_source_registry r ON r.source_id=p.provider
         LEFT JOIN reference_coverage_current c
           ON c.coverage_id=('coverage:' || p.provider)
         WHERE p.operation='promote'
         ORDER BY p.provider")
    .bind(source_selection_json(source_changes)?)
    .fetch_all(pool)
    .await?;
    let now = unix_nanos();
    let mut result = Vec::new();
    for row in rows {
        let source_id = row.try_get::<String, _>("provider")?;
        let definition = serde_json::from_str::<ReferenceSourceDefinition>(
            row.try_get::<String, _>("payload")?.as_str(),
        )
        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        let Some(binding) = ReferenceSourceBinding::from_source_id(&source_id) else {
            continue;
        };
        let Some(scope) = coverage_scope(&definition, binding)? else {
            continue;
        };
        let previous = row
            .try_get::<Option<String>, _>("current_payload")?
            .map(|payload| serde_json::from_str::<ReferenceCoverage>(&payload))
            .transpose()
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        let previous_state = previous
            .as_ref()
            .map(|coverage| coverage.state)
            .unwrap_or(CoverageState::NotConfigured);
        if previous_state == CoverageState::Usable {
            continue;
        }
        let completeness = match definition.sync_policy {
            SourceSyncPolicy::FullSnapshot
            | SourceSyncPolicy::PagedSnapshot
            | SourceSyncPolicy::ScopedSnapshot => CoverageCompleteness::CompleteForDeclaredScope,
            SourceSyncPolicy::IncrementalDelta | SourceSyncPolicy::ManualCurated => {
                CoverageCompleteness::Partial
            },
        };
        result.push((
            previous_state,
            ReferenceCoverage {
                coverage_id: ReferenceCoverageId::new(format!("coverage:{source_id}"))
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?,
                source_id: ReferenceSourceId::new(&source_id)
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?,
                fact_kinds: binding.fact_kinds(),
                scope,
                completeness,
                state: CoverageState::Usable,
                generation: Some(generation),
                event_sequence: Some(Sequence::new(
                    first_event_sequence.get() + result.len() as u64,
                )),
                last_attempt_unix_nanos: Some(now),
                last_success_unix_nanos: Some(now),
                stale_after_unix_nanos: None,
                has_last_known_good: true,
            },
        ));
    }
    let pending = sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) SELECT p.source_id,p.state,p.has_last_known_good,
                p.last_attempt_unix_nanos,c.payload AS current_payload
         FROM reference_coverage_pending_transition p
         JOIN reference_coverage_current c ON c.source_id=p.source_id
         WHERE NOT EXISTS (
             SELECT 1 FROM selected promotion
             WHERE promotion.provider=p.source_id
         )
         UNION ALL
         SELECT c.source_id,'not_configured',0,?2,c.payload
         FROM reference_coverage_current c JOIN selected s ON s.provider=c.source_id
         WHERE s.operation='delete'
         ORDER BY source_id")
    .bind(source_selection_json(source_changes)?)
    .bind(unix_nanos().get() as i64)
    .fetch_all(pool)
    .await?;
    for row in pending {
        let mut coverage = serde_json::from_str::<ReferenceCoverage>(
            row.try_get::<String, _>("current_payload")?.as_str(),
        )
        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        let previous_state = coverage.state;
        let next_state = coverage_state_from_str(row.try_get::<String, _>("state")?.as_str())?;
        if coverage_conclusion_class(previous_state) == coverage_conclusion_class(next_state) {
            continue;
        }
        coverage.state = next_state;
        if next_state == CoverageState::NotConfigured {
            coverage.completeness = CoverageCompleteness::Unknown;
        }
        coverage.generation = Some(generation);
        coverage.event_sequence = Some(Sequence::new(
            first_event_sequence.get() + result.len() as u64,
        ));
        coverage.last_attempt_unix_nanos = Some(UnixNanos::new(
            row.try_get::<i64, _>("last_attempt_unix_nanos")? as u64,
        ));
        coverage.has_last_known_good = row.try_get::<i64, _>("has_last_known_good")? != 0;
        result.push((previous_state, coverage));
    }
    Ok(result)
}

fn coverage_state_from_str(value: &str) -> sqlx::Result<CoverageState> {
    match value {
        "not_configured" => Ok(CoverageState::NotConfigured),
        "waiting" => Ok(CoverageState::Waiting),
        "scanning" => Ok(CoverageState::Scanning),
        "promoting" => Ok(CoverageState::Promoting),
        "usable" => Ok(CoverageState::Usable),
        "stale" => Ok(CoverageState::Stale),
        "retry_waiting" => Ok(CoverageState::RetryWaiting),
        "paused" => Ok(CoverageState::Paused),
        "unavailable" => Ok(CoverageState::Unavailable),
        other => Err(sqlx::Error::Protocol(format!(
            "unknown Reference coverage state: {other}"
        ))),
    }
}

fn coverage_conclusion_class(state: CoverageState) -> &'static str {
    match state {
        CoverageState::Usable => "usable",
        CoverageState::Stale => "stale",
        CoverageState::Unavailable => "unavailable",
        CoverageState::NotConfigured => "not_configured",
        CoverageState::Paused => "paused",
        CoverageState::Waiting
        | CoverageState::Scanning
        | CoverageState::Promoting
        | CoverageState::RetryWaiting => "preparing",
    }
}

async fn refresh_reference_coverages(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    generation: Generation,
    event_sequence: Sequence,
    later_transition_count: usize,
    source_changes: &SourceChanges,
) -> sqlx::Result<()> {
    let rows = sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) SELECT p.provider,r.payload,c.state AS current_state,
                c.event_sequence AS current_event_sequence
         FROM selected p
         JOIN reference_source_registry r ON r.source_id=p.provider
         LEFT JOIN reference_coverage_current c
           ON c.coverage_id=('coverage:' || p.provider)
         WHERE p.operation='promote'")
    .bind(source_selection_json(source_changes)?)
    .fetch_all(&mut **tx)
    .await?;
    let now = unix_nanos();
    let transition_count = rows
        .iter()
        .filter(|row| {
            row.try_get::<Option<String>, _>("current_state")
                .ok()
                .flatten()
                .as_deref()
                != Some("usable")
        })
        .count();
    let first_coverage_sequence = event_sequence.get().saturating_sub(
        transition_count
            .saturating_add(later_transition_count)
            .saturating_sub(1) as u64,
    );
    let mut transition_offset = 0_u64;
    for row in rows {
        let source_id = row.try_get::<String, _>("provider")?;
        let definition = serde_json::from_str::<ReferenceSourceDefinition>(
            row.try_get::<String, _>("payload")?.as_str(),
        )
        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        let Some(binding) = ReferenceSourceBinding::from_source_id(&source_id) else {
            continue;
        };
        let fact_kinds = binding.fact_kinds();
        let Some(scope) = coverage_scope(&definition, binding)? else {
            continue;
        };
        let completeness = match definition.sync_policy {
            SourceSyncPolicy::FullSnapshot
            | SourceSyncPolicy::PagedSnapshot
            | SourceSyncPolicy::ScopedSnapshot => CoverageCompleteness::CompleteForDeclaredScope,
            SourceSyncPolicy::IncrementalDelta | SourceSyncPolicy::ManualCurated => {
                CoverageCompleteness::Partial
            },
        };
        let current_state = row.try_get::<Option<String>, _>("current_state")?;
        let current_event_sequence = row
            .try_get::<Option<i64>, _>("current_event_sequence")?
            .and_then(|value| u64::try_from(value).ok())
            .map(Sequence::new);
        let coverage_event_sequence = if current_state.as_deref() == Some("usable") {
            current_event_sequence
        } else {
            let sequence = Sequence::new(first_coverage_sequence.saturating_add(transition_offset));
            transition_offset = transition_offset.saturating_add(1);
            Some(sequence)
        };
        let coverage = ReferenceCoverage {
            coverage_id: ReferenceCoverageId::new(format!("coverage:{source_id}"))
                .map_err(|error| sqlx::Error::Protocol(error.to_string()))?,
            source_id: ReferenceSourceId::new(&source_id)
                .map_err(|error| sqlx::Error::Protocol(error.to_string()))?,
            fact_kinds,
            scope,
            completeness,
            state: CoverageState::Usable,
            generation: Some(generation),
            event_sequence: coverage_event_sequence,
            last_attempt_unix_nanos: Some(now),
            last_success_unix_nanos: Some(now),
            stale_after_unix_nanos: None,
            has_last_known_good: true,
        };
        upsert_coverage_tx(tx, &coverage).await?;
    }
    Ok(())
}

async fn pending_transition_event_count(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    source_changes: &SourceChanges,
) -> sqlx::Result<usize> {
    let rows = sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) SELECT p.state,c.payload AS current_payload
         FROM reference_coverage_pending_transition p
         JOIN reference_coverage_current c ON c.source_id=p.source_id
         WHERE NOT EXISTS (
             SELECT 1 FROM selected promotion
             WHERE promotion.provider=p.source_id
         )
         UNION ALL
         SELECT 'not_configured',c.payload
         FROM reference_coverage_current c JOIN selected s ON s.provider=c.source_id
         WHERE s.operation='delete'")
    .bind(source_selection_json(source_changes)?)
    .fetch_all(&mut **tx)
    .await?;
    rows.into_iter()
        .map(|row| {
            let coverage = serde_json::from_str::<ReferenceCoverage>(
                row.try_get::<String, _>("current_payload")?.as_str(),
            )
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
            let next = coverage_state_from_str(row.try_get::<String, _>("state")?.as_str())?;
            Ok(usize::from(
                coverage_conclusion_class(coverage.state) != coverage_conclusion_class(next),
            ))
        })
        .sum()
}

async fn apply_pending_coverage_transitions(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    generation: Generation,
    final_event_sequence: Sequence,
    source_changes: &SourceChanges,
) -> sqlx::Result<()> {
    let rows = sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) SELECT p.source_id,p.state,p.has_last_known_good,p.last_attempt_unix_nanos,
                c.payload AS current_payload
         FROM reference_coverage_pending_transition p
         JOIN reference_coverage_current c ON c.source_id=p.source_id
         WHERE NOT EXISTS (
             SELECT 1 FROM selected promotion
             WHERE promotion.provider=p.source_id
         )
         UNION ALL
         SELECT c.source_id,'not_configured',0,?2,c.payload
         FROM reference_coverage_current c JOIN selected s ON s.provider=c.source_id
         WHERE s.operation='delete'
         ORDER BY source_id")
    .bind(source_selection_json(source_changes)?)
    .bind(unix_nanos().get() as i64)
    .fetch_all(&mut **tx)
    .await?;
    let transition_count = rows
        .iter()
        .map(|row| {
            let coverage = serde_json::from_str::<ReferenceCoverage>(
                row.try_get::<String, _>("current_payload")?.as_str(),
            )
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
            let next = coverage_state_from_str(row.try_get::<String, _>("state")?.as_str())?;
            Ok::<usize, sqlx::Error>(usize::from(
                coverage_conclusion_class(coverage.state) != coverage_conclusion_class(next),
            ))
        })
        .collect::<sqlx::Result<Vec<_>>>()?
        .into_iter()
        .sum::<usize>();
    let first_sequence = final_event_sequence
        .get()
        .saturating_sub(transition_count.saturating_sub(1) as u64);
    let mut offset = 0_u64;
    for row in rows {
        let mut coverage = serde_json::from_str::<ReferenceCoverage>(
            row.try_get::<String, _>("current_payload")?.as_str(),
        )
        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        let next = coverage_state_from_str(row.try_get::<String, _>("state")?.as_str())?;
        let event_worthy =
            coverage_conclusion_class(coverage.state) != coverage_conclusion_class(next);
        coverage.state = next;
        if next == CoverageState::NotConfigured {
            coverage.completeness = CoverageCompleteness::Unknown;
        }
        coverage.last_attempt_unix_nanos = Some(UnixNanos::new(
            row.try_get::<i64, _>("last_attempt_unix_nanos")? as u64,
        ));
        coverage.has_last_known_good = row.try_get::<i64, _>("has_last_known_good")? != 0;
        if event_worthy {
            coverage.generation = Some(generation);
            coverage.event_sequence = Some(Sequence::new(first_sequence.saturating_add(offset)));
            offset = offset.saturating_add(1);
        }
        upsert_coverage_tx(tx, &coverage).await?;
    }
    sqlx::query("DELETE FROM reference_coverage_pending_transition")
        .execute(&mut **tx)
        .await?;
    Ok(())
}

fn coverage_scope(
    definition: &ReferenceSourceDefinition,
    binding: ReferenceSourceBinding,
) -> sqlx::Result<Option<ReferenceCoverageScope>> {
    match definition.scope.kind {
        SourceScopeKind::UnderlyingInstrument | SourceScopeKind::Coverage => {
            let Some(id) = definition.scope.id.as_deref() else {
                return Ok(None);
            };
            Ok(Some(ReferenceCoverageScope::UnderlyingOptions {
                underlying_instrument_ids: vec![
                    InstrumentId::new(id)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?,
                ],
            }))
        },
        SourceScopeKind::Global | SourceScopeKind::ProviderCatalog => {
            Ok(Some(ReferenceCoverageScope::ProviderCatalog {
                binding: binding.to_contract(),
            }))
        },
        SourceScopeKind::Custom => Ok(None),
    }
}

async fn upsert_coverage_tx(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    coverage: &ReferenceCoverage,
) -> sqlx::Result<()> {
    let payload = serde_json::to_string(coverage)
        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
    sqlx::query("INSERT INTO reference_coverage_current(coverage_id,source_id,completeness,state,generation,event_sequence,last_attempt_unix_nanos,last_success_unix_nanos,stale_after_unix_nanos,has_last_known_good,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(coverage_id) DO UPDATE SET source_id=excluded.source_id,completeness=excluded.completeness,state=excluded.state,generation=excluded.generation,event_sequence=excluded.event_sequence,last_attempt_unix_nanos=excluded.last_attempt_unix_nanos,last_success_unix_nanos=excluded.last_success_unix_nanos,stale_after_unix_nanos=excluded.stale_after_unix_nanos,has_last_known_good=excluded.has_last_known_good,payload=excluded.payload")
        .bind(coverage.coverage_id.as_str())
        .bind(coverage.source_id.as_str())
        .bind(match coverage.completeness {
            CoverageCompleteness::Unknown => "unknown",
            CoverageCompleteness::Partial => "partial",
            CoverageCompleteness::CompleteForDeclaredScope => "complete_for_declared_scope",
        })
        .bind(match coverage.state {
            CoverageState::NotConfigured => "not_configured",
            CoverageState::Waiting => "waiting",
            CoverageState::Scanning => "scanning",
            CoverageState::Promoting => "promoting",
            CoverageState::Usable => "usable",
            CoverageState::Stale => "stale",
            CoverageState::RetryWaiting => "retry_waiting",
            CoverageState::Paused => "paused",
            CoverageState::Unavailable => "unavailable",
        })
        .bind(coverage.generation.map(|value| value.get() as i64))
        .bind(coverage.event_sequence.map(|value| value.get() as i64))
        .bind(coverage.last_attempt_unix_nanos.map(|value| value.get() as i64))
        .bind(coverage.last_success_unix_nanos.map(|value| value.get() as i64))
        .bind(coverage.stale_after_unix_nanos.map(|value| value.get() as i64))
        .bind(i64::from(coverage.has_last_known_good))
        .bind(payload)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn refresh_provider_catalog_memberships(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    source_changes: &SourceChanges,
) -> sqlx::Result<()> {
    let rows = sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) SELECT r.provider,r.record_id,r.payload,
                m.payload AS previous_membership
         FROM reference_provider_records r
         JOIN selected p
           ON p.provider=r.provider AND p.operation='promote'
         LEFT JOIN reference_provider_catalog_memberships_current m
           ON m.source_id=r.provider AND m.instrument_id=r.record_id
         WHERE r.record_kind='instrument'")
    .bind(source_selection_json(source_changes)?)
    .fetch_all(&mut **tx)
    .await?;
    sqlx::query("WITH selected(provider,operation) AS (SELECT value,'promote' FROM json_each(?1,'$.completed_scans') UNION ALL SELECT value,'delete' FROM json_each(?1,'$.removed_scans')) DELETE FROM reference_provider_catalog_memberships_current
         WHERE source_id IN (SELECT provider FROM selected)")
    .bind(source_selection_json(source_changes)?)
    .execute(&mut **tx)
    .await?;
    let now = unix_nanos();
    for row in rows {
        let provider = row.try_get::<String, _>("provider")?;
        let instrument = serde_json::from_str::<Instrument>(row.try_get("payload")?)
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        let previous = row
            .try_get::<Option<String>, _>("previous_membership")?
            .map(|payload| serde_json::from_str::<ProviderCatalogMembership>(&payload))
            .transpose()
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        let membership = ProviderCatalogMembership {
            source_id: ReferenceSourceId::new(&provider)
                .map_err(|error| sqlx::Error::Protocol(error.to_string()))?,
            instrument_id: instrument.instrument_id,
            provider_symbol: Some(instrument.symbol.to_string()),
            provider_product: ReferenceSourceBinding::from_source_id(&provider)
                .map(|binding| binding.product().to_owned()),
            status: instrument.status,
            effective_from_unix_nanos: previous
                .map(|value| value.effective_from_unix_nanos)
                .unwrap_or(now),
            effective_to_unix_nanos: None,
        };
        let payload = serde_json::to_string(&membership)
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        sqlx::query(
            "INSERT INTO reference_provider_catalog_memberships_current(
                source_id,instrument_id,provider_symbol,provider_product,status,
                effective_to_unix_nanos,payload
             ) VALUES (?,?,?,?,?,?,?)",
        )
        .bind(membership.source_id.as_str())
        .bind(membership.instrument_id.as_str())
        .bind(&membership.provider_symbol)
        .bind(&membership.provider_product)
        .bind(membership.status.as_str())
        .bind(
            membership
                .effective_to_unix_nanos
                .map(|value: UnixNanos| value.get() as i64),
        )
        .bind(payload)
        .execute(&mut **tx)
        .await?;
    }
    Ok(())
}

pub(crate) async fn reset_provider_scan_tx(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    provider: &str,
) -> sqlx::Result<()> {
    sqlx::query("DELETE FROM reference_provider_staging WHERE provider = ?")
        .bind(provider)
        .execute(&mut **tx)
        .await?;
    sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,NULL,?) ON CONFLICT(provider) DO UPDATE SET cursor=NULL,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
        .bind(provider)
        .bind(unix_nanos().get() as i64)
        .execute(&mut **tx)
        .await?;
    sqlx::query("INSERT INTO reference_provider_scan_format(provider,version) VALUES (?,?) ON CONFLICT(provider) DO UPDATE SET version=excluded.version")
        .bind(provider)
        .bind(PROVIDER_SCAN_FORMAT_VERSION)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

pub(crate) async fn source_definitions(
    pool: &SqlitePool,
) -> sqlx::Result<Vec<crate::domain::ReferenceSourceDefinition>> {
    let payloads = sqlx::query_scalar::<_, String>(
        "SELECT payload FROM reference_source_registry ORDER BY source_id",
    )
    .fetch_all(pool)
    .await?;
    payloads
        .into_iter()
        .map(|payload| {
            serde_json::from_str(&payload).map_err(|error| sqlx::Error::Protocol(error.to_string()))
        })
        .collect()
}

#[cfg(test)]
pub(crate) async fn source_desired_states(
    pool: &SqlitePool,
) -> sqlx::Result<Vec<(String, String)>> {
    sqlx::query_as::<_, (String, String)>(
        "SELECT provider, desired_state FROM reference_provider_control ORDER BY provider",
    )
    .fetch_all(pool)
    .await
}

pub(crate) async fn upsert_source_definition(
    pool: &SqlitePool,
    definition: &crate::domain::ReferenceSourceDefinition,
) -> sqlx::Result<()> {
    let payload = serde_json::to_string(definition)
        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
    let paused = i64::from(definition.desired_state == crate::domain::SourceDesiredState::Paused);
    let mut tx = pool.begin().await?;
    sqlx::query(
        "INSERT INTO reference_source_registry(
            source_id,
            provider_id,
            scope_kind,
            scope_id,
            desired_state,
            connection_id,
            sync_policy,
            payload,
            updated_at_unix_nanos
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            provider_id = excluded.provider_id,
            scope_kind = excluded.scope_kind,
            scope_id = excluded.scope_id,
            desired_state = excluded.desired_state,
            connection_id = excluded.connection_id,
            sync_policy = excluded.sync_policy,
            payload = excluded.payload,
            updated_at_unix_nanos = excluded.updated_at_unix_nanos",
    )
    .bind(definition.source_id.as_str())
    .bind(definition.provider_id.as_str())
    .bind(definition.scope.kind.as_str())
    .bind(&definition.scope.id)
    .bind(definition.desired_state.as_str())
    .bind(definition.connection_id.as_deref())
    .bind(definition.sync_policy.as_str())
    .bind(payload)
    .bind(unix_nanos().get() as i64)
    .execute(&mut *tx)
    .await?;
    sqlx::query("INSERT INTO reference_provider_control(provider, paused, desired_state, updated_at_unix_nanos) VALUES (?, ?, ?, ?) ON CONFLICT(provider) DO UPDATE SET paused = excluded.paused, desired_state = excluded.desired_state, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
        .bind(definition.source_id.as_str())
        .bind(paused)
        .bind(definition.desired_state.as_str())
        .bind(unix_nanos().get() as i64)
        .execute(&mut *tx)
        .await?;
    if let Some(binding) = ReferenceSourceBinding::from_source_id(definition.source_id.as_str()) {
        let scope = coverage_scope(definition, binding)?;
        if let Some(scope) = scope {
            let existing = sqlx::query_scalar::<_, String>(
                "SELECT payload FROM reference_coverage_current WHERE coverage_id = ?",
            )
            .bind(format!("coverage:{}", definition.source_id))
            .fetch_optional(&mut *tx)
            .await?
            .map(|payload| serde_json::from_str::<ReferenceCoverage>(&payload))
            .transpose()
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
            let same_scope = existing.as_ref().is_some_and(|value| value.scope == scope);
            let state = match definition.desired_state {
                crate::domain::SourceDesiredState::Enabled if same_scope => existing
                    .as_ref()
                    .map(|value| value.state)
                    .unwrap_or(CoverageState::Waiting),
                crate::domain::SourceDesiredState::Enabled => CoverageState::Waiting,
                crate::domain::SourceDesiredState::Paused => CoverageState::Paused,
                crate::domain::SourceDesiredState::Disabled => CoverageState::Unavailable,
                crate::domain::SourceDesiredState::Removed => CoverageState::NotConfigured,
            };
            let now = unix_nanos();
            let coverage = ReferenceCoverage {
                coverage_id: ReferenceCoverageId::new(format!("coverage:{}", definition.source_id))
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?,
                source_id: definition.source_id.clone(),
                fact_kinds: binding.fact_kinds(),
                scope,
                completeness: if same_scope {
                    existing
                        .as_ref()
                        .map(|value| value.completeness)
                        .unwrap_or_default()
                } else {
                    CoverageCompleteness::Unknown
                },
                state,
                generation: same_scope
                    .then(|| existing.as_ref().and_then(|value| value.generation))
                    .flatten(),
                event_sequence: same_scope
                    .then(|| existing.as_ref().and_then(|value| value.event_sequence))
                    .flatten(),
                last_attempt_unix_nanos: Some(now),
                last_success_unix_nanos: same_scope
                    .then(|| {
                        existing
                            .as_ref()
                            .and_then(|value| value.last_success_unix_nanos)
                    })
                    .flatten(),
                stale_after_unix_nanos: same_scope
                    .then(|| {
                        existing
                            .as_ref()
                            .and_then(|value| value.stale_after_unix_nanos)
                    })
                    .flatten(),
                has_last_known_good: same_scope
                    && existing
                        .as_ref()
                        .is_some_and(|value| value.has_last_known_good),
            };
            upsert_coverage_tx(&mut tx, &coverage).await?;
        }
    }
    tx.commit().await
}

pub(crate) async fn set_source_failure(
    pool: &SqlitePool,
    source_id: &str,
    has_last_known_good: bool,
) -> sqlx::Result<()> {
    let state = if has_last_known_good {
        "stale"
    } else {
        "unavailable"
    };
    let now = unix_nanos().get() as i64;
    sqlx::query(
        "INSERT INTO reference_coverage_pending_transition(
             source_id,state,has_last_known_good,last_attempt_unix_nanos
         ) VALUES (?,?,?,?)
         ON CONFLICT(source_id) DO UPDATE SET state=excluded.state,
             has_last_known_good=excluded.has_last_known_good,
             last_attempt_unix_nanos=excluded.last_attempt_unix_nanos",
    )
    .bind(source_id)
    .bind(state)
    .bind(i64::from(has_last_known_good))
    .bind(now)
    .execute(pool)
    .await?;
    Ok(())
}

pub(crate) async fn source_scan_ids(
    pool: &SqlitePool,
    source_id: &str,
) -> sqlx::Result<Vec<ReferenceSourceId>> {
    let ids = sqlx::query_scalar::<_, String>(
        "WITH scans AS (
            SELECT provider FROM reference_provider_records
            UNION SELECT provider FROM reference_provider_staging
            UNION SELECT provider FROM reference_provider_sync
            UNION SELECT ?1
         ) SELECT provider FROM scans
         WHERE provider=?1 OR substr(provider,1,length(?1)+1)=?1||':'
         ORDER BY provider",
    )
    .bind(source_id)
    .fetch_all(pool)
    .await?;
    ids.into_iter()
        .map(|id| {
            ReferenceSourceId::new(id).map_err(|error| sqlx::Error::Protocol(error.to_string()))
        })
        .collect()
}

pub(crate) async fn set_source_desired_state(
    pool: &SqlitePool,
    provider: &str,
    desired_state: &str,
) -> sqlx::Result<()> {
    let paused = i64::from(desired_state == "paused");
    sqlx::query("INSERT INTO reference_provider_control(provider, paused, desired_state, updated_at_unix_nanos) VALUES (?, ?, ?, ?) ON CONFLICT(provider) DO UPDATE SET paused = excluded.paused, desired_state = excluded.desired_state, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
        .bind(provider)
        .bind(paused)
        .bind(desired_state)
    .bind(unix_nanos().get() as i64)
    .execute(pool)
    .await?;
    let coverage_state = match desired_state {
        "enabled" => "waiting",
        "paused" => "paused",
        "disabled" => "unavailable",
        "removed" => "not_configured",
        _ => "unavailable",
    };
    let has_last_known_good = sqlx::query_scalar::<_, i64>(
        "SELECT COALESCE(MAX(has_last_known_good), 0)
         FROM reference_coverage_current WHERE source_id = ?",
    )
    .bind(provider)
    .fetch_one(pool)
    .await?;
    sqlx::query(
        "INSERT INTO reference_coverage_pending_transition(
             source_id,state,has_last_known_good,last_attempt_unix_nanos
         ) VALUES (?,?,?,?)
         ON CONFLICT(source_id) DO UPDATE SET state=excluded.state,
             has_last_known_good=excluded.has_last_known_good,
             last_attempt_unix_nanos=excluded.last_attempt_unix_nanos",
    )
    .bind(provider)
    .bind(coverage_state)
    .bind(if desired_state == "removed" {
        0
    } else {
        has_last_known_good
    })
    .bind(unix_nanos().get() as i64)
    .execute(pool)
    .await?;
    Ok(())
}

#[cfg(test)]
pub(crate) async fn option_underlyings(
    pool: &SqlitePool,
    provider: &str,
) -> sqlx::Result<Vec<String>> {
    sqlx::query_scalar::<_, String>(
        "SELECT underlying FROM reference_option_coverage WHERE provider = ? AND enabled = 1 ORDER BY underlying",
    )
    .bind(provider)
    .fetch_all(pool)
    .await
}

pub(crate) async fn set_option_underlying(
    pool: &SqlitePool,
    provider: &str,
    underlying: &str,
    enabled: bool,
) -> sqlx::Result<()> {
    sqlx::query("INSERT INTO reference_option_coverage(provider, underlying, enabled, updated_at_unix_nanos) VALUES (?, ?, ?, ?) ON CONFLICT(provider, underlying) DO UPDATE SET enabled = excluded.enabled, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
        .bind(provider)
        .bind(underlying)
        .bind(i64::from(enabled))
        .bind(unix_nanos().get() as i64)
        .execute(pool)
        .await?;
    Ok(())
}

fn decode<T: serde::de::DeserializeOwned>(payload: String) -> ReferenceResult<T> {
    serde_json::from_str(&payload).map_err(persistence)
}

fn persistence(error: impl std::fmt::Display) -> ReferenceError {
    ReferenceError::Persistence(error.to_string())
}
