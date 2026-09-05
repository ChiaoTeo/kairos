//! Local, workspace-backed Reference CLI facade.

use std::path::{Path, PathBuf};

use kairos_reference_contract::{
    Asset, AssetCatalogQuery, Exchange, ExchangeCatalogQuery, Instrument, InstrumentSearchQuery,
    LifecycleEntry, Listing, ListingCatalogQuery, Market, MarketSearchQuery, ReferenceCatalog,
    ReferenceCatalogStats, ReferenceIntegrityStats, ReferencePage,
};
use kairos_workspace::workspace::Workspace;
use serde::Serialize;

use super::{ReferenceKind, ReferenceQuery};

/// Standalone Reference CLI facade.
///
/// This facade reads Reference-owned local catalog evidence for one CLI
/// invocation. It must not connect to the Reference runtime control socket or
/// publish runtime views.
pub struct CliReferenceApplication {
    database: PathBuf,
}

#[derive(Clone, Debug, Serialize)]
pub struct ReferenceCatalogStatusResult {
    pub status: &'static str,
    pub generation: kairos_primitives::time::Generation,
    pub event_sequence: kairos_primitives::time::Sequence,
    pub committed_at_unix_nanos: kairos_primitives::time::UnixNanos,
    pub counts: ReferenceCatalogStats,
    pub integrity: ReferenceIntegrityStats,
    pub note: &'static str,
}

#[derive(Clone, Debug, Serialize)]
#[serde(untagged)]
pub enum ReferenceCatalogRecord {
    Exchange(Exchange),
    Asset(Asset),
    Instrument(Instrument),
    Listing(Listing),
    Market(Market),
    LifecycleEvent(LifecycleEntry),
}

#[derive(Clone, Debug, Serialize)]
#[serde(untagged)]
pub enum ReferenceCliOutput {
    Status(ReferenceCatalogStatusResult),
    Record(ReferenceCatalogRecord),
    Records(Vec<ReferenceCatalogRecord>),
}

#[derive(Default)]
struct ReferenceCatalogRecords {
    exchanges: Vec<Exchange>,
    assets: Vec<Asset>,
    instruments: Vec<Instrument>,
    listings: Vec<Listing>,
    markets: Vec<Market>,
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

    pub fn catalog_status(&self) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
        let reader = ReferenceCatalog::open(&self.database)?;
        let status = reader.status()?;
        Ok(ReferenceCliOutput::Status(ReferenceCatalogStatusResult {
            status: "ready",
            generation: status.watermark.generation,
            event_sequence: status.watermark.event_sequence,
            committed_at_unix_nanos: status.watermark.committed_at_unix_nanos,
            counts: status.counts,
            integrity: status.integrity,
            note: "diagnostic query read from the Reference-owned catalog",
        }))
    }

    fn diagnostic_records(
        &self,
        limit: usize,
    ) -> Result<ReferenceCatalogRecords, Box<dyn std::error::Error>> {
        let reader = ReferenceCatalog::open(&self.database)?;
        let session = reader.read_session()?;
        let page = ReferencePage {
            limit: Some(limit.clamp(1, 10_000) as u64),
            offset: 0,
        };
        Ok(ReferenceCatalogRecords {
            exchanges: session.exchanges(&ExchangeCatalogQuery {
                page: page.clone(),
                ..Default::default()
            })?,
            assets: session.assets(&AssetCatalogQuery {
                page: page.clone(),
                ..Default::default()
            })?,
            instruments: session.instruments(&InstrumentSearchQuery {
                page: page.clone(),
                ..Default::default()
            })?,
            listings: session.listings(&ListingCatalogQuery {
                page: page.clone(),
                ..Default::default()
            })?,
            markets: session.markets(&MarketSearchQuery {
                page,
                ..Default::default()
            })?,
        })
    }

    pub fn catalog_collection(
        &self,
        collection: ReferenceCatalogCollection,
        request: ReferenceCatalogListRequest,
    ) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_records(request.limit)?;
        let records = match collection {
            ReferenceCatalogCollection::Exchanges => snapshot
                .exchanges
                .into_iter()
                .map(ReferenceCatalogRecord::Exchange)
                .collect(),
            ReferenceCatalogCollection::Assets => snapshot
                .assets
                .into_iter()
                .map(ReferenceCatalogRecord::Asset)
                .collect(),
            ReferenceCatalogCollection::Instruments => snapshot
                .instruments
                .into_iter()
                .map(ReferenceCatalogRecord::Instrument)
                .collect(),
            ReferenceCatalogCollection::Listings => snapshot
                .listings
                .into_iter()
                .map(ReferenceCatalogRecord::Listing)
                .collect(),
        };
        Ok(ReferenceCliOutput::Records(filter_catalog_records(
            records, request,
        )))
    }

    pub fn list_assets(
        &self,
        request: ReferenceCatalogListRequest,
    ) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
        self.catalog_collection(ReferenceCatalogCollection::Assets, request)
    }

    pub fn show_asset(
        &self,
        asset_id: &str,
    ) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
        self.show(asset_id)?
            .ok_or_else(|| format!("unknown asset identifier: {asset_id}").into())
    }

    pub fn show_catalog_record(
        &self,
        identifier: &str,
    ) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
        self.show(identifier)?
            .ok_or_else(|| format!("unknown reference identifier: {identifier}").into())
    }

    pub fn markets(
        &self,
        request: ReferenceMarketCatalogRequest,
        resolve: bool,
    ) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_records(request.limit.unwrap_or(256))?;
        let values = filter_markets(snapshot.markets, request);
        if resolve {
            match values.as_slice() {
                [market] => Ok(ReferenceCliOutput::Record(ReferenceCatalogRecord::Market(
                    market.clone(),
                ))),
                [] => Err("reference market was not found".into()),
                _ => Err("reference market query is ambiguous".into()),
            }
        } else {
            Ok(ReferenceCliOutput::Records(
                values
                    .into_iter()
                    .map(ReferenceCatalogRecord::Market)
                    .collect(),
            ))
        }
    }

    pub fn option_chain(
        &self,
        request: ReferenceOptionChainRequest,
    ) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_records(request.limit)?;
        let limit = request.limit.clamp(1, 10_000);
        let mut values = snapshot.instruments;
        values.retain(|value| {
            value.instrument_type == kairos_primitives::reference::InstrumentKind::Option
                && value
                    .underlying_instrument_id
                    .as_ref()
                    .map(|id| id.as_str())
                    == Some(request.underlying_instrument_id.as_str())
                && request
                    .option_right
                    .as_deref()
                    .is_none_or(|right| value.option_right.as_deref() == Some(right))
                && request.expiry_unix_nanos.is_none_or(|expiry| {
                    value.expiry_unix_nanos.map(|value| value.get()) == Some(expiry)
                })
                && request.expiry_from_unix_nanos.is_none_or(|from| {
                    value
                        .expiry_unix_nanos
                        .is_some_and(|expiry| expiry.get() >= from)
                })
                && request.expiry_to_unix_nanos.is_none_or(|to| {
                    value
                        .expiry_unix_nanos
                        .is_some_and(|expiry| expiry.get() < to)
                })
                && (!request.active_only || value.status.as_str() == "active")
        });
        values.truncate(limit);
        Ok(ReferenceCliOutput::Records(
            values
                .into_iter()
                .map(ReferenceCatalogRecord::Instrument)
                .collect(),
        ))
    }

    pub fn query(
        &self,
        kind: ReferenceKind,
        query: ReferenceQuery,
    ) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_records(query.limit.unwrap_or(256))?;
        read_query(&snapshot, kind, query)
    }

    pub fn search(
        &self,
        text: String,
        limit: usize,
    ) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
        self.query(
            ReferenceKind::All,
            ReferenceQuery {
                text: Some(text),
                limit: Some(limit),
                ..ReferenceQuery::default()
            },
        )
    }

    pub fn show(
        &self,
        identifier: &str,
    ) -> Result<Option<ReferenceCliOutput>, Box<dyn std::error::Error>> {
        let snapshot = self.diagnostic_records(10_000)?;
        Ok(find_record(&snapshot, identifier).map(ReferenceCliOutput::Record))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReferenceCatalogCollection {
    Exchanges,
    Assets,
    Instruments,
    Listings,
}

fn read_query(
    snapshot: &ReferenceCatalogRecords,
    kind: ReferenceKind,
    query: ReferenceQuery,
) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
    let collections = snapshot_collections(snapshot, kind);
    let limit = query.limit.unwrap_or(256).clamp(1, 10_000);
    let mut values = Vec::new();
    for records in collections {
        let remaining = limit.saturating_sub(values.len());
        if remaining == 0 {
            break;
        }
        let mut records = records;
        records.retain(|value| value.matches_query(&query));
        values.extend(records);
    }
    values.truncate(limit);
    Ok(ReferenceCliOutput::Records(values))
}

fn snapshot_collections(
    snapshot: &ReferenceCatalogRecords,
    kind: ReferenceKind,
) -> Vec<Vec<ReferenceCatalogRecord>> {
    let mut all = Vec::new();
    macro_rules! include {
        ($kind:ident, $field:ident, $record:ident) => {
            if matches!(kind, ReferenceKind::$kind | ReferenceKind::All) {
                all.push(
                    snapshot
                        .$field
                        .iter()
                        .cloned()
                        .map(ReferenceCatalogRecord::$record)
                        .collect(),
                );
            }
        };
    }
    include!(Exchange, exchanges, Exchange);
    include!(Asset, assets, Asset);
    include!(Instrument, instruments, Instrument);
    include!(Listing, listings, Listing);
    include!(Market, markets, Market);
    all
}

fn filter_catalog_records(
    mut values: Vec<ReferenceCatalogRecord>,
    request: ReferenceCatalogListRequest,
) -> Vec<ReferenceCatalogRecord> {
    values.retain(|value| {
        value.matches_text(request.query.as_deref())
            && request
                .status
                .as_deref()
                .is_none_or(|status| value.status() == Some(status))
            && (!request.active_only || value.status() == Some("active"))
    });
    values.truncate(request.limit);
    values
}

fn filter_markets(mut values: Vec<Market>, request: ReferenceMarketCatalogRequest) -> Vec<Market> {
    let limit = request.limit.unwrap_or(256);
    values.retain(|value| {
        request
            .market_id
            .as_deref()
            .is_none_or(|expected| value.market_id.as_str() == expected)
            && request.symbol.as_deref().is_none_or(|expected| {
                value.venue_symbol.as_ref().map(|value| value.as_str()) == Some(expected)
            })
            && request
                .exchange_id
                .as_deref()
                .is_none_or(|expected| value.exchange_id.as_str() == expected)
            && request
                .instrument_kind
                .as_deref()
                .is_none_or(|expected| value.instrument_kind.as_str() == expected)
            && request.asset_type.as_deref().is_none_or(|expected| {
                value.asset_type.map(|value| value.as_str()) == Some(expected)
            })
            && request
                .status
                .as_deref()
                .is_none_or(|expected| value.status.as_str() == expected)
            && (!request.active_only || value.status.as_str() == "active")
    });
    values.truncate(limit);
    values
}

fn find_record(
    snapshot: &ReferenceCatalogRecords,
    identifier: &str,
) -> Option<ReferenceCatalogRecord> {
    snapshot_collections(snapshot, ReferenceKind::All)
        .into_iter()
        .flatten()
        .find(|value| value.identifier() == identifier)
}

impl ReferenceCatalogRecord {
    fn identifier(&self) -> &str {
        match self {
            Self::Exchange(value) => &value.exchange_id,
            Self::Asset(value) => value.asset_id.as_str(),
            Self::Instrument(value) => value.instrument_id.as_str(),
            Self::Listing(value) => value.listing_id.as_str(),
            Self::Market(value) => value.market_id.as_str(),
            Self::LifecycleEvent(value) => &value.event_id,
        }
    }

    fn status(&self) -> Option<&'static str> {
        match self {
            Self::Exchange(value) => Some(value.status.as_str()),
            Self::Asset(value) => Some(value.status.as_str()),
            Self::Instrument(value) => Some(value.status.as_str()),
            Self::Listing(value) => Some(value.status.as_str()),
            Self::Market(value) => Some(value.status.as_str()),
            Self::LifecycleEvent(_) => None,
        }
    }

    fn matches_text(&self, text: Option<&str>) -> bool {
        let Some(text) = text else { return true };
        let text = text.to_ascii_lowercase();
        self.searchable_fields()
            .into_iter()
            .flatten()
            .any(|value| value.to_ascii_lowercase().contains(&text))
    }

    fn searchable_fields(&self) -> Vec<Option<&str>> {
        match self {
            Self::Exchange(value) => {
                vec![Some(value.exchange_id.as_str()), Some(&value.name)]
            },
            Self::Asset(value) => vec![
                Some(value.asset_id.as_str()),
                Some(value.code.as_str()),
                value.name.as_deref(),
                Some(value.asset_class.as_str()),
            ],
            Self::Instrument(value) => vec![
                Some(value.instrument_id.as_str()),
                Some(value.symbol.as_str()),
                value.name.as_deref(),
                Some(value.instrument_type.as_str()),
                value
                    .underlying_instrument_id
                    .as_ref()
                    .map(|id| id.as_str()),
            ],
            Self::Listing(value) => vec![
                Some(value.listing_id.as_str()),
                Some(value.instrument_id.as_str()),
                Some(value.exchange_id.as_str()),
                Some(value.exchange_symbol.as_str()),
            ],
            Self::Market(value) => vec![
                Some(value.market_id.as_str()),
                Some(value.instrument_id.as_str()),
                Some(value.exchange_id.as_str()),
                value.venue_symbol.as_ref().map(|symbol| symbol.as_str()),
                Some(value.instrument_kind.as_str()),
            ],
            Self::LifecycleEvent(value) => vec![
                Some(&value.event_id),
                Some(&value.event_type),
                value.record_kind.as_deref(),
                value.record_id.as_deref(),
            ],
        }
    }

    fn matches_query(&self, query: &ReferenceQuery) -> bool {
        self.matches_text(query.text.as_deref())
            && query
                .status
                .as_deref()
                .is_none_or(|status| self.status() == Some(status))
            && (!query.active_only || self.status() == Some("active"))
            && query
                .exchange_id
                .as_ref()
                .is_none_or(|exchange| match self {
                    Self::Listing(value) => &value.exchange_id == exchange,
                    Self::Market(value) => &value.exchange_id == exchange,
                    _ => false,
                })
            && query.instrument_kind.is_none_or(|kind| match self {
                Self::Instrument(value) => value.instrument_type == kind,
                Self::Market(value) => value.instrument_kind == kind,
                _ => false,
            })
            && query
                .underlying_instrument_id
                .as_deref()
                .is_none_or(|underlying| match self {
                    Self::Instrument(value) => value
                        .underlying_instrument_id
                        .as_ref()
                        .is_some_and(|id| id.as_str() == underlying),
                    Self::Market(value) => value
                        .underlying_instrument_id
                        .as_ref()
                        .is_some_and(|id| id.as_str() == underlying),
                    _ => false,
                })
    }
}
