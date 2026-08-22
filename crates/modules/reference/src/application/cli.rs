use std::path::{Path, PathBuf};

use kairos_reference_contract::{
    ReferenceCollection, ReferenceProjectionSnapshot, ReferenceSqliteReader,
};
use kairos_workspace::workspace::Workspace;
use serde_json::{Value, json};

use super::{ReferenceKind, ReferenceQuery};

/// Standalone Reference CLI facade.
///
/// This facade reads Reference-owned local catalog evidence for one CLI
/// invocation. It must not connect to the Reference runtime control socket or
/// publish runtime projections.
pub struct CliReferenceApplication {
    database: PathBuf,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceCatalogListRequest {
    pub query: Option<String>,
    pub status: Option<String>,
    pub active_only: bool,
    pub limit: usize,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceMarketCatalogRequest {
    pub symbol: Option<String>,
    pub market_id: Option<String>,
    pub exchange_id: Option<String>,
    pub instrument_kind: Option<String>,
    pub asset_type: Option<String>,
    pub status: Option<String>,
    pub limit: Option<usize>,
    pub active_only: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceOptionChainRequest {
    pub underlying_instrument_id: String,
    pub expiry_unix_nanos: Option<u64>,
    pub expiry_from_unix_nanos: Option<u64>,
    pub expiry_to_unix_nanos: Option<u64>,
    pub option_right: Option<String>,
    pub active_only: bool,
    pub limit: usize,
}

impl CliReferenceApplication {
    pub fn open(workspace: &Workspace) -> Result<Self, Box<dyn std::error::Error>> {
        Ok(Self {
            database: workspace.child(&["state", "reference", "reference.sqlite"])?,
        })
    }

    pub fn open_database(database: impl AsRef<Path>) -> Self {
        Self {
            database: database.as_ref().to_path_buf(),
        }
    }

    pub fn catalog_status(&self) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let reader = ReferenceSqliteReader::open(&self.database)?;
        let status = reader.status()?;
        Ok(serde_json::json!({
            "status": "ready",
            "generation": status.watermark.generation,
            "event_sequence": status.watermark.event_sequence,
            "committed_at_unix_nanos": status.watermark.committed_at_unix_nanos,
            "counts": status.counts,
            "integrity": status.integrity,
            "note": "diagnostic query read from the Reference-owned catalog",
        }))
    }

    pub fn diagnostic_snapshot(
        &self,
    ) -> Result<ReferenceProjectionSnapshot, Box<dyn std::error::Error>> {
        let reader = ReferenceSqliteReader::open(&self.database)?;
        fn records<T: serde::de::DeserializeOwned>(
            reader: &ReferenceSqliteReader,
            collection: ReferenceCollection,
        ) -> Result<Vec<T>, Box<dyn std::error::Error>> {
            reader
                .records(collection, 10_000)?
                .into_iter()
                .map(|value| serde_json::from_value(value).map_err(Into::into))
                .collect()
        }

        let watermark = reader.watermark()?;
        Ok(ReferenceProjectionSnapshot {
            generation: watermark.generation,
            event_sequence: watermark.event_sequence,
            entities: records(&reader, ReferenceCollection::Entities)?,
            assets: records(&reader, ReferenceCollection::Assets)?,
            instruments: records(&reader, ReferenceCollection::Instruments)?,
            listings: records(&reader, ReferenceCollection::Listings)?,
            markets: records(&reader, ReferenceCollection::Markets)?,
            lifecycle_events: records(&reader, ReferenceCollection::LifecycleEvents)?,
            ..Default::default()
        })
    }

    pub fn catalog_collection(
        &self,
        collection: ReferenceCatalogCollection,
        request: ReferenceCatalogListRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_snapshot()?;
        let values = match collection {
            ReferenceCatalogCollection::Entities => json_records(&snapshot.entities)?,
            ReferenceCatalogCollection::Assets => json_records(&snapshot.assets)?,
            ReferenceCatalogCollection::Instruments => json_records(&snapshot.instruments)?,
            ReferenceCatalogCollection::Listings => json_records(&snapshot.listings)?,
        };
        Ok(json!(filter_catalog_records(values, request)))
    }

    pub fn list_assets(
        &self,
        request: ReferenceCatalogListRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        self.catalog_collection(ReferenceCatalogCollection::Assets, request)
    }

    pub fn show_asset(&self, asset_id: &str) -> Result<Value, Box<dyn std::error::Error>> {
        self.show(asset_id)?
            .ok_or_else(|| format!("unknown asset identifier: {asset_id}").into())
    }

    pub fn show_catalog_record(
        &self,
        identifier: &str,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        self.show(identifier)?
            .ok_or_else(|| format!("unknown reference identifier: {identifier}").into())
    }

    pub fn participant_entities(
        &self,
        entity_type: &str,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_snapshot()?;
        let mut values = json_records(&snapshot.entities)?;
        values
            .retain(|value| value.get("entity_type").and_then(Value::as_str) == Some(entity_type));
        Ok(json!(values))
    }

    pub fn markets(
        &self,
        request: ReferenceMarketCatalogRequest,
        resolve: bool,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_snapshot()?;
        let mut values = filter_markets(json_records(&snapshot.markets)?, request);
        if resolve {
            match values.as_slice() {
                [market] => Ok(json!(market)),
                [] => Err("reference market was not found".into()),
                _ => Err("reference market query is ambiguous".into()),
            }
        } else {
            Ok(json!(values))
        }
    }

    pub fn option_chain(
        &self,
        request: ReferenceOptionChainRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_snapshot()?;
        let limit = request.limit.clamp(1, 10_000);
        let mut values = json_records(&snapshot.instruments)?;
        values.retain(|value| {
            is_instrument_kind(value, "option")
                && matches_field(
                    value,
                    "underlying_instrument_id",
                    Some(request.underlying_instrument_id.as_str()),
                )
                && matches_field(value, "option_right", request.option_right.as_deref())
                && matches_u64_field(value, "expiry_unix_nanos", request.expiry_unix_nanos)
                && request.expiry_from_unix_nanos.is_none_or(|from| {
                    u64_field(value, "expiry_unix_nanos").is_some_and(|expiry| expiry >= from)
                })
                && request.expiry_to_unix_nanos.is_none_or(|to| {
                    u64_field(value, "expiry_unix_nanos").is_some_and(|expiry| expiry < to)
                })
                && (!request.active_only
                    || value.get("status").and_then(Value::as_str) == Some("active"))
        });
        values.truncate(limit);
        Ok(json!(values))
    }

    pub fn lifecycle_events(
        &self,
        query: ReferenceQuery,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_snapshot()?;
        let from = query
            .sequence_from
            .map(|value| value.get())
            .unwrap_or(1)
            .saturating_sub(1);
        let mut events = json_records(&snapshot.lifecycle_events)?;
        events.retain(|event| {
            let sequence = event
                .get("event_id")
                .and_then(Value::as_str)
                .and_then(|value| value.rsplit(':').next())
                .and_then(|value| value.parse::<u64>().ok());
            sequence.is_some_and(|sequence| sequence > from)
                && query
                    .sequence_to
                    .is_none_or(|to| sequence.is_some_and(|sequence| sequence <= to.get()))
        });
        events.truncate(query.limit.unwrap_or(256));
        Ok(json!(events))
    }

    pub fn query(
        &self,
        kind: ReferenceKind,
        query: ReferenceQuery,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_snapshot()?;
        read_query(&snapshot, kind, query)
    }

    pub fn search(&self, text: String, limit: usize) -> Result<Value, Box<dyn std::error::Error>> {
        self.query(
            ReferenceKind::All,
            ReferenceQuery {
                text: Some(text),
                limit: Some(limit),
                ..ReferenceQuery::default()
            },
        )
    }

    pub fn show(&self, identifier: &str) -> Result<Option<Value>, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_snapshot()?;
        Ok(find_record(&snapshot, identifier)?)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReferenceCatalogCollection {
    Entities,
    Assets,
    Instruments,
    Listings,
}

fn read_query(
    snapshot: &ReferenceProjectionSnapshot,
    kind: ReferenceKind,
    query: ReferenceQuery,
) -> Result<Value, Box<dyn std::error::Error>> {
    let collections = snapshot_collections(snapshot, kind)?;
    let limit = query.limit.unwrap_or(256).clamp(1, 10_000);
    let mut values = Vec::new();
    for records in collections {
        let remaining = limit.saturating_sub(values.len());
        if remaining == 0 {
            break;
        }
        let mut records = records;
        records.retain(|value| {
            matches_json(value, query.text.as_deref())
                && matches_field(value, "status", query.status.as_deref())
                && matches_field(
                    value,
                    "exchange_id",
                    query.exchange_id.as_ref().map(|value| value.as_str()),
                )
                && matches_field(
                    value,
                    "instrument_kind",
                    query.instrument_kind.map(|value| value.as_str()),
                )
                && matches_field(
                    value,
                    "underlying_instrument_id",
                    query.underlying_instrument_id.as_deref(),
                )
                && (!query.active_only
                    || value.get("status").and_then(Value::as_str) == Some("active"))
        });
        values.extend(records);
    }
    values.truncate(limit);
    Ok(json!(values))
}

fn snapshot_collections(
    snapshot: &ReferenceProjectionSnapshot,
    kind: ReferenceKind,
) -> Result<Vec<Vec<Value>>, serde_json::Error> {
    let mut all = Vec::new();
    macro_rules! include {
        ($variant:ident, $field:ident) => {
            if matches!(kind, ReferenceKind::$variant | ReferenceKind::All) {
                all.push(json_records(&snapshot.$field)?);
            }
        };
    }
    include!(Entity, entities);
    include!(Asset, assets);
    include!(Instrument, instruments);
    include!(Listing, listings);
    include!(Market, markets);
    include!(Event, lifecycle_events);
    Ok(all)
}

fn json_records<T: serde::Serialize>(records: &[T]) -> Result<Vec<Value>, serde_json::Error> {
    records.iter().map(serde_json::to_value).collect()
}

fn filter_catalog_records(
    mut values: Vec<Value>,
    request: ReferenceCatalogListRequest,
) -> Vec<Value> {
    values.retain(|value| {
        matches_json(value, request.query.as_deref())
            && matches_field(value, "status", request.status.as_deref())
            && (!request.active_only
                || value.get("status").and_then(Value::as_str) == Some("active"))
    });
    values.truncate(request.limit);
    values
}

fn filter_markets(mut values: Vec<Value>, request: ReferenceMarketCatalogRequest) -> Vec<Value> {
    let limit = request.limit.unwrap_or(256);
    values.retain(|value| {
        request
            .market_id
            .as_deref()
            .is_none_or(|expected| value.get("market_id").and_then(Value::as_str) == Some(expected))
            && request.symbol.as_deref().is_none_or(|expected| {
                value.get("venue_symbol").and_then(Value::as_str) == Some(expected)
            })
            && request.exchange_id.as_deref().is_none_or(|expected| {
                value.get("exchange_id").and_then(Value::as_str) == Some(expected)
            })
            && request.instrument_kind.as_deref().is_none_or(|expected| {
                value.get("instrument_kind").and_then(Value::as_str) == Some(expected)
            })
            && request.asset_type.as_deref().is_none_or(|expected| {
                value.get("asset_type").and_then(Value::as_str) == Some(expected)
            })
            && request.status.as_deref().is_none_or(|expected| {
                value.get("status").and_then(Value::as_str) == Some(expected)
            })
            && (!request.active_only
                || value.get("status").and_then(Value::as_str) == Some("active"))
    });
    values.truncate(limit);
    values
}

fn find_record(
    snapshot: &ReferenceProjectionSnapshot,
    identifier: &str,
) -> Result<Option<Value>, serde_json::Error> {
    Ok(snapshot_collections(snapshot, ReferenceKind::All)?
        .into_iter()
        .flatten()
        .find(|value| {
            [
                "entity_id",
                "asset_id",
                "instrument_id",
                "listing_id",
                "market_id",
                "product_id",
                "access_id",
                "event_id",
            ]
            .into_iter()
            .any(|field| value.get(field).and_then(Value::as_str) == Some(identifier))
        }))
}

fn matches_json(value: &Value, text: Option<&str>) -> bool {
    text.is_none_or(|text| {
        value
            .to_string()
            .to_ascii_lowercase()
            .contains(&text.to_ascii_lowercase())
    })
}

fn matches_field(value: &Value, field: &str, expected: Option<&str>) -> bool {
    expected.is_none_or(|expected| value.get(field).and_then(Value::as_str) == Some(expected))
}

fn is_instrument_kind(value: &Value, expected: &str) -> bool {
    value
        .get("instrument_type")
        .and_then(Value::as_str)
        .is_some_and(|kind| kind.eq_ignore_ascii_case(expected))
}

fn u64_field(value: &Value, field: &str) -> Option<u64> {
    value.get(field).and_then(Value::as_u64).or_else(|| {
        value
            .get(field)
            .and_then(Value::as_str)
            .and_then(|value| value.parse().ok())
    })
}

fn matches_u64_field(value: &Value, field: &str, expected: Option<u64>) -> bool {
    expected.is_none_or(|expected| u64_field(value, field) == Some(expected))
}
