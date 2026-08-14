//! Read-only SQLite data plane for the Reference catalog.
//!
//! Reference is the only writer. Consumers open short-lived read-only
//! connections through this contract so the service's private persistence
//! implementation and SQL records do not leak across module boundaries.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::time::Duration;

use rusqlite::types::Value;
use rusqlite::{params, params_from_iter, Connection, OpenFlags, OptionalExtension};

use crate::model::{ExecutionAccess, Instrument, MarketDataAccess};
use crate::{ContractError, ContractResult, LifecycleEvent, ReferenceMarket};

pub const REFERENCE_SQLITE_SCHEMA_VERSION: u32 = 1;
const MAX_PAGE_SIZE: usize = 10_000;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ReferenceWatermark {
    pub generation: u64,
    pub event_sequence: u64,
    pub committed_at_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize)]
pub struct ReferenceCatalogStats {
    pub entities: u64,
    pub assets: u64,
    pub instruments: u64,
    pub listings: u64,
    pub markets: u64,
    pub active_markets: u64,
    pub financial_products: u64,
    pub execution_accesses: u64,
    pub market_data_accesses: u64,
    pub lifecycle_events: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReferenceCollection {
    Entities,
    Assets,
    Instruments,
    Listings,
    Markets,
    FinancialProducts,
    ExecutionAccesses,
    MarketDataAccesses,
    LifecycleEvents,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct SqliteMarketQuery {
    pub market_id: Option<String>,
    pub source_id: Option<String>,
    pub source_symbol: Option<String>,
    pub instrument_id: Option<String>,
    pub listing_id: Option<String>,
    pub underlying_instrument_id: Option<String>,
    pub exchange_id: Option<String>,
    pub market_type: Option<String>,
    pub asset_type: Option<String>,
    pub statuses: Vec<String>,
    pub after_market_id: Option<String>,
    pub limit: usize,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct SqliteInstrumentQuery {
    pub symbol: Option<String>,
    pub instrument_type: Option<String>,
    pub underlying_instrument_id: Option<String>,
    pub statuses: Vec<String>,
    pub after_instrument_id: Option<String>,
    pub limit: usize,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct SqliteExecutionAccessQuery {
    pub access_id: Option<String>,
    pub market_id: Option<String>,
    pub provider_id: Option<String>,
    pub statuses: Vec<String>,
    pub after_access_id: Option<String>,
    pub limit: usize,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct SqliteMarketDataAccessQuery {
    pub access_id: Option<String>,
    pub market_id: Option<String>,
    pub provider_id: Option<String>,
    pub statuses: Vec<String>,
    pub after_access_id: Option<String>,
    pub limit: usize,
}

impl SqliteMarketQuery {
    pub fn page_size(mut self, limit: usize) -> Self {
        self.limit = limit;
        self
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceProjection {
    pub watermark: ReferenceWatermark,
    pub markets: Vec<ReferenceMarket>,
    pub instruments: BTreeMap<String, Instrument>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceMarketPage {
    pub watermark: ReferenceWatermark,
    pub markets: Vec<ReferenceMarket>,
}

#[derive(Clone, Debug)]
pub struct ReferenceSqliteReader {
    path: PathBuf,
}

impl ReferenceSqliteReader {
    pub fn open(path: impl AsRef<Path>) -> ContractResult<Self> {
        let path = path.as_ref().to_path_buf();
        let connection = open_read_only(&path)?;
        validate_schema(&connection)?;
        Ok(Self { path })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn watermark(&self) -> ContractResult<ReferenceWatermark> {
        let connection = self.connection()?;
        read_watermark(&connection)
    }

    pub fn stats(&self) -> ContractResult<ReferenceCatalogStats> {
        let connection = self.connection()?;
        let values: (i64, i64, i64, i64, i64, i64, i64, i64, i64, i64) = connection
            .query_row(
                "SELECT \
                 (SELECT COUNT(*) FROM reference_entities_current), \
                 (SELECT COUNT(*) FROM reference_assets_current), \
                 (SELECT COUNT(*) FROM reference_instruments_current), \
                 (SELECT COUNT(*) FROM reference_listings_current), \
                 (SELECT COUNT(*) FROM reference_markets_current), \
                 (SELECT COUNT(*) FROM reference_markets_current WHERE status = 'active'), \
                 (SELECT COUNT(*) FROM reference_financial_products_current), \
                 (SELECT COUNT(*) FROM reference_execution_accesses_current), \
                 (SELECT COUNT(*) FROM reference_market_data_accesses_current), \
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
                        row.get(7)?,
                        row.get(8)?,
                        row.get(9)?,
                    ))
                },
            )
            .map_err(transport)?;
        Ok(ReferenceCatalogStats {
            entities: non_negative(values.0, "entities")?,
            assets: non_negative(values.1, "assets")?,
            instruments: non_negative(values.2, "instruments")?,
            listings: non_negative(values.3, "listings")?,
            markets: non_negative(values.4, "markets")?,
            active_markets: non_negative(values.5, "active_markets")?,
            financial_products: non_negative(values.6, "financial_products")?,
            execution_accesses: non_negative(values.7, "execution_accesses")?,
            market_data_accesses: non_negative(values.8, "market_data_accesses")?,
            lifecycle_events: non_negative(values.9, "lifecycle_events")?,
        })
    }

    /// Read a bounded collection as contract-neutral JSON payloads. This is
    /// intended for inspection surfaces; business modules should use typed,
    /// scoped methods such as `markets` and `projection`.
    pub fn records(
        &self,
        collection: ReferenceCollection,
        limit: usize,
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
            ReferenceCollection::Entities,
            ReferenceCollection::Assets,
            ReferenceCollection::Instruments,
            ReferenceCollection::Listings,
            ReferenceCollection::Markets,
            ReferenceCollection::FinancialProducts,
            ReferenceCollection::ExecutionAccesses,
            ReferenceCollection::MarketDataAccesses,
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

    pub fn market(&self, market_id: &str) -> ContractResult<Option<ReferenceMarket>> {
        let connection = self.connection()?;
        read_payload_optional(
            &connection,
            "SELECT payload FROM reference_markets_current WHERE market_id = ?",
            market_id,
        )
    }

    pub fn instrument(&self, instrument_id: &str) -> ContractResult<Option<Instrument>> {
        let connection = self.connection()?;
        read_payload_optional(
            &connection,
            "SELECT payload FROM reference_instruments_current WHERE instrument_id = ?",
            instrument_id,
        )
    }

    /// Read a bounded, typed set of execution paths. Execution consumers use
    /// this before preflight; they must not inspect the generic JSON collection
    /// or infer a provider address from a MarketId.
    pub fn execution_accesses(
        &self,
        query: &SqliteExecutionAccessQuery,
    ) -> ContractResult<Vec<ExecutionAccess>> {
        let connection = self.connection()?;
        let mut sql =
            String::from("SELECT payload FROM reference_execution_accesses_current WHERE 1 = 1");
        let mut values = Vec::<Value>::new();
        push_filter(&mut sql, &mut values, "access_id", query.access_id.as_ref());
        push_filter(&mut sql, &mut values, "market_id", query.market_id.as_ref());
        push_filter(
            &mut sql,
            &mut values,
            "provider_id",
            query.provider_id.as_ref(),
        );
        if !query.statuses.is_empty() {
            sql.push_str(" AND status IN (");
            for (index, status) in query.statuses.iter().enumerate() {
                if index > 0 {
                    sql.push_str(", ");
                }
                sql.push('?');
                values.push(Value::Text(status.clone()));
            }
            sql.push(')');
        }
        push_filter(
            &mut sql,
            &mut values,
            "access_id >",
            query.after_access_id.as_ref(),
        );
        sql.push_str(" ORDER BY access_id LIMIT ?");
        values.push(Value::Integer(bounded_limit(query.limit) as i64));
        let mut statement = connection.prepare(&sql).map_err(transport)?;
        let rows = statement
            .query_map(params_from_iter(values), |row| row.get::<_, String>(0))
            .map_err(transport)?;
        rows.map(|row| decode_payload(&row.map_err(transport)?))
            .collect()
    }

    pub fn market_data_accesses(
        &self,
        query: &SqliteMarketDataAccessQuery,
    ) -> ContractResult<Vec<MarketDataAccess>> {
        let connection = self.connection()?;
        let mut sql =
            String::from("SELECT payload FROM reference_market_data_accesses_current WHERE 1 = 1");
        let mut values = Vec::<Value>::new();
        push_filter(&mut sql, &mut values, "access_id", query.access_id.as_ref());
        push_filter(&mut sql, &mut values, "market_id", query.market_id.as_ref());
        push_filter(
            &mut sql,
            &mut values,
            "provider_id",
            query.provider_id.as_ref(),
        );
        if !query.statuses.is_empty() {
            sql.push_str(" AND status IN (");
            for (index, status) in query.statuses.iter().enumerate() {
                if index > 0 {
                    sql.push_str(", ");
                }
                sql.push('?');
                values.push(Value::Text(status.clone()));
            }
            sql.push(')');
        }
        push_filter(
            &mut sql,
            &mut values,
            "access_id >",
            query.after_access_id.as_ref(),
        );
        sql.push_str(" ORDER BY access_id LIMIT ?");
        values.push(Value::Integer(bounded_limit(query.limit) as i64));
        let mut statement = connection.prepare(&sql).map_err(transport)?;
        let rows = statement
            .query_map(params_from_iter(values), |row| row.get::<_, String>(0))
            .map_err(transport)?;
        rows.map(|row| decode_payload(&row.map_err(transport)?))
            .collect()
    }

    pub fn markets(&self, query: &SqliteMarketQuery) -> ContractResult<Vec<ReferenceMarket>> {
        let connection = self.connection()?;
        read_markets(&connection, query)
    }

    pub fn market_page(&self, query: &SqliteMarketQuery) -> ContractResult<ReferenceMarketPage> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction().map_err(transport)?;
        let watermark = read_watermark(&transaction)?;
        let markets = read_markets(&transaction, query)?;
        transaction.commit().map_err(transport)?;
        Ok(ReferenceMarketPage { watermark, markets })
    }

    pub fn instruments(&self, query: &SqliteInstrumentQuery) -> ContractResult<Vec<Instrument>> {
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
                values.push(Value::Text(status.clone()));
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

    /// Read a consumer-scoped market projection and all referenced
    /// instruments from one SQLite snapshot transaction.
    pub fn projection(&self, query: &SqliteMarketQuery) -> ContractResult<ReferenceProjection> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction().map_err(transport)?;
        let watermark = read_watermark(&transaction)?;
        let markets = read_markets(&transaction, query)?;
        let instrument_ids: BTreeSet<&str> = markets
            .iter()
            .map(|market| market.instrument_id.as_str())
            .collect();
        let mut instruments = BTreeMap::new();
        for instrument_id in instrument_ids {
            let instrument: Option<Instrument> = read_payload_optional(
                &transaction,
                "SELECT payload FROM reference_instruments_current WHERE instrument_id = ?",
                instrument_id,
            )?;
            let Some(instrument) = instrument else {
                return Err(ContractError::Invalid(format!(
                    "Reference market points to missing instrument {instrument_id}"
                )));
            };
            instruments.insert(instrument.instrument_id.clone(), instrument);
        }
        transaction.commit().map_err(transport)?;
        Ok(ReferenceProjection {
            watermark,
            markets,
            instruments,
        })
    }

    pub fn changes_after(
        &self,
        sequence: u64,
        limit: usize,
    ) -> ContractResult<Vec<LifecycleEvent>> {
        let connection = self.connection()?;
        let from = sequence
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
        ReferenceCollection::Entities => ("reference_entities_current", "entity_id"),
        ReferenceCollection::Assets => ("reference_assets_current", "asset_id"),
        ReferenceCollection::Instruments => ("reference_instruments_current", "instrument_id"),
        ReferenceCollection::Listings => ("reference_listings_current", "listing_id"),
        ReferenceCollection::Markets => ("reference_markets_current", "market_id"),
        ReferenceCollection::FinancialProducts => {
            ("reference_financial_products_current", "product_id")
        }
        ReferenceCollection::ExecutionAccesses => {
            ("reference_execution_accesses_current", "access_id")
        }
        ReferenceCollection::MarketDataAccesses => {
            ("reference_market_data_accesses_current", "access_id")
        }
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
        generation: non_negative(generation, "generation")?,
        event_sequence: non_negative(event_sequence, "event_sequence")?,
        committed_at_unix_nanos: non_negative(committed_at, "committed_at_unix_nanos")?,
    })
}

fn read_markets(
    connection: &Connection,
    query: &SqliteMarketQuery,
) -> ContractResult<Vec<ReferenceMarket>> {
    let mut sql = String::from("SELECT payload FROM reference_markets_current WHERE 1 = 1");
    let mut values = Vec::<Value>::new();
    push_filter(&mut sql, &mut values, "market_id", query.market_id.as_ref());
    push_filter(&mut sql, &mut values, "source_id", query.source_id.as_ref());
    push_filter(
        &mut sql,
        &mut values,
        "source_symbol",
        query.source_symbol.as_ref(),
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
        "market_type",
        query.market_type.as_ref(),
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
            values.push(Value::Text(status.clone()));
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

fn push_filter(sql: &mut String, values: &mut Vec<Value>, column: &str, value: Option<&String>) {
    if let Some(value) = value {
        sql.push_str(" AND ");
        sql.push_str(column);
        if !column.ends_with('>') {
            sql.push_str(" =");
        }
        sql.push_str(" ?");
        values.push(Value::Text(value.clone()));
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

fn decode_payload<T: serde::de::DeserializeOwned>(payload: &str) -> ContractResult<T> {
    serde_json::from_str(payload)
        .map_err(|error| ContractError::Invalid(format!("decode Reference SQLite row: {error}")))
}

fn bounded_limit(limit: usize) -> usize {
    limit.clamp(1, MAX_PAGE_SIZE)
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
    use super::{ReferenceCollection, ReferenceSqliteReader, SqliteMarketQuery};

    #[test]
    fn reader_is_read_only_and_returns_consistent_projection() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE reference_meta(\
                    id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL,\
                    generation INTEGER NOT NULL, event_sequence INTEGER NOT NULL,\
                    committed_at_unix_nanos INTEGER NOT NULL);\
                 INSERT INTO reference_meta VALUES(1, 1, 7, 11, 13);\
                 CREATE TABLE reference_markets_current(\
                    market_id TEXT PRIMARY KEY, source_id TEXT, market_key TEXT,\
                    instrument_id TEXT, listing_id TEXT, exchange_id TEXT, market_type TEXT,\
                    asset_type TEXT, underlying_instrument_id TEXT, source_symbol TEXT,\
                    status TEXT, effective_to_unix_nanos INTEGER, payload TEXT);\
                 CREATE TABLE reference_instruments_current(\
                    instrument_id TEXT PRIMARY KEY, payload TEXT);\
                 CREATE TABLE reference_execution_accesses_current(access_id TEXT PRIMARY KEY, status TEXT, payload TEXT);\
                 CREATE TABLE reference_market_data_accesses_current(access_id TEXT PRIMARY KEY, status TEXT, payload TEXT);\
                 CREATE TABLE reference_lifecycle(sequence INTEGER PRIMARY KEY, payload TEXT);",
            )
            .unwrap();
        let market = serde_json::json!({
            "source_id": "binance-spot",
            "market_id": "market:binance:btc-usdt",
            "market_key": "btc-usdt",
            "instrument_id": "instrument:btc",
            "listing_id": "listing:btc",
            "exchange_id": "binance",
            "market_type": "spot",
            "source_symbol": "BTCUSDT",
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
                "INSERT INTO reference_markets_current VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rusqlite::params![
                    "market:binance:btc-usdt",
                    "binance-spot",
                    "btc-usdt",
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

        let reader = ReferenceSqliteReader::open(&path).unwrap();
        let projection = reader
            .projection(&SqliteMarketQuery {
                source_id: Some("binance-spot".into()),
                limit: 100,
                ..Default::default()
            })
            .unwrap();
        assert_eq!(projection.watermark.generation, 7);
        assert_eq!(projection.markets.len(), 1);
        assert_eq!(projection.instruments.len(), 1);
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
                 INSERT INTO reference_meta VALUES(1,1,1,0,1);
                 CREATE TABLE reference_entities_current(entity_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_assets_current(asset_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_instruments_current(instrument_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_listings_current(listing_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_markets_current(market_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_financial_products_current(product_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_execution_accesses_current(access_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_market_data_accesses_current(access_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_lifecycle(sequence INTEGER PRIMARY KEY, payload TEXT);
                 WITH RECURSIVE rows(value) AS (
                   SELECT 1 UNION ALL SELECT value + 1 FROM rows WHERE value < 1000000
                 )
                 INSERT INTO reference_markets_current(market_id,status,payload)
                 SELECT printf('market:%08d', value), 'active', '{}' FROM rows;",
            )
            .unwrap();
        drop(connection);

        let reader = ReferenceSqliteReader::open(&path).unwrap();
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
