use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::sync::OnceLock;

use kairos_primitives::reference::{ReferenceStatus, VenueId};
use kairos_reference_contract::{
    Exchange, Listing, ListingRole, Market, TradingRules, Venue, VenueKind, VenueListing,
    VenueMarket, VenueRole,
};
use sqlx::SqlitePool;
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};

use crate::domain::ReferenceError;

static SQLITE_OPERATION_LOCK: OnceLock<tokio::sync::Mutex<()>> = OnceLock::new();

pub(crate) fn persistence(error: impl std::fmt::Display) -> ReferenceError {
    ReferenceError::Persistence(error.to_string())
}

pub(crate) fn operation_lock() -> &'static tokio::sync::Mutex<()> {
    SQLITE_OPERATION_LOCK.get_or_init(|| tokio::sync::Mutex::new(()))
}

pub(crate) async fn open_pool(path: &Path) -> sqlx::Result<SqlitePool> {
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
        let expected = i64::from(kairos_reference_contract::REFERENCE_SQLITE_SCHEMA_VERSION);
        if version == 6 && expected == 10 {
            migrate_v6_to_v7(&pool).await?;
            migrate_v8_to_v9(&pool).await?;
        } else if version == 7 && expected == 10 {
            migrate_v7_to_v8(&pool).await?;
            migrate_v8_to_v9(&pool).await?;
        } else if version == 8 && expected == 10 {
            migrate_v8_to_v9(&pool).await?;
        } else if version != 9 && version != expected {
            return Err(sqlx::Error::Protocol(format!(
                "unsupported Reference SQLite schema version {version}; expected {expected}"
            )));
        }
        if version != expected {
            migrate_v9_to_v10(&pool).await?;
        }
    }
    sqlx::raw_sql(include_str!("../../../schema.sql"))
        .execute(&pool)
        .await?;
    ensure_provider_control_desired_state(&pool).await?;
    Ok(pool)
}

async fn migrate_v9_to_v10(pool: &SqlitePool) -> sqlx::Result<()> {
    let mut tx = pool.begin().await?;
    // An old ready marker is not a completed workflow in the new process.
    // Preserve committed data and discard only the obsolete coordination table.
    sqlx::query("DROP TABLE IF EXISTS reference_provider_pending_promotion")
        .execute(&mut *tx)
        .await?;
    sqlx::query("UPDATE reference_meta SET schema_version=10 WHERE id=1")
        .execute(&mut *tx)
        .await?;
    tx.commit().await
}

async fn migrate_v8_to_v9(pool: &SqlitePool) -> sqlx::Result<()> {
    let mut tx = pool.begin().await?;
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS reference_coverage_pending_transition (
            source_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK(state IN (
                'not_configured', 'waiting', 'scanning', 'promoting', 'usable',
                'stale', 'retry_waiting', 'paused', 'unavailable'
            )),
            has_last_known_good INTEGER NOT NULL CHECK(has_last_known_good IN (0, 1)),
            last_attempt_unix_nanos INTEGER NOT NULL
        ) WITHOUT ROWID",
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query("UPDATE reference_meta SET schema_version = 9 WHERE id = 1")
        .execute(&mut *tx)
        .await?;
    tx.commit().await
}

async fn migrate_v7_to_v8(pool: &SqlitePool) -> sqlx::Result<()> {
    let mut tx = pool.begin().await?;
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS reference_venue_identifier_mappings_current (
            mapping_key TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_product TEXT NOT NULL,
            identifier_kind TEXT NOT NULL,
            identifier TEXT NOT NULL,
            venue_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE(provider, provider_product, identifier_kind, identifier)
        )",
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query(
        "CREATE INDEX IF NOT EXISTS reference_venue_identifier_mappings_venue_idx
         ON reference_venue_identifier_mappings_current(venue_id, status, mapping_key)",
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query("UPDATE reference_meta SET schema_version = 8 WHERE id = 1")
        .execute(&mut *tx)
        .await?;
    tx.commit().await
}

async fn migrate_v6_to_v7(pool: &SqlitePool) -> sqlx::Result<()> {
    // The schema DDL is idempotent. Keep the metadata at v6 until all v3
    // compatibility records have been materialized successfully.
    sqlx::raw_sql(include_str!("../../../schema.sql"))
        .execute(pool)
        .await?;

    let exchanges =
        load_payloads::<Exchange>(pool, "SELECT payload FROM reference_exchanges_current").await?;
    let listings =
        load_payloads::<Listing>(pool, "SELECT payload FROM reference_listings_current").await?;
    let markets =
        load_payloads::<Market>(pool, "SELECT payload FROM reference_markets_current").await?;
    let mut venues = exchanges
        .into_iter()
        .map(|exchange| {
            let venue_id = venue_id_from_exchange(&exchange.exchange_id)?;
            Ok((
                exchange.exchange_id.to_string(),
                Venue {
                    venue_id,
                    name: exchange.name,
                    venue_kind: VenueKind::RegulatedExchange,
                    roles: BTreeSet::new(),
                    mic: None,
                    operating_mic: None,
                    parent_venue_id: None,
                    jurisdiction: None,
                    status: exchange.status,
                },
            ))
        })
        .collect::<sqlx::Result<BTreeMap<_, _>>>()?;

    for listing in &listings {
        ensure_migrated_venue(&mut venues, &listing.exchange_id, VenueRole::Listing)?;
    }
    for market in &markets {
        ensure_migrated_venue(&mut venues, &market.exchange_id, VenueRole::Execution)?;
    }

    let mut tx = pool.begin().await?;
    for venue in venues.values().filter(|venue| !venue.roles.is_empty()) {
        let payload = encode_payload(venue)?;
        sqlx::query(
            "INSERT INTO reference_venues_current(
                venue_id,venue_kind,mic,operating_mic,parent_venue_id,jurisdiction,status,payload
             ) VALUES (?,?,?,?,?,?,?,?)
             ON CONFLICT(venue_id) DO UPDATE SET
                venue_kind=excluded.venue_kind,mic=excluded.mic,
                operating_mic=excluded.operating_mic,parent_venue_id=excluded.parent_venue_id,
                jurisdiction=excluded.jurisdiction,status=excluded.status,payload=excluded.payload",
        )
        .bind(venue.venue_id.as_str())
        .bind(venue.venue_kind.as_str())
        .bind(venue.mic.as_ref().map(|value| value.as_str()))
        .bind(venue.operating_mic.as_ref().map(|value| value.as_str()))
        .bind(venue.parent_venue_id.as_ref().map(|value| value.as_str()))
        .bind(venue.jurisdiction.as_ref().map(|value| value.as_str()))
        .bind(venue.status.as_str())
        .bind(payload)
        .execute(&mut *tx)
        .await?;
    }

    for listing in listings {
        let migrated = VenueListing {
            listing_id: listing.listing_id,
            instrument_id: listing.instrument_id,
            listing_venue_id: venue_id_from_exchange(&listing.exchange_id)?,
            market_segment_id: None,
            listing_symbol: listing.exchange_symbol,
            listing_role: ListingRole::Unknown,
            status: listing.status,
            effective_from_unix_nanos: listing.effective_from_unix_nanos,
            effective_to_unix_nanos: listing.effective_to_unix_nanos,
        };
        let payload = encode_payload(&migrated)?;
        sqlx::query(
            "INSERT INTO reference_venue_listings_current(
                listing_id,instrument_id,listing_venue_id,market_segment_id,listing_symbol,
                listing_role,status,effective_to_unix_nanos,payload
             ) VALUES (?,?,?,?,?,?,?,?,?)
             ON CONFLICT(listing_id) DO UPDATE SET
                instrument_id=excluded.instrument_id,listing_venue_id=excluded.listing_venue_id,
                market_segment_id=excluded.market_segment_id,listing_symbol=excluded.listing_symbol,
                listing_role=excluded.listing_role,status=excluded.status,
                effective_to_unix_nanos=excluded.effective_to_unix_nanos,payload=excluded.payload",
        )
        .bind(migrated.listing_id.as_str())
        .bind(migrated.instrument_id.as_str())
        .bind(migrated.listing_venue_id.as_str())
        .bind(
            migrated
                .market_segment_id
                .as_ref()
                .map(|value| value.as_str()),
        )
        .bind(migrated.listing_symbol.as_str())
        .bind("unknown")
        .bind(migrated.status.as_str())
        .bind(
            migrated
                .effective_to_unix_nanos
                .map(|value| value.get() as i64),
        )
        .bind(payload)
        .execute(&mut *tx)
        .await?;
    }

    for market in markets {
        let migrated = VenueMarket {
            market_id: market.market_id,
            instrument_id: market.instrument_id,
            execution_venue_id: venue_id_from_exchange(&market.exchange_id)?,
            origin_listing_id: market.listing_id,
            market_segment_id: None,
            venue_symbol: market.venue_symbol,
            trading_calendar_id: None,
            trading_session_ids: Vec::new(),
            base_asset_id: market.base_asset_id,
            quote_asset_id: market.quote_asset_id,
            status: market.status,
            trading_rules: TradingRules {
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
        let payload = encode_payload(&migrated)?;
        sqlx::query(
            "INSERT INTO reference_venue_markets_current(
                market_id,instrument_id,execution_venue_id,origin_listing_id,market_segment_id,
                venue_symbol,status,effective_to_unix_nanos,payload
             ) VALUES (?,?,?,?,?,?,?,?,?)
             ON CONFLICT(market_id) DO UPDATE SET
                instrument_id=excluded.instrument_id,execution_venue_id=excluded.execution_venue_id,
                origin_listing_id=excluded.origin_listing_id,
                market_segment_id=excluded.market_segment_id,venue_symbol=excluded.venue_symbol,
                status=excluded.status,effective_to_unix_nanos=excluded.effective_to_unix_nanos,
                payload=excluded.payload",
        )
        .bind(migrated.market_id.as_str())
        .bind(migrated.instrument_id.as_str())
        .bind(migrated.execution_venue_id.as_str())
        .bind(
            migrated
                .origin_listing_id
                .as_ref()
                .map(|value| value.as_str()),
        )
        .bind(
            migrated
                .market_segment_id
                .as_ref()
                .map(|value| value.as_str()),
        )
        .bind(migrated.venue_symbol.as_ref().map(|value| value.as_str()))
        .bind(migrated.status.as_str())
        .bind(
            migrated
                .effective_to_unix_nanos
                .map(|value| value.get() as i64),
        )
        .bind(payload)
        .execute(&mut *tx)
        .await?;
    }

    sqlx::query("UPDATE reference_meta SET schema_version = 8 WHERE id = 1")
        .execute(&mut *tx)
        .await?;
    tx.commit().await
}

async fn load_payloads<T>(pool: &SqlitePool, query: &'static str) -> sqlx::Result<Vec<T>>
where
    T: serde::de::DeserializeOwned,
{
    sqlx::query_scalar::<_, String>(query)
        .fetch_all(pool)
        .await?
        .into_iter()
        .map(|payload| serde_json::from_str(&payload).map_err(json_protocol))
        .collect()
}

fn venue_id_from_exchange(
    exchange_id: &kairos_primitives::reference::ExchangeId,
) -> sqlx::Result<VenueId> {
    let key = exchange_id
        .as_str()
        .strip_prefix("exchange:")
        .unwrap_or_else(|| exchange_id.as_str());
    VenueId::new(format!("venue:{key}")).map_err(|error| sqlx::Error::Protocol(error.to_string()))
}

fn ensure_migrated_venue(
    venues: &mut BTreeMap<String, Venue>,
    exchange_id: &kairos_primitives::reference::ExchangeId,
    role: VenueRole,
) -> sqlx::Result<()> {
    let venue = venues.entry(exchange_id.to_string()).or_insert(Venue {
        venue_id: venue_id_from_exchange(exchange_id)?,
        name: exchange_id.to_string(),
        venue_kind: VenueKind::RegulatedExchange,
        roles: BTreeSet::new(),
        mic: None,
        operating_mic: None,
        parent_venue_id: None,
        jurisdiction: None,
        status: ReferenceStatus::Unknown,
    });
    venue.roles.insert(role);
    Ok(())
}

fn encode_payload(value: &impl serde::Serialize) -> sqlx::Result<String> {
    serde_json::to_string(value).map_err(json_protocol)
}

fn json_protocol(error: serde_json::Error) -> sqlx::Error {
    sqlx::Error::Protocol(error.to_string())
}

async fn ensure_provider_control_desired_state(pool: &SqlitePool) -> sqlx::Result<()> {
    let has_desired_state = sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM pragma_table_info('reference_provider_control') WHERE name = 'desired_state'",
    )
    .fetch_one(pool)
    .await?
        != 0;
    if !has_desired_state {
        sqlx::query(
            "ALTER TABLE reference_provider_control ADD COLUMN desired_state TEXT NOT NULL DEFAULT 'enabled' CHECK (desired_state IN ('enabled', 'disabled', 'paused', 'removed'))",
        )
        .execute(pool)
        .await?;
    }
    sqlx::query(
        "UPDATE reference_provider_control SET desired_state = CASE WHEN paused = 1 THEN 'paused' ELSE desired_state END",
    )
    .execute(pool)
    .await?;
    Ok(())
}
