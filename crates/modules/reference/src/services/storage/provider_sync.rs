use sqlx::{Row, Sqlite, SqlitePool};

use crate::domain::{
    Asset, Exchange, Instrument, Listing, Market, ProviderCatalog, ReferenceError, ReferenceResult,
};
use crate::services::time::unix_nanos;

pub(crate) const PROVIDER_SCAN_FORMAT_VERSION: i64 = 7;

pub(crate) type ProviderRecord = (&'static str, String, String);

pub(crate) fn provider_records(catalog: &ProviderCatalog) -> ReferenceResult<Vec<ProviderRecord>> {
    let mut records = Vec::with_capacity(
        catalog.exchanges.len()
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
        sqlx::query("DELETE FROM reference_provider_pending_promotion WHERE provider = ?")
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

pub(crate) async fn load_provider_candidate(
    pool: &SqlitePool,
    overlay: &ProviderCatalog,
) -> sqlx::Result<ProviderCatalog> {
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
    .fetch_all(pool)
    .await?;
    let mut catalogs = std::collections::BTreeMap::<String, ProviderCatalog>::new();
    for row in rows {
        let provider = row.try_get::<String, _>("provider")?;
        let kind = row.try_get::<String, _>("record_kind")?;
        let payload = row.try_get::<String, _>("payload")?;
        push_provider_record(catalogs.entry(provider).or_default(), &kind, payload)
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
    }
    let mut inputs = catalogs.values().collect::<Vec<_>>();
    inputs.push(overlay);
    ProviderCatalog::merge(inputs).map_err(|error| sqlx::Error::Protocol(error.to_string()))
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
    if normalized {
        sqlx::query("INSERT INTO reference_provider_pending_promotion(provider,operation) VALUES (?,'promote') ON CONFLICT(provider) DO UPDATE SET operation='promote'")
            .bind(provider)
            .execute(&mut *tx)
            .await?;
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
        push_provider_record(
            pages.last_mut().expect("page created for row"),
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

pub(crate) async fn promote_staged(pool: &SqlitePool, provider: &str) -> sqlx::Result<u64> {
    let mut tx = pool.begin().await?;
    let changed_count = staged_change_count_tx(&mut tx, provider).await?;
    sqlx::query("INSERT INTO reference_provider_pending_promotion(provider,operation) VALUES (?,'promote') ON CONFLICT(provider) DO UPDATE SET operation='promote'")
        .bind(provider)
        .execute(&mut *tx)
        .await?;
    sqlx::query("INSERT INTO reference_provider_sync(provider,cursor,updated_at_unix_nanos) VALUES (?,NULL,?) ON CONFLICT(provider) DO UPDATE SET cursor=NULL,updated_at_unix_nanos=excluded.updated_at_unix_nanos")
        .bind(provider)
        .bind(unix_nanos().get() as i64)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(changed_count)
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

pub(crate) async fn remove_last_good(pool: &SqlitePool, provider: &str) -> sqlx::Result<()> {
    let mut tx = pool.begin().await?;
    sqlx::query("INSERT INTO reference_provider_pending_promotion(provider,operation) VALUES (?,'delete') ON CONFLICT(provider) DO UPDATE SET operation='delete'")
        .bind(provider)
        .execute(&mut *tx)
        .await?;
    tx.commit().await
}

pub(crate) async fn commit_pending_provider_promotions(
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

pub(crate) async fn reset_provider_scan_tx(
    tx: &mut sqlx::Transaction<'_, Sqlite>,
    provider: &str,
) -> sqlx::Result<()> {
    sqlx::query("DELETE FROM reference_provider_staging WHERE provider = ?")
        .bind(provider)
        .execute(&mut **tx)
        .await?;
    sqlx::query("DELETE FROM reference_provider_pending_promotion WHERE provider = ?")
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

pub(crate) async fn source_desired_states(
    pool: &SqlitePool,
) -> sqlx::Result<Vec<(String, String)>> {
    sqlx::query_as::<_, (String, String)>(
        "SELECT provider, desired_state FROM reference_provider_control ORDER BY provider",
    )
    .fetch_all(pool)
    .await
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
            credential_binding,
            sync_policy,
            payload,
            updated_at_unix_nanos
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            provider_id = excluded.provider_id,
            scope_kind = excluded.scope_kind,
            scope_id = excluded.scope_id,
            desired_state = excluded.desired_state,
            credential_binding = excluded.credential_binding,
            sync_policy = excluded.sync_policy,
            payload = excluded.payload,
            updated_at_unix_nanos = excluded.updated_at_unix_nanos",
    )
    .bind(definition.source_id.as_str())
    .bind(definition.provider_id.as_str())
    .bind(definition.scope.kind.as_str())
    .bind(&definition.scope.id)
    .bind(definition.desired_state.as_str())
    .bind(definition.credential_binding.as_deref())
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
    tx.commit().await
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
    Ok(())
}

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
