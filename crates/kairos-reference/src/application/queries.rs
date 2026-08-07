//! Read-side reference queries.
//!
//! These types are intentionally provider-neutral. Callers can depend on this
//! query contract without knowing whether the catalog came from SQLite, an
//! in-memory test store, or a running reference process.

use serde::Serialize;

use crate::domain::{Asset, Entity, Instrument, LifecycleEvent, Listing, Market, ReferenceResult};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum ReferenceKind {
    Entity,
    Asset,
    Instrument,
    Listing,
    Market,
    Event,
    #[default]
    All,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceQuery {
    pub text: Option<String>,
    pub kind: ReferenceKind,
    pub venue_id: Option<String>,
    pub market_type: Option<String>,
    pub status: Option<String>,
    pub active_only: bool,
    pub as_of_unix_nanos: Option<u64>,
    pub limit: Option<usize>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", content = "value")]
pub enum ReferenceRecord {
    Entity(Entity),
    Asset(Asset),
    Instrument(Instrument),
    Listing(Listing),
    Market(Market),
    Event(LifecycleEvent),
}

impl ReferenceQuery {
    pub fn matches_text(&self, values: &[&str]) -> bool {
        self.text.as_deref().is_none_or(|needle| {
            let needle = needle.to_ascii_lowercase();
            values
                .iter()
                .any(|value| value.to_ascii_lowercase().contains(&needle))
        })
    }

    pub fn matches_status(&self, status: &str) -> bool {
        self.status
            .as_deref()
            .is_none_or(|expected| expected.eq_ignore_ascii_case(status))
            && (!self.active_only || matches!(status, "active" | "trading"))
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MarketQuery {
    pub market_id: Option<String>,
    pub venue_id: Option<String>,
    pub market_type: Option<String>,
    pub source_symbol: Option<String>,
    pub active_only: bool,
    pub as_of_unix_nanos: Option<u64>,
    pub status: Option<String>,
}

impl MarketQuery {
    pub fn by_symbol(symbol: impl Into<String>) -> Self {
        Self {
            source_symbol: Some(symbol.into()),
            ..Self::default()
        }
    }

    pub fn matches(&self, market: &Market) -> bool {
        if self
            .market_id
            .as_deref()
            .is_some_and(|value| value != market.market_id)
            || self
                .venue_id
                .as_deref()
                .is_some_and(|value| value != market.venue_id)
            || self
                .market_type
                .as_deref()
                .is_some_and(|value| value != market.market_type)
            || self
                .source_symbol
                .as_deref()
                .is_some_and(|value| !value.eq_ignore_ascii_case(&market.source_symbol))
            || self
                .status
                .as_deref()
                .is_some_and(|value| !value.eq_ignore_ascii_case(&market.status))
        {
            return false;
        }
        if self.active_only && !is_active(market) {
            return false;
        }
        if let Some(as_of) = self.as_of_unix_nanos {
            if market.effective_from_unix_nanos > as_of
                || market
                    .effective_to_unix_nanos
                    .is_some_and(|end| as_of >= end)
            {
                return false;
            }
        }
        true
    }

    pub(crate) fn not_found_message(&self) -> String {
        format!("no reference market matches {self:?}")
    }

    pub(crate) fn ambiguous_message(&self) -> String {
        format!("reference market query is ambiguous: {self:?}")
    }
}

pub trait ReferenceReader {
    fn markets(&self, query: &MarketQuery) -> Vec<Market>;
    fn resolve_market(&self, query: &MarketQuery) -> ReferenceResult<Market>;
}

fn is_active(market: &Market) -> bool {
    matches!(market.status.as_str(), "active" | "trading")
}
