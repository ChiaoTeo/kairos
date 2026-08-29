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
    ReferenceSourceId, ReferenceStatus, Symbol,
};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use rusqlite::types::Value;
use rusqlite::{Connection, OpenFlags, OptionalExtension, params, params_from_iter};

use crate::catalog::{Asset, Exchange, Instrument, Listing};
use crate::{
    AccountReferenceSnapshot, ContractError, ContractResult, ExecutionReferenceSnapshot, Market,
    MarketReferenceSnapshot, ReferenceCatalogSnapshot, ReferenceInstrumentAvailability,
};

pub const REFERENCE_SQLITE_SCHEMA_VERSION: u32 = 6;
const MAX_PAGE_SIZE: usize = 10_000;
const MAX_EVENT_PAGE_SIZE: u64 = 4_096;

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

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferencePage {
    pub limit: Option<u64>,
    pub offset: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ExchangeCatalogQuery {
    pub exchange_ids: Option<Vec<ExchangeId>>,
    pub search: Option<String>,
    pub status: Option<ReferenceStatus>,
    pub active_only: bool,
    pub page: ReferencePage,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct AssetCatalogQuery {
    pub asset_ids: Option<Vec<AssetId>>,
    pub search: Option<String>,
    pub code: Option<Symbol>,
    pub asset_class: Option<AssetClass>,
    pub status: Option<ReferenceStatus>,
    pub active_only: bool,
    pub page: ReferencePage,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct InstrumentSearchQuery {
    pub instrument_ids: Option<Vec<InstrumentId>>,
    pub search: Option<String>,
    pub symbol: Option<Symbol>,
    pub instrument_type: Option<InstrumentKind>,
    pub product_family: Option<String>,
    pub underlying_instrument_id: Option<InstrumentId>,
    pub expiry_unix_nanos: Option<u64>,
    pub expiry_from_unix_nanos: Option<u64>,
    pub expiry_to_unix_nanos: Option<u64>,
    pub option_right: Option<String>,
    pub status: Option<ReferenceStatus>,
    pub active_only: bool,
    pub page: ReferencePage,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct InstrumentAvailabilityQuery {
    pub source_ids: Option<Vec<ReferenceSourceId>>,
    pub instrument_ids: Option<Vec<InstrumentId>>,
    pub search: Option<String>,
    pub symbol: Option<Symbol>,
    pub instrument_type: Option<InstrumentKind>,
    pub active_only: bool,
    pub page: ReferencePage,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ListingCatalogQuery {
    pub listing_ids: Option<Vec<ListingId>>,
    pub search: Option<String>,
    pub instrument_id: Option<InstrumentId>,
    pub exchange_id: Option<ExchangeId>,
    pub exchange_symbol: Option<Symbol>,
    pub status: Option<ReferenceStatus>,
    pub active_only: bool,
    pub page: ReferencePage,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MarketSearchQuery {
    pub market_ids: Option<Vec<MarketId>>,
    pub search: Option<String>,
    pub venue_symbol: Option<Symbol>,
    pub asset_code: Option<Symbol>,
    pub exchange_id: Option<ExchangeId>,
    pub instrument_kind: Option<InstrumentKind>,
    pub asset_type: Option<AssetClass>,
    pub instrument_id: Option<InstrumentId>,
    pub listing_id: Option<ListingId>,
    pub underlying_instrument_id: Option<InstrumentId>,
    pub status: Option<ReferenceStatus>,
    pub active_only: bool,
    pub page: ReferencePage,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct LifecycleCatalogQuery {
    pub sequence_from: Option<u64>,
    pub sequence_to: Option<u64>,
    pub limit: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ReferenceLifecycleEvent {
    #[serde(default)]
    pub sequence: u64,
    pub event_id: String,
    pub event_type: String,
    pub event_time_unix_nanos: UnixNanos,
    #[serde(default)]
    pub record_kind: Option<String>,
    #[serde(default)]
    pub record_id: Option<String>,
    pub market_id: Option<MarketId>,
    pub instrument_id: Option<InstrumentId>,
    pub listing_id: Option<ListingId>,
    pub exchange_id: Option<ExchangeId>,
    pub venue_symbol: Option<Symbol>,
    pub previous_status: Option<ReferenceStatus>,
    pub current_status: Option<ReferenceStatus>,
    pub previous_symbol: Option<String>,
    pub current_symbol: Option<String>,
    #[serde(default)]
    pub operation: Option<String>,
    #[serde(default)]
    pub provenance: Option<String>,
    #[serde(default)]
    pub conflict_policy: Option<String>,
    #[serde(default)]
    pub generation: Generation,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceOptionCoverage {
    pub source_id: String,
    pub underlyings: Vec<String>,
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

/// One explicitly pinned, read-only SQLite snapshot owned by Reference.
///
/// The connection and transaction never cross the contract boundary. Callers
/// receive owned typed records while every method on this value observes the
/// same committed catalog generation.
pub struct ReferenceReadSession {
    connection: Connection,
    watermark: ReferenceWatermark,
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

    pub fn read_session(&self) -> ContractResult<ReferenceReadSession> {
        ReferenceReadSession::open(&self.path)
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

impl ReferenceReadSession {
    fn open(path: &Path) -> ContractResult<Self> {
        let connection = open_read_only(path)?;
        validate_schema(&connection)?;
        connection
            .execute_batch("BEGIN DEFERRED TRANSACTION")
            .map_err(transport)?;
        // Reading metadata establishes the WAL snapshot immediately. Later
        // calls cannot drift to a newer generation on the same connection.
        let watermark = read_watermark(&connection)?;
        Ok(Self {
            connection,
            watermark,
        })
    }

    pub fn watermark(&self) -> ReferenceWatermark {
        self.watermark
    }

    pub fn status(&self) -> ContractResult<ReferenceCatalogStatus> {
        Ok(ReferenceCatalogStatus {
            watermark: self.watermark,
            counts: read_stats(&self.connection)?,
            integrity: read_integrity_stats(&self.connection)?,
        })
    }

    pub fn exchanges(&self, query: &ExchangeCatalogQuery) -> ContractResult<Vec<Exchange>> {
        let mut builder =
            RecordQuery::new("reference_exchanges_current", "exchange_id", &query.page)?;
        builder.ids(query.exchange_ids.as_deref())?;
        builder.search(query.search.as_deref())?;
        builder.filter_text("status", query.status.as_ref());
        builder.active_only(query.active_only);
        read_typed_records(&self.connection, builder)
    }

    pub fn assets(&self, query: &AssetCatalogQuery) -> ContractResult<Vec<Asset>> {
        let mut builder = RecordQuery::new("reference_assets_current", "asset_id", &query.page)?;
        builder.ids(query.asset_ids.as_deref())?;
        builder.search(query.search.as_deref())?;
        builder.filter_text("code", query.code.as_deref());
        builder.filter_text("asset_class", query.asset_class.as_ref());
        builder.filter_text("status", query.status.as_ref());
        builder.active_only(query.active_only);
        read_typed_records(&self.connection, builder)
    }

    pub fn instruments(&self, query: &InstrumentSearchQuery) -> ContractResult<Vec<Instrument>> {
        if query
            .expiry_from_unix_nanos
            .zip(query.expiry_to_unix_nanos)
            .is_some_and(|(from, to)| from > to)
        {
            return Err(ContractError::Invalid(
                "expiry_from_unix_nanos must not exceed expiry_to_unix_nanos".into(),
            ));
        }
        if query
            .option_right
            .as_deref()
            .is_some_and(|value| !matches!(value, "call" | "put"))
        {
            return Err(ContractError::Invalid(
                "option_right must be call or put".into(),
            ));
        }
        let mut builder = RecordQuery::new(
            "reference_instruments_current",
            "instrument_id",
            &query.page,
        )?;
        builder.ids(query.instrument_ids.as_deref())?;
        builder.search(query.search.as_deref())?;
        builder.filter_text("symbol", query.symbol.as_deref());
        builder.filter_text("instrument_type", query.instrument_type.as_ref());
        builder.filter_text("product_family", query.product_family.as_deref());
        builder.filter_text(
            "underlying_instrument_id",
            query.underlying_instrument_id.as_deref(),
        );
        builder.filter_u64("expiry_unix_nanos", query.expiry_unix_nanos)?;
        builder.compare_u64("expiry_unix_nanos >=", query.expiry_from_unix_nanos)?;
        builder.compare_u64("expiry_unix_nanos <=", query.expiry_to_unix_nanos)?;
        if let Some(option_right) = query.option_right.as_deref() {
            builder.clause(
                "json_extract(payload, '$.option_right') = ?",
                Value::Text(option_right.into()),
            );
        }
        builder.filter_text("status", query.status.as_ref());
        builder.active_only(query.active_only);
        if query.underlying_instrument_id.is_some() {
            builder.order_by =
                "expiry_unix_nanos, CAST(json_extract(payload, '$.strike') AS REAL), instrument_id";
        }
        read_typed_records(&self.connection, builder)
    }

    /// Read last-known-good provider availability from the same committed
    /// provider records that Reference promotes into the canonical catalog.
    /// No second mutable availability registry is introduced.
    pub fn instrument_availability(
        &self,
        query: &InstrumentAvailabilityQuery,
    ) -> ContractResult<Vec<ReferenceInstrumentAvailability>> {
        validate_page(&query.page)?;
        let mut sql = String::from(
            "SELECT provider, payload FROM reference_provider_records \
             WHERE record_kind = 'instrument'",
        );
        let mut values = Vec::new();
        push_in_filter(
            &mut sql,
            &mut values,
            "provider",
            query.source_ids.as_deref(),
        )?;
        push_in_filter(
            &mut sql,
            &mut values,
            "record_id",
            query.instrument_ids.as_deref(),
        )?;
        if let Some(search) = query.search.as_deref() {
            let pattern = search_pattern(search)?;
            sql.push_str(
                " AND (LOWER(record_id) LIKE LOWER(?) ESCAPE '\\' \
                 OR LOWER(payload) LIKE LOWER(?) ESCAPE '\\')",
            );
            values.push(Value::Text(pattern.clone()));
            values.push(Value::Text(pattern));
        }
        if let Some(symbol) = query.symbol.as_ref() {
            sql.push_str(" AND json_extract(payload, '$.symbol') = ?");
            values.push(Value::Text(symbol.to_string()));
        }
        if let Some(kind) = query.instrument_type.as_ref() {
            sql.push_str(" AND json_extract(payload, '$.instrument_type') = ?");
            values.push(Value::Text(kind.to_string()));
        }
        if query.active_only {
            sql.push_str(" AND json_extract(payload, '$.status') IN ('active', 'trading')");
        }
        sql.push_str(" ORDER BY provider, record_id");
        match query.page.limit {
            Some(limit) => {
                sql.push_str(" LIMIT ? OFFSET ?");
                values.push(Value::Integer(limit as i64));
            },
            None => sql.push_str(" LIMIT -1 OFFSET ?"),
        }
        values.push(sqlite_integer(query.page.offset, "offset")?);
        let mut statement = self.connection.prepare(&sql).map_err(transport)?;
        let rows = statement
            .query_map(params_from_iter(values), |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })
            .map_err(transport)?;
        rows.map(|row| {
            let (source_id, payload) = row.map_err(transport)?;
            Ok(ReferenceInstrumentAvailability {
                source_id: ReferenceSourceId::new(source_id).map_err(|error| {
                    ContractError::Invalid(format!("invalid Reference source id: {error}"))
                })?,
                instrument: decode_payload(&payload)?,
            })
        })
        .collect()
    }

    pub fn listings(&self, query: &ListingCatalogQuery) -> ContractResult<Vec<Listing>> {
        let mut builder =
            RecordQuery::new("reference_listings_current", "listing_id", &query.page)?;
        builder.ids(query.listing_ids.as_deref())?;
        builder.search(query.search.as_deref())?;
        builder.filter_text("instrument_id", query.instrument_id.as_deref());
        builder.filter_text("exchange_id", query.exchange_id.as_deref());
        builder.filter_text("exchange_symbol", query.exchange_symbol.as_deref());
        builder.filter_text("status", query.status.as_ref());
        builder.active_only(query.active_only);
        read_typed_records(&self.connection, builder)
    }

    pub fn markets(&self, query: &MarketSearchQuery) -> ContractResult<Vec<Market>> {
        let mut builder = RecordQuery::new("reference_markets_current", "market_id", &query.page)?;
        builder.ids(query.market_ids.as_deref())?;
        builder.search(query.search.as_deref())?;
        builder.filter_text("venue_symbol", query.venue_symbol.as_deref());
        builder.filter_text("exchange_id", query.exchange_id.as_deref());
        builder.filter_text("instrument_kind", query.instrument_kind.as_ref());
        builder.filter_text("asset_type", query.asset_type.as_ref());
        builder.filter_text("instrument_id", query.instrument_id.as_deref());
        builder.filter_text("listing_id", query.listing_id.as_deref());
        builder.filter_text(
            "underlying_instrument_id",
            query.underlying_instrument_id.as_deref(),
        );
        builder.filter_text("status", query.status.as_ref());
        builder.active_only(query.active_only);
        if let Some(asset_code) = query.asset_code.as_deref() {
            let code = asset_code.trim().to_uppercase();
            if code.is_empty() {
                return Err(ContractError::Invalid(
                    "asset_code must not be empty".into(),
                ));
            }
            builder.sql.push_str(" AND (instrument_id IN (SELECT instrument_id FROM reference_instruments_current WHERE symbol = ?) OR underlying_instrument_id IN (SELECT instrument_id FROM reference_instruments_current WHERE symbol = ?) OR json_extract(payload, '$.base_asset_id') IN (SELECT asset_id FROM reference_assets_current WHERE code = ?) OR json_extract(payload, '$.quote_asset_id') IN (SELECT asset_id FROM reference_assets_current WHERE code = ?))");
            builder
                .values
                .extend((0..4).map(|_| Value::Text(code.clone())));
        }
        read_typed_records(&self.connection, builder)
    }

    pub fn lifecycle_events(
        &self,
        query: &LifecycleCatalogQuery,
    ) -> ContractResult<Vec<ReferenceLifecycleEvent>> {
        if !(1..=MAX_EVENT_PAGE_SIZE).contains(&query.limit) {
            return Err(ContractError::Invalid(format!(
                "limit must be between 1 and {MAX_EVENT_PAGE_SIZE}"
            )));
        }
        if query
            .sequence_from
            .zip(query.sequence_to)
            .is_some_and(|(from, to)| from > to)
        {
            return Err(ContractError::Invalid(
                "sequence_from must not exceed sequence_to".into(),
            ));
        }
        let mut sql = String::from("SELECT sequence, payload FROM reference_lifecycle WHERE 1 = 1");
        let mut values = Vec::new();
        if let Some(from) = query.sequence_from {
            sql.push_str(" AND sequence >= ?");
            values.push(sqlite_integer(from, "sequence_from")?);
        }
        if let Some(to) = query.sequence_to {
            sql.push_str(" AND sequence <= ?");
            values.push(sqlite_integer(to, "sequence_to")?);
        }
        sql.push_str(" ORDER BY sequence LIMIT ?");
        values.push(Value::Integer(query.limit as i64));
        let mut statement = self.connection.prepare(&sql).map_err(transport)?;
        let rows = statement
            .query_map(params_from_iter(values), |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
            })
            .map_err(transport)?;
        rows.map(|row| {
            let (sequence, payload) = row.map_err(transport)?;
            let mut event: ReferenceLifecycleEvent = decode_payload(&payload)?;
            event.sequence = non_negative(sequence, "lifecycle sequence")?;
            Ok(event)
        })
        .collect()
    }

    pub fn option_coverage(&self) -> ContractResult<ReferenceOptionCoverage> {
        let mut statement = self.connection.prepare(
            "SELECT underlying FROM reference_option_coverage WHERE provider = ? AND enabled = 1 ORDER BY underlying",
        ).map_err(transport)?;
        let rows = statement
            .query_map(["massive-options"], |row| row.get::<_, String>(0))
            .map_err(transport)?;
        Ok(ReferenceOptionCoverage {
            source_id: "massive-options".into(),
            underlyings: rows
                .map(|row| row.map_err(transport))
                .collect::<ContractResult<_>>()?,
        })
    }

    pub fn outbox_depth(&self) -> ContractResult<u64> {
        let count = self
            .connection
            .query_row(
                "SELECT COUNT(*) FROM reference_publication_outbox",
                [],
                |row| row.get::<_, i64>(0),
            )
            .map_err(transport)?;
        non_negative(count, "publication outbox depth")
    }
}

impl Drop for ReferenceReadSession {
    fn drop(&mut self) {
        let _ = self.connection.execute_batch("ROLLBACK");
    }
}

struct RecordQuery<'a> {
    sql: String,
    values: Vec<Value>,
    key: &'a str,
    order_by: &'a str,
    limit: Option<u64>,
    offset: u64,
}

impl<'a> RecordQuery<'a> {
    fn new(table: &str, key: &'a str, page: &ReferencePage) -> ContractResult<Self> {
        validate_page(page)?;
        Ok(Self {
            sql: format!("SELECT payload FROM {table} WHERE 1 = 1"),
            values: Vec::new(),
            key,
            order_by: key,
            limit: page.limit,
            offset: page.offset,
        })
    }

    fn ids<T: ToString>(&mut self, ids: Option<&[T]>) -> ContractResult<()> {
        let Some(ids) = ids else {
            return Ok(());
        };
        if ids.is_empty() {
            self.sql.push_str(" AND 0 = 1");
            return Ok(());
        }
        let ids = ids.iter().map(ToString::to_string).collect::<BTreeSet<_>>();
        if ids.len() > MAX_PAGE_SIZE {
            return Err(ContractError::Invalid(format!(
                "too many identifiers; maximum is {MAX_PAGE_SIZE}"
            )));
        }
        self.sql.push_str(" AND ");
        self.sql.push_str(self.key);
        self.sql.push_str(" IN (");
        self.sql.push_str(&vec!["?"; ids.len()].join(","));
        self.sql.push(')');
        self.values.extend(ids.into_iter().map(Value::Text));
        Ok(())
    }

    fn search(&mut self, search: Option<&str>) -> ContractResult<()> {
        let Some(search) = search else {
            return Ok(());
        };
        let pattern = search_pattern(search)?;
        self.sql.push_str(" AND (LOWER(");
        self.sql.push_str(self.key);
        self.sql
            .push_str(") LIKE LOWER(?) ESCAPE '\\' OR LOWER(payload) LIKE LOWER(?) ESCAPE '\\')");
        self.values.push(Value::Text(pattern.clone()));
        self.values.push(Value::Text(pattern));
        Ok(())
    }

    fn filter_text<T: ToString + ?Sized>(&mut self, column: &str, value: Option<&T>) {
        if let Some(value) = value {
            self.clause(&format!("{column} = ?"), Value::Text(value.to_string()));
        }
    }

    fn filter_u64(&mut self, column: &str, value: Option<u64>) -> ContractResult<()> {
        if let Some(value) = value {
            self.clause(&format!("{column} = ?"), sqlite_integer(value, column)?);
        }
        Ok(())
    }

    fn compare_u64(&mut self, clause: &str, value: Option<u64>) -> ContractResult<()> {
        if let Some(value) = value {
            self.clause(&format!("{clause} ?"), sqlite_integer(value, clause)?);
        }
        Ok(())
    }

    fn active_only(&mut self, active_only: bool) {
        if active_only {
            self.sql.push_str(" AND status IN ('active', 'trading')");
        }
    }

    fn clause(&mut self, clause: &str, value: Value) {
        self.sql.push_str(" AND ");
        self.sql.push_str(clause);
        self.values.push(value);
    }
}

fn validate_page(page: &ReferencePage) -> ContractResult<()> {
    if page
        .limit
        .is_some_and(|limit| !(1..=MAX_PAGE_SIZE as u64).contains(&limit))
    {
        return Err(ContractError::Invalid(format!(
            "limit must be between 1 and {MAX_PAGE_SIZE}"
        )));
    }
    sqlite_integer(page.offset, "offset")?;
    Ok(())
}

fn search_pattern(search: &str) -> ContractResult<String> {
    let search = search.trim();
    if search.is_empty() {
        return Err(ContractError::Invalid(
            "Reference query must not be empty".into(),
        ));
    }
    let escaped = search
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_");
    Ok(format!("%{escaped}%"))
}

fn push_in_filter<T: ToString>(
    sql: &mut String,
    values: &mut Vec<Value>,
    column: &str,
    items: Option<&[T]>,
) -> ContractResult<()> {
    let Some(items) = items else {
        return Ok(());
    };
    if items.is_empty() {
        sql.push_str(" AND 0 = 1");
        return Ok(());
    }
    let items = items
        .iter()
        .map(ToString::to_string)
        .collect::<BTreeSet<_>>();
    if items.len() > MAX_PAGE_SIZE {
        return Err(ContractError::Invalid(format!(
            "too many identifiers; maximum is {MAX_PAGE_SIZE}"
        )));
    }
    sql.push_str(" AND ");
    sql.push_str(column);
    sql.push_str(" IN (");
    sql.push_str(&vec!["?"; items.len()].join(","));
    sql.push(')');
    values.extend(items.into_iter().map(Value::Text));
    Ok(())
}

fn read_typed_records<T: serde::de::DeserializeOwned>(
    connection: &Connection,
    mut query: RecordQuery<'_>,
) -> ContractResult<Vec<T>> {
    query.sql.push_str(" ORDER BY ");
    query.sql.push_str(query.order_by);
    match query.limit {
        Some(limit) => {
            query.sql.push_str(" LIMIT ? OFFSET ?");
            query.values.push(Value::Integer(limit as i64));
        },
        None => query.sql.push_str(" LIMIT -1 OFFSET ?"),
    }
    query.values.push(sqlite_integer(query.offset, "offset")?);
    let mut statement = connection.prepare(&query.sql).map_err(transport)?;
    let rows = statement
        .query_map(params_from_iter(query.values), |row| {
            row.get::<_, String>(0)
        })
        .map_err(transport)?;
    rows.map(|row| decode_payload(&row.map_err(transport)?))
        .collect()
}

fn sqlite_integer(value: u64, label: &str) -> ContractResult<Value> {
    i64::try_from(value)
        .map(Value::Integer)
        .map_err(|_| ContractError::Invalid(format!("Reference {label} is out of range")))
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
    // SQLite may need to create the `-shm` sidecar before it can read a cleanly
    // closed WAL database. `READ_ONLY` rejects that operation on some linked
    // SQLite versions even though no catalog page is being mutated. Open the
    // existing file without CREATE, then enable SQLite's connection-level
    // write prohibition before any schema or data access.
    let read_only = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )
    .and_then(configure_read_connection);
    let connection = match read_only {
        Ok(connection) => return Ok(connection),
        Err(read_only_error) => Connection::open_with_flags(
            path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_URI,
        )
        .and_then(configure_read_connection)
        .map_err(|sidecar_error| {
            transport(format!(
                "read-only open failed ({read_only_error}); WAL sidecar open failed ({sidecar_error})"
            ))
        })?,
    };
    Ok(connection)
}

fn configure_read_connection(connection: Connection) -> rusqlite::Result<Connection> {
    connection.busy_timeout(Duration::from_secs(5))?;
    connection.pragma_update(None, "query_only", true)?;
    // sqlite3_open_v2 may defer the WAL/-shm failure until the first page is
    // read. Probe the schema here so the existing-file READ_WRITE fallback is
    // selected before this connection escapes the boundary.
    connection.query_row("PRAGMA schema_version", [], |_| Ok(()))?;
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
             (SELECT COUNT(*) FROM reference_markets_current WHERE status IN ('active', 'trading')), \
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
    use kairos_primitives::reference::{MarketId, Symbol};

    use super::{
        InstrumentAvailabilityQuery, MarketCatalogQuery, MarketSearchQuery, ReferenceCatalog,
        ReferenceCollection, ReferencePage,
    };

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
    fn read_session_pins_watermark_and_rows_to_one_wal_snapshot() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "PRAGMA journal_mode=WAL;
                 CREATE TABLE reference_meta(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, generation INTEGER NOT NULL, event_sequence INTEGER NOT NULL, committed_at_unix_nanos INTEGER NOT NULL);
                 INSERT INTO reference_meta VALUES(1,6,3,7,11);
                 CREATE TABLE reference_markets_current(market_id TEXT PRIMARY KEY, instrument_id TEXT, listing_id TEXT, exchange_id TEXT, instrument_kind TEXT, asset_type TEXT, underlying_instrument_id TEXT, venue_symbol TEXT, status TEXT, effective_to_unix_nanos INTEGER, payload TEXT);
                 CREATE TABLE reference_instruments_current(instrument_id TEXT PRIMARY KEY, payload TEXT);
                 CREATE TABLE reference_exchanges_current(exchange_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_assets_current(asset_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_listings_current(listing_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
                 CREATE TABLE reference_lifecycle(sequence INTEGER PRIMARY KEY, payload TEXT);
                 CREATE TABLE reference_option_coverage(provider TEXT, underlying TEXT, enabled INTEGER);
                 CREATE TABLE reference_publication_outbox(sequence INTEGER);",
            )
            .unwrap();
        let market = serde_json::json!({
            "market_id": "market:test",
            "instrument_id": "instrument:test",
            "exchange_id": "exchange:test",
            "instrument_kind": "spot",
            "venue_symbol": "OLD",
            "status": "active",
            "price_precision": 2,
            "quantity_precision": 6,
            "effective_from_unix_nanos": 0
        });
        connection
            .execute(
                "INSERT INTO reference_markets_current VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                rusqlite::params![
                    "market:test",
                    "instrument:test",
                    Option::<String>::None,
                    "exchange:test",
                    "spot",
                    Option::<String>::None,
                    Option::<String>::None,
                    "OLD",
                    "active",
                    Option::<i64>::None,
                    market.to_string()
                ],
            )
            .unwrap();
        drop(connection);

        let catalog = ReferenceCatalog::open(&path).unwrap();
        let session = catalog.read_session().unwrap();
        assert_eq!(session.watermark().generation.get(), 3);

        let writer = rusqlite::Connection::open(&path).unwrap();
        let updated = serde_json::json!({
            "market_id": "market:test",
            "instrument_id": "instrument:test",
            "exchange_id": "exchange:test",
            "instrument_kind": "spot",
            "venue_symbol": "NEW",
            "status": "active",
            "price_precision": 2,
            "quantity_precision": 6,
            "effective_from_unix_nanos": 0
        });
        writer.execute_batch("BEGIN IMMEDIATE").unwrap();
        writer
            .execute(
                "UPDATE reference_meta SET generation=4, event_sequence=8 WHERE id=1",
                [],
            )
            .unwrap();
        writer.execute("UPDATE reference_markets_current SET venue_symbol='NEW', payload=? WHERE market_id='market:test'", [updated.to_string()]).unwrap();
        writer.execute_batch("COMMIT").unwrap();

        let rows = session
            .markets(&MarketSearchQuery {
                market_ids: Some(vec![MarketId::new("market:test").unwrap()]),
                page: ReferencePage {
                    limit: Some(2),
                    offset: 0,
                },
                ..Default::default()
            })
            .unwrap();
        assert_eq!(rows[0].venue_symbol.as_ref().unwrap().as_str(), "OLD");
        assert_eq!(session.watermark().generation.get(), 3);

        drop(session);
        assert_eq!(catalog.watermark().unwrap().generation.get(), 4);
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
    fn provider_instrument_availability_is_distinct_from_canonical_listing() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE reference_meta(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, generation INTEGER NOT NULL, event_sequence INTEGER NOT NULL, committed_at_unix_nanos INTEGER NOT NULL);
                 INSERT INTO reference_meta VALUES(1,6,4,9,11);
                 CREATE TABLE reference_provider_records(provider TEXT NOT NULL, record_kind TEXT NOT NULL, record_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(provider, record_kind, record_id));",
            )
            .unwrap();
        let aapl = serde_json::json!({
            "source_id": "binance-equity",
            "instrument_id": "instrument:equity:US:AAPL:common",
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "instrument_type": "equity",
            "status": "active"
        });
        connection
            .execute(
                "INSERT INTO reference_provider_records VALUES(?,?,?,?)",
                rusqlite::params![
                    "binance-equity",
                    "instrument",
                    "instrument:equity:US:AAPL:common",
                    aapl.to_string()
                ],
            )
            .unwrap();
        drop(connection);

        let catalog = ReferenceCatalog::open(&path).unwrap();
        let session = catalog.read_session().unwrap();
        let rows = session
            .instrument_availability(&InstrumentAvailabilityQuery {
                symbol: Some(Symbol::new("AAPL").unwrap()),
                active_only: true,
                page: ReferencePage {
                    limit: Some(20),
                    offset: 0,
                },
                ..Default::default()
            })
            .unwrap();

        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].source_id.as_str(), "binance-equity");
        assert_eq!(rows[0].instrument.symbol.as_str(), "AAPL");
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
