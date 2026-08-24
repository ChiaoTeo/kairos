//! Read-only SQLite implementation of the Reference catalog contract.
//!
//! Reference is the only writer. Cross-module callers access this adapter only
//! through the contract-owned `ReferenceClient`; table names and SQL remain
//! private to this crate.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::time::Duration;

use kairos_primitives::reference::{
    AssetClass, AssetId, ExchangeId, InstrumentId, InstrumentKind, ListingId, MarketId,
    ReferenceStatus, Symbol,
};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use rusqlite::types::Value;
use rusqlite::{Connection, OpenFlags, OptionalExtension, params, params_from_iter};

use crate::catalog::{Asset, Exchange, Instrument, Listing};
use crate::{
    AccountReferenceSnapshot, ContractError, ContractResult, ExecutionReferenceSnapshot, Market,
    MarketReferenceSnapshot, ReferenceCatalogSnapshot,
};

pub const REFERENCE_SQLITE_SCHEMA_VERSION: u32 = 6;
const MAX_PAGE_SIZE: usize = 10_000;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize)]
pub struct ReferenceWatermark {
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub committed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize)]
pub struct ReferenceCatalogStats {
    pub exchanges: u64,
    pub assets: u64,
    pub instruments: u64,
    pub listings: u64,
    pub markets: u64,
    pub active_markets: u64,
    pub lifecycle_events: u64,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize)]
pub struct ReferenceIntegrityStats {
    pub missing_equity_markets: u64,
    pub legacy_exchange_market_ids: u64,
    pub legacy_exchange_listing_ids: u64,
    pub option_listings: u64,
    pub option_markets: u64,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize)]
pub struct ReferenceCatalogStatus {
    pub watermark: ReferenceWatermark,
    pub counts: ReferenceCatalogStats,
    pub integrity: ReferenceIntegrityStats,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReferenceCollection {
    Exchanges,
    Assets,
    Instruments,
    Listings,
    Markets,
    LifecycleEvents,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MarketCatalogQuery {
    pub market_id: Option<MarketId>,
    pub venue_symbol: Option<Symbol>,
    pub instrument_id: Option<InstrumentId>,
    pub listing_id: Option<ListingId>,
    pub underlying_instrument_id: Option<InstrumentId>,
    pub exchange_id: Option<ExchangeId>,
    pub instrument_kind: Option<InstrumentKind>,
    pub asset_type: Option<AssetClass>,
    pub statuses: Vec<ReferenceStatus>,
    pub after_market_id: Option<MarketId>,
    pub limit: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct InstrumentCatalogQuery {
    pub symbol: Option<Symbol>,
    pub instrument_type: Option<InstrumentKind>,
    pub underlying_instrument_id: Option<InstrumentId>,
    pub statuses: Vec<ReferenceStatus>,
    pub after_instrument_id: Option<InstrumentId>,
    pub limit: u64,
}

impl MarketCatalogQuery {
    pub fn page_size(mut self, limit: u64) -> Self {
        self.limit = limit;
        self
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceMarketCatalogPage {
    pub watermark: ReferenceWatermark,
    pub markets: Vec<Market>,
    pub instruments: BTreeMap<InstrumentId, Instrument>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceMarketPage {
    pub watermark: ReferenceWatermark,
    pub markets: Vec<Market>,
}

#[derive(Clone, Debug)]
pub struct ReferenceCatalog {
    path: PathBuf,
}

impl ReferenceCatalog {
    pub fn open(path: impl AsRef<Path>) -> ContractResult<Self> {
        let path = path.as_ref().to_path_buf();
        let connection = open_read_only(&path)?;
        validate_schema(&connection)?;
        Ok(Self { path })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub(crate) fn market_snapshot(
        &self,
        actor_id: &str,
    ) -> ContractResult<MarketReferenceSnapshot> {
        Ok(self.consumer_snapshot(actor_id)?.for_market())
    }

    pub(crate) fn execution_snapshot(
        &self,
        actor_id: &str,
    ) -> ContractResult<ExecutionReferenceSnapshot> {
        Ok(self.consumer_snapshot(actor_id)?.for_execution())
    }

    pub(crate) fn account_snapshot(
        &self,
        actor_id: &str,
    ) -> ContractResult<AccountReferenceSnapshot> {
        Ok(self.consumer_snapshot(actor_id)?.for_account())
    }

    fn consumer_snapshot(&self, actor_id: &str) -> ContractResult<ReferenceCatalogSnapshot> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction().map_err(transport)?;
        let watermark = read_watermark(&transaction)?;
        let snapshot = ReferenceCatalogSnapshot {
            actor_id: kairos_primitives::runtime::ActorId::new(actor_id)
                .map_err(|error| ContractError::Invalid(error.to_string()))?,
            workspace_id: kairos_primitives::runtime::WorkspaceId::new("workspace:reference")
                .expect("valid reference workspace"),
            generation: watermark.generation,
            event_sequence: watermark.event_sequence,
            instruments: read_all(
                &transaction,
                "reference_instruments_current",
                "instrument_id",
            )?,
            markets: read_all(&transaction, "reference_markets_current", "market_id")?,
            ..ReferenceCatalogSnapshot::default()
        };
        transaction.commit().map_err(transport)?;
        Ok(snapshot)
    }

    pub fn watermark(&self) -> ContractResult<ReferenceWatermark> {
        let connection = self.connection()?;
        read_watermark(&connection)
    }

    pub fn stats(&self) -> ContractResult<ReferenceCatalogStats> {
        let connection = self.connection()?;
        read_stats(&connection)
    }

    pub fn status(&self) -> ContractResult<ReferenceCatalogStatus> {
        let connection = self.connection()?;
        Ok(ReferenceCatalogStatus {
            watermark: read_watermark(&connection)?,
            counts: read_stats(&connection)?,
            integrity: read_integrity_stats(&connection)?,
        })
    }

    /// Read a bounded collection as contract-neutral JSON payloads. This is
    /// intended only for Reference-owned inspection surfaces.
    pub fn records(
        &self,
        collection: ReferenceCollection,
        limit: u64,
    ) -> ContractResult<Vec<serde_json::Value>> {
        let connection = self.connection()?;
        let (table, key) = collection_table(collection);
        let sql = format!("SELECT payload FROM {table} ORDER BY {key} LIMIT ?");
        let mut statement = connection.prepare(&sql).map_err(transport)?;
        let rows = statement
            .query_map([bounded_limit(limit) as i64], |row| row.get::<_, String>(0))
            .map_err(transport)?;
        rows.map(|row| decode_payload(&row.map_err(transport)?))
            .collect()
    }

    pub fn record(&self, identifier: &str) -> ContractResult<Option<serde_json::Value>> {
        let connection = self.connection()?;
        let mut matched = None;
        for collection in [
            ReferenceCollection::Exchanges,
            ReferenceCollection::Assets,
            ReferenceCollection::Instruments,
            ReferenceCollection::Listings,
            ReferenceCollection::Markets,
            ReferenceCollection::LifecycleEvents,
        ] {
            let (table, key) = collection_table(collection);
            let sql = format!("SELECT payload FROM {table} WHERE {key} = ?");
            let payload: Option<String> = connection
                .query_row(&sql, [identifier], |row| row.get(0))
                .optional()
                .map_err(transport)?;
            if let Some(payload) = payload {
                if matched.is_some() {
                    return Err(ContractError::Invalid(format!(
                        "ambiguous Reference identifier {identifier}"
                    )));
                }
                matched = Some(decode_payload(&payload)?);
            }
        }
        Ok(matched)
    }

    pub fn market(&self, market_id: &MarketId) -> ContractResult<Option<Market>> {
        let connection = self.connection()?;
        read_payload_optional(
            &connection,
            "SELECT payload FROM reference_markets_current WHERE market_id = ?",
            market_id.as_str(),
        )
    }

    pub fn instrument(&self, instrument_id: &InstrumentId) -> ContractResult<Option<Instrument>> {
        let connection = self.connection()?;
        read_payload_optional(
            &connection,
            "SELECT payload FROM reference_instruments_current WHERE instrument_id = ?",
            instrument_id.as_str(),
        )
    }

    pub fn asset(&self, asset_id: &AssetId) -> ContractResult<Option<Asset>> {
        let connection = self.connection()?;
        read_payload_optional(
            &connection,
            "SELECT payload FROM reference_assets_current WHERE asset_id = ?",
            asset_id.as_str(),
        )
    }

    pub fn exchange(&self, exchange_id: &str) -> ContractResult<Option<Exchange>> {
        let connection = self.connection()?;
        read_payload_optional(
            &connection,
            "SELECT payload FROM reference_exchanges_current WHERE exchange_id = ?",
            exchange_id,
        )
    }

    pub fn listing(&self, listing_id: &ListingId) -> ContractResult<Option<Listing>> {
        let connection = self.connection()?;
        read_payload_optional(
            &connection,
            "SELECT payload FROM reference_listings_current WHERE listing_id = ?",
            listing_id.as_str(),
        )
    }

    pub fn markets(&self, query: &MarketCatalogQuery) -> ContractResult<Vec<Market>> {
        let connection = self.connection()?;
        read_markets(&connection, query)
    }

    pub fn market_page(&self, query: &MarketCatalogQuery) -> ContractResult<ReferenceMarketPage> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction().map_err(transport)?;
        let watermark = read_watermark(&transaction)?;
        let markets = read_markets(&transaction, query)?;
        transaction.commit().map_err(transport)?;
        Ok(ReferenceMarketPage { watermark, markets })
    }

    pub fn instruments(&self, query: &InstrumentCatalogQuery) -> ContractResult<Vec<Instrument>> {
        let connection = self.connection()?;
        let mut sql = String::from("SELECT payload FROM reference_instruments_current WHERE 1 = 1");
        let mut values = Vec::<Value>::new();
        push_filter(&mut sql, &mut values, "symbol", query.symbol.as_ref());
        push_filter(
            &mut sql,
            &mut values,
            "instrument_type",
            query.instrument_type.as_ref(),
        );
        push_filter(
            &mut sql,
            &mut values,
            "underlying_instrument_id",
            query.underlying_instrument_id.as_ref(),
        );
        if !query.statuses.is_empty() {
            sql.push_str(" AND status IN (");
            for (index, status) in query.statuses.iter().enumerate() {
                if index > 0 {
                    sql.push_str(", ");
                }
                sql.push('?');
                values.push(Value::Text(status.to_string()));
            }
            sql.push(')');
        }
        push_filter(
            &mut sql,
            &mut values,
            "instrument_id >",
            query.after_instrument_id.as_ref(),
        );
        sql.push_str(" ORDER BY instrument_id LIMIT ?");
        values.push(Value::Integer(bounded_limit(query.limit) as i64));
        let mut statement = connection.prepare(&sql).map_err(transport)?;
        let rows = statement
            .query_map(params_from_iter(values), |row| row.get::<_, String>(0))
            .map_err(transport)?;
        rows.map(|row| decode_payload(&row.map_err(transport)?))
            .collect()
    }

    /// Read a market catalog page and all referenced instruments
    /// from one SQLite snapshot transaction.
    pub fn market_catalog(
        &self,
        query: &MarketCatalogQuery,
    ) -> ContractResult<ReferenceMarketCatalogPage> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction().map_err(transport)?;
        let watermark = read_watermark(&transaction)?;
        let markets = read_markets(&transaction, query)?;
        let instrument_ids: BTreeSet<&InstrumentId> =
            markets.iter().map(|market| &market.instrument_id).collect();
        let mut instruments = BTreeMap::new();
        for instrument_id in instrument_ids {
            let instrument: Option<Instrument> = read_payload_optional(
                &transaction,
                "SELECT payload FROM reference_instruments_current WHERE instrument_id = ?",
                instrument_id.as_str(),
            )?;
            let Some(instrument) = instrument else {
                return Err(ContractError::Invalid(format!(
                    "Reference market points to missing instrument {instrument_id}"
                )));
            };
            instruments.insert(instrument.instrument_id.clone(), instrument);
        }
        transaction.commit().map_err(transport)?;
        Ok(ReferenceMarketCatalogPage {
            watermark,
            markets,
            instruments,
        })
    }

    pub fn changes_after(
        &self,
        sequence: Sequence,
        limit: u64,
    ) -> ContractResult<Vec<serde_json::Value>> {
        let connection = self.connection()?;
        let from = sequence
            .get()
            .checked_add(1)
            .and_then(|value| i64::try_from(value).ok())
            .ok_or_else(|| ContractError::Invalid("Reference sequence is out of range".into()))?;
        let limit = bounded_limit(limit) as i64;
        let mut statement = connection
            .prepare(
                "SELECT payload FROM reference_lifecycle \
                 WHERE sequence >= ? ORDER BY sequence LIMIT ?",
            )
            .map_err(transport)?;
        let rows = statement
            .query_map(params![from, limit], |row| row.get::<_, String>(0))
            .map_err(transport)?;
        rows.map(|row| decode_payload(&row.map_err(transport)?))
            .collect()
    }

    fn connection(&self) -> ContractResult<Connection> {
        let connection = open_read_only(&self.path)?;
        validate_schema(&connection)?;
        Ok(connection)
    }
}

fn collection_table(collection: ReferenceCollection) -> (&'static str, &'static str) {
    match collection {
        ReferenceCollection::Exchanges => ("reference_exchanges_current", "exchange_id"),
        ReferenceCollection::Assets => ("reference_assets_current", "asset_id"),
        ReferenceCollection::Instruments => ("reference_instruments_current", "instrument_id"),
        ReferenceCollection::Listings => ("reference_listings_current", "listing_id"),
        ReferenceCollection::Markets => ("reference_markets_current", "market_id"),
        ReferenceCollection::LifecycleEvents => ("reference_lifecycle", "sequence"),
    }
}

fn open_read_only(path: &Path) -> ContractResult<Connection> {
    let connection = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(transport)?;
    connection
        .busy_timeout(Duration::from_secs(5))
        .map_err(transport)?;
    connection
        .pragma_update(None, "query_only", true)
        .map_err(transport)?;
    Ok(connection)
}

fn validate_schema(connection: &Connection) -> ContractResult<()> {
    let schema_version: Option<i64> = connection
        .query_row(
            "SELECT schema_version FROM reference_meta WHERE id = 1",
            [],
            |row| row.get(0),
        )
        .optional()
        .map_err(transport)?;
    match schema_version {
        Some(version) if version == i64::from(REFERENCE_SQLITE_SCHEMA_VERSION) => Ok(()),
        Some(version) => Err(ContractError::Invalid(format!(
            "unsupported Reference SQLite schema version {version}"
        ))),
        None => Err(ContractError::Invalid(
            "Reference SQLite metadata is missing".into(),
        )),
    }
}

fn read_watermark(connection: &Connection) -> ContractResult<ReferenceWatermark> {
    let (generation, event_sequence, committed_at): (i64, i64, i64) = connection
        .query_row(
            "SELECT generation, event_sequence, committed_at_unix_nanos \
             FROM reference_meta WHERE id = 1",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .map_err(transport)?;
    Ok(ReferenceWatermark {
        generation: Generation::new(non_negative(generation, "generation")?),
        event_sequence: Sequence::new(non_negative(event_sequence, "event_sequence")?),
        committed_at_unix_nanos: UnixNanos::new(non_negative(
            committed_at,
            "committed_at_unix_nanos",
        )?),
    })
}

fn read_stats(connection: &Connection) -> ContractResult<ReferenceCatalogStats> {
    let values: (i64, i64, i64, i64, i64, i64, i64) = connection
        .query_row(
            "SELECT \
             (SELECT COUNT(*) FROM reference_exchanges_current), \
             (SELECT COUNT(*) FROM reference_assets_current), \
             (SELECT COUNT(*) FROM reference_instruments_current), \
             (SELECT COUNT(*) FROM reference_listings_current), \
             (SELECT COUNT(*) FROM reference_markets_current), \
             (SELECT COUNT(*) FROM reference_markets_current WHERE status = 'active'), \
             (SELECT COUNT(*) FROM reference_lifecycle)",
            [],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                    row.get(6)?,
                ))
            },
        )
        .map_err(transport)?;
    Ok(ReferenceCatalogStats {
        exchanges: non_negative(values.0, "exchanges")?,
        assets: non_negative(values.1, "assets")?,
        instruments: non_negative(values.2, "instruments")?,
        listings: non_negative(values.3, "listings")?,
        markets: non_negative(values.4, "markets")?,
        active_markets: non_negative(values.5, "active_markets")?,
        lifecycle_events: non_negative(values.6, "lifecycle_events")?,
    })
}

fn read_integrity_stats(connection: &Connection) -> ContractResult<ReferenceIntegrityStats> {
    let missing_equity_markets = connection
        .query_row(
            "SELECT COUNT(*) \
             FROM reference_listings_current AS listing \
             WHERE listing.status IN ('active', 'trading') \
               AND listing.listing_id LIKE '%:equity:%' \
               AND NOT EXISTS ( \
                 SELECT 1 FROM reference_markets_current AS market \
                 WHERE market.listing_id = listing.listing_id \
               )",
            [],
            |row| row.get::<_, i64>(0),
        )
        .map_err(transport)?;
    let legacy_exchange_market_ids = connection
        .query_row(
            "SELECT COUNT(*) FROM reference_markets_current \
             WHERE market_id LIKE 'market:exchange:%'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .map_err(transport)?;
    let legacy_exchange_listing_ids = connection
        .query_row(
            "SELECT COUNT(*) FROM reference_listings_current \
             WHERE listing_id LIKE 'listing:exchange:%'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .map_err(transport)?;
    let option_listings = connection
        .query_row(
            "SELECT COUNT(*) FROM reference_listings_current \
             WHERE listing_id LIKE '%:option:%'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .map_err(transport)?;
    let option_markets = connection
        .query_row(
            "SELECT COUNT(*) FROM reference_markets_current \
             WHERE instrument_kind = 'option'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .map_err(transport)?;
    Ok(ReferenceIntegrityStats {
        missing_equity_markets: non_negative(missing_equity_markets, "missing_equity_markets")?,
        legacy_exchange_market_ids: non_negative(
            legacy_exchange_market_ids,
            "legacy_exchange_market_ids",
        )?,
        legacy_exchange_listing_ids: non_negative(
            legacy_exchange_listing_ids,
            "legacy_exchange_listing_ids",
        )?,
        option_listings: non_negative(option_listings, "option_listings")?,
        option_markets: non_negative(option_markets, "option_markets")?,
    })
}

fn read_markets(
    connection: &Connection,
    query: &MarketCatalogQuery,
) -> ContractResult<Vec<Market>> {
    let mut sql = String::from("SELECT payload FROM reference_markets_current WHERE 1 = 1");
    let mut values = Vec::<Value>::new();
    push_filter(&mut sql, &mut values, "market_id", query.market_id.as_ref());
    push_filter(
        &mut sql,
        &mut values,
        "venue_symbol",
        query.venue_symbol.as_ref(),
    );
    push_filter(
        &mut sql,
        &mut values,
        "instrument_id",
        query.instrument_id.as_ref(),
    );
    push_filter(
        &mut sql,
        &mut values,
        "listing_id",
        query.listing_id.as_ref(),
    );
    push_filter(
        &mut sql,
        &mut values,
        "underlying_instrument_id",
        query.underlying_instrument_id.as_ref(),
    );
    push_filter(
        &mut sql,
        &mut values,
        "exchange_id",
        query.exchange_id.as_ref(),
    );
    push_filter(
        &mut sql,
        &mut values,
        "instrument_kind",
        query.instrument_kind.as_ref(),
    );
    push_filter(
        &mut sql,
        &mut values,
        "asset_type",
        query.asset_type.as_ref(),
    );
    if !query.statuses.is_empty() {
        sql.push_str(" AND status IN (");
        for (index, status) in query.statuses.iter().enumerate() {
            if index > 0 {
                sql.push_str(", ");
            }
            sql.push('?');
            values.push(Value::Text(status.to_string()));
        }
        sql.push(')');
    }
    push_filter(
        &mut sql,
        &mut values,
        "market_id >",
        query.after_market_id.as_ref(),
    );
    sql.push_str(" ORDER BY market_id LIMIT ?");
    values.push(Value::Integer(bounded_limit(query.limit) as i64));

    let mut statement = connection.prepare(&sql).map_err(transport)?;
    let rows = statement
        .query_map(params_from_iter(values), |row| row.get::<_, String>(0))
        .map_err(transport)?;
    rows.map(|row| decode_payload(&row.map_err(transport)?))
        .collect()
}

fn push_filter<T: ToString>(
    sql: &mut String,
    values: &mut Vec<Value>,
    column: &str,
    value: Option<&T>,
) {
    if let Some(value) = value {
        sql.push_str(" AND ");
        sql.push_str(column);
        if !column.ends_with('>') {
            sql.push_str(" =");
        }
        sql.push_str(" ?");
        values.push(Value::Text(value.to_string()));
    }
}

fn read_payload_optional<T: serde::de::DeserializeOwned>(
    connection: &Connection,
    sql: &str,
    key: &str,
) -> ContractResult<Option<T>> {
    let payload = connection
        .query_row(sql, [key], |row| row.get::<_, String>(0))
        .optional()
        .map_err(transport)?;
    payload.map(|payload| decode_payload(&payload)).transpose()
}

fn read_all<T: serde::de::DeserializeOwned>(
    connection: &Connection,
    table: &str,
    key: &str,
) -> ContractResult<Vec<T>> {
    let sql = format!("SELECT payload FROM {table} ORDER BY {key}");
    let mut statement = connection.prepare(&sql).map_err(transport)?;
    let rows = statement
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(transport)?;
    rows.map(|row| decode_payload(&row.map_err(transport)?))
        .collect()
}

fn decode_payload<T: serde::de::DeserializeOwned>(payload: &str) -> ContractResult<T> {
    serde_json::from_str(payload)
        .map_err(|error| ContractError::Invalid(format!("decode Reference SQLite row: {error}")))
}

fn bounded_limit(limit: u64) -> usize {
    usize::try_from(limit.clamp(1, MAX_PAGE_SIZE as u64)).expect("bounded page size fits usize")
}

fn non_negative(value: i64, label: &str) -> ContractResult<u64> {
    u64::try_from(value)
        .map_err(|_| ContractError::Invalid(format!("Reference {label} is negative")))
}

fn transport(error: impl std::fmt::Display) -> ContractError {
    ContractError::Transport(format!("SQLite: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{MarketCatalogQuery, ReferenceCatalog, ReferenceCollection};

    #[test]
    fn reader_is_read_only_and_returns_consistent_catalog_page() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE reference_meta(\
                    id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL,\
                    generation INTEGER NOT NULL, event_sequence INTEGER NOT NULL,\
                    committed_at_unix_nanos INTEGER NOT NULL);\
                 INSERT INTO reference_meta VALUES(1, 6, 7, 11, 13);\
                 CREATE TABLE reference_markets_current(\
                    market_id TEXT PRIMARY KEY, instrument_id TEXT, listing_id TEXT,\
                    exchange_id TEXT, instrument_kind TEXT, asset_type TEXT,\
                    underlying_instrument_id TEXT, venue_symbol TEXT,\
                    status TEXT, effective_to_unix_nanos INTEGER, payload TEXT);\
                 CREATE TABLE reference_instruments_current(\
                    instrument_id TEXT PRIMARY KEY, payload TEXT);\
                 CREATE TABLE reference_exchanges_current(exchange_id TEXT PRIMARY KEY, payload TEXT);\
                 CREATE TABLE reference_assets_current(asset_id TEXT PRIMARY KEY, payload TEXT);\
                 CREATE TABLE reference_listings_current(listing_id TEXT PRIMARY KEY, payload TEXT);\
                 CREATE TABLE reference_lifecycle(sequence INTEGER PRIMARY KEY, payload TEXT);",
            )
            .unwrap();
        let market = serde_json::json!({
            "market_id": "market:binance:btc-usdt",
            "instrument_id": "instrument:btc",
            "listing_id": "listing:btc",
            "exchange_id": "binance",
            "instrument_kind": "spot",
            "venue_symbol": "BTCUSDT",
            "status": "active",
            "price_precision": 2,
            "quantity_precision": 6,
            "effective_from_unix_nanos": 0
        });
        let instrument = serde_json::json!({
            "instrument_id": "instrument:btc",
            "symbol": "BTC",
            "instrument_type": "spot",
            "status": "active"
        });
        connection
            .execute(
                "INSERT INTO reference_markets_current VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                rusqlite::params![
                    "market:binance:btc-usdt",
                    "instrument:btc",
                    "listing:btc",
                    "binance",
                    "spot",
                    Option::<String>::None,
                    Option::<String>::None,
                    "BTCUSDT",
                    "active",
                    Option::<i64>::None,
                    market.to_string()
                ],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO reference_instruments_current VALUES(?,?)",
                rusqlite::params!["instrument:btc", instrument.to_string()],
            )
            .unwrap();
        drop(connection);

        let reader = ReferenceCatalog::open(&path).unwrap();
        let catalog_page = reader
            .market_catalog(&MarketCatalogQuery {
                venue_symbol: Some(kairos_primitives::reference::Symbol::new("BTCUSDT").unwrap()),
                limit: 100,
                ..Default::default()
            })
            .unwrap();
        assert_eq!(catalog_page.watermark.generation, 7.into());
        assert_eq!(catalog_page.markets.len(), 1);
        assert_eq!(catalog_page.instruments.len(), 1);

        let scoped = reader.market_snapshot("reference-actor").unwrap();
        assert_eq!(
            (scoped.generation, scoped.event_sequence),
            (7.into(), 11.into())
        );
        assert_eq!(scoped.markets.len(), 1);
        assert_eq!(scoped.instruments.len(), 1);
    }

    #[test]
    fn status_reports_current_catalog_integrity() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE reference_meta(\
                    id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL,\
                    generation INTEGER NOT NULL, event_sequence INTEGER NOT NULL,\
                    committed_at_unix_nanos INTEGER NOT NULL);\
                 INSERT INTO reference_meta VALUES(1, 6, 7, 11, 13);\
                 CREATE TABLE reference_exchanges_current(exchange_id TEXT PRIMARY KEY, payload TEXT);\
                 CREATE TABLE reference_assets_current(asset_id TEXT PRIMARY KEY, payload TEXT);\
                 CREATE TABLE reference_instruments_current(instrument_id TEXT PRIMARY KEY, payload TEXT);\
                 CREATE TABLE reference_listings_current(\
                    listing_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL,\
                    exchange_id TEXT NOT NULL, exchange_symbol TEXT NOT NULL,\
                    status TEXT NOT NULL, effective_to_unix_nanos INTEGER, payload TEXT NOT NULL);\
                 CREATE TABLE reference_markets_current(\
                    market_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL, listing_id TEXT,\
                    exchange_id TEXT NOT NULL, instrument_kind TEXT NOT NULL, asset_type TEXT,\
                    underlying_instrument_id TEXT, venue_symbol TEXT, status TEXT NOT NULL,\
                    effective_to_unix_nanos INTEGER, payload TEXT NOT NULL);\
                 CREATE TABLE reference_lifecycle(sequence INTEGER PRIMARY KEY, payload TEXT);",
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO reference_listings_current VALUES(?,?,?,?,?,?,?)",
                rusqlite::params![
                    "listing:exchange:nasdaq:equity:AAPL:USD",
                    "instrument:equity:US:AAPL:common",
                    "exchange:nasdaq",
                    "AAPL",
                    "active",
                    Option::<i64>::None,
                    "{}"
                ],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO reference_listings_current VALUES(?,?,?,?,?,?,?)",
                rusqlite::params![
                    "listing:cboe-bzx-options:option:SPY-20270115-500-C",
                    "instrument:option:SPY:20270115:500:C",
                    "exchange:cboe-bzx-options",
                    "O:SPY260821C00500000",
                    "active",
                    Option::<i64>::None,
                    "{}"
                ],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO reference_markets_current VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                rusqlite::params![
                    "market:exchange:nasdaq:equity:AAPL",
                    "instrument:equity:US:AAPL:common",
                    "listing:exchange:nasdaq:equity:AAPL:USD",
                    "exchange:nasdaq",
                    "equity",
                    "equity",
                    Option::<String>::None,
                    "AAPL",
                    "active",
                    Option::<i64>::None,
                    "{}"
                ],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO reference_markets_current VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                rusqlite::params![
                    "market:cboe-bzx-options:option:O:SPY260821C00500000",
                    "instrument:option:SPY:20270115:500:C",
                    "listing:cboe-bzx-options:option:SPY-20270115-500-C",
                    "exchange:cboe-bzx-options",
                    "option",
                    Option::<String>::None,
                    "instrument:equity:US:SPY:common",
                    "O:SPY260821C00500000",
                    "active",
                    Option::<i64>::None,
                    "{}"
                ],
            )
            .unwrap();
        drop(connection);

        let reader = ReferenceCatalog::open(&path).unwrap();
        let status = reader.status().unwrap();

        assert_eq!(status.watermark.generation, 7.into());
        assert_eq!(status.counts.listings, 2);
        assert_eq!(status.counts.markets, 2);
        assert_eq!(status.integrity.missing_equity_markets, 0);
        assert_eq!(status.integrity.legacy_exchange_market_ids, 1);
        assert_eq!(status.integrity.legacy_exchange_listing_ids, 1);
        assert_eq!(status.integrity.option_listings, 1);
        assert_eq!(status.integrity.option_markets, 1);
    }

    #[test]
    #[ignore = "million-row scale acceptance"]
    fn million_row_catalog_reads_remain_bounded() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "PRAGMA journal_mode=OFF;
                 PRAGMA synchronous=OFF;
                 CREATE TABLE reference_meta(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, generation INTEGER NOT NULL, event_sequence INTEGER NOT NULL, committed_at_unix_nanos INTEGER NOT NULL);
                 INSERT INTO reference_meta VALUES(1,3,1,0,1);
                 CREATE TABLE reference_exchanges_current(exchange_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_assets_current(asset_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_instruments_current(instrument_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_listings_current(listing_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_markets_current(market_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_lifecycle(sequence INTEGER PRIMARY KEY, payload TEXT);
                 WITH RECURSIVE rows(value) AS (
                   SELECT 1 UNION ALL SELECT value + 1 FROM rows WHERE value < 1000000
                 )
                 INSERT INTO reference_markets_current(market_id,status,payload)
                 SELECT printf('market:%08d', value), 'active', '{}' FROM rows;",
            )
            .unwrap();
        drop(connection);

        let reader = ReferenceCatalog::open(&path).unwrap();
        assert_eq!(reader.stats().unwrap().markets, 1_000_000);
        assert_eq!(
            reader
                .records(ReferenceCollection::Markets, 128)
                .unwrap()
                .len(),
            128
        );
    }
}
