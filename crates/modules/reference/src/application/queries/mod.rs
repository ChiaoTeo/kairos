//! Read-side reference queries.
//!
//! These types are intentionally provider-neutral. Callers can depend on this
//! query contract without knowing whether the catalog came from SQLite, an
//! in-memory test store, or a running reference process.

use kairos_primitives::reference::{ExchangeId, InstrumentKind, MarketId, Symbol};
use kairos_primitives::time::UnixNanos;
use serde::Serialize;

mod model;

pub use model::ReferenceReadModel;

use crate::application::ReferenceApplication;
use crate::domain::{Asset, Exchange, Instrument, Listing, Market};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum ReferenceKind {
    Exchange,
    Asset,
    Instrument,
    Listing,
    Market,
    #[default]
    All,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceQuery {
    pub text: Option<String>,
    pub kind: ReferenceKind,
    pub exchange_id: Option<ExchangeId>,
    pub instrument_kind: Option<InstrumentKind>,
    pub asset_type: Option<String>,
    pub underlying_instrument_id: Option<String>,
    pub status: Option<String>,
    pub active_only: bool,
    pub as_of_unix_nanos: Option<UnixNanos>,
    pub limit: Option<usize>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", content = "value")]
pub enum ReferenceRecord {
    Exchange(Exchange),
    Asset(Asset),
    Instrument(Instrument),
    Listing(Listing),
    Market(Market),
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
    pub market_id: Option<MarketId>,
    pub exchange_id: Option<ExchangeId>,
    pub instrument_kind: Option<InstrumentKind>,
    pub asset_type: Option<String>,
    pub venue_symbol: Option<Symbol>,
    pub active_only: bool,
    pub as_of_unix_nanos: Option<UnixNanos>,
    pub status: Option<String>,
}

impl MarketQuery {
    pub fn by_symbol(symbol: impl Into<String>) -> Self {
        Self {
            venue_symbol: Some(Symbol::new(symbol).expect("market query symbol is required")),
            ..Self::default()
        }
    }

    pub fn matches(&self, market: &Market) -> bool {
        if self
            .market_id
            .as_deref()
            .is_some_and(|value| value != market.market_id.as_str())
            || self
                .exchange_id
                .as_ref()
                .is_some_and(|value| value != &market.exchange_id)
            || self
                .instrument_kind
                .is_some_and(|value| value != market.instrument_kind)
            || self
                .asset_type
                .as_deref()
                .is_some_and(|value| market.asset_type.map(|class| class.as_str()) != Some(value))
            || self.venue_symbol.as_ref().is_some_and(|value| {
                market
                    .venue_symbol
                    .as_ref()
                    .is_none_or(|symbol| !value.as_str().eq_ignore_ascii_case(symbol.as_str()))
            })
            || self
                .status
                .as_deref()
                .is_some_and(|value| !value.eq_ignore_ascii_case(market.status.as_str()))
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

    #[cfg(test)]
    pub(crate) fn not_found_message(&self) -> String {
        format!("no reference market matches {self:?}")
    }

    #[cfg(test)]
    pub(crate) fn ambiguous_message(&self) -> String {
        format!("reference market query is ambiguous: {self:?}")
    }
}

fn is_active(market: &Market) -> bool {
    matches!(market.status.as_str(), "active" | "trading")
}

impl ReferenceApplication {
    /// Read the current catalog for diagnostics and controlled snapshots.
    ///
    /// The returned reference is read-only; mutation remains owned by this
    /// application instance and its actor.
    #[cfg(test)]
    pub fn catalog(&self) -> &crate::domain::ReferenceCatalog {
        &self.actor.catalog
    }

    #[cfg(test)]
    pub fn markets(&self, query: &MarketQuery) -> Vec<Market> {
        self.actor
            .catalog
            .markets
            .values()
            .filter(|market| query.matches(market))
            .cloned()
            .collect()
    }

    #[cfg(test)]
    pub fn resolve_market(&self, query: &MarketQuery) -> crate::domain::ReferenceResult<Market> {
        let markets = self.markets(query);
        match markets.as_slice() {
            [market] => Ok(market.clone()),
            [] => Err(crate::domain::ReferenceError::Invalid(
                query.not_found_message(),
            )),
            _ => Err(crate::domain::ReferenceError::Invalid(
                query.ambiguous_message(),
            )),
        }
    }

    /// Execute the complete read-side catalog query used by verification tests.
    /// The application owns filtering so server and CLI cannot drift.
    #[cfg(test)]
    pub fn query(&self, query: &ReferenceQuery) -> Vec<ReferenceRecord> {
        let mut records = Vec::new();
        let include = |kind: ReferenceKind| query.kind == ReferenceKind::All || query.kind == kind;
        if include(ReferenceKind::Exchange) {
            records.extend(
                self.actor
                    .catalog
                    .exchanges
                    .values()
                    .filter(|value| {
                        query.matches_status(value.status.as_str())
                            && query.matches_text(&[value.exchange_id.as_str(), &value.name])
                            && query
                                .exchange_id
                                .as_deref()
                                .is_none_or(|exchange| value.exchange_id == exchange)
                    })
                    .cloned()
                    .map(ReferenceRecord::Exchange),
            );
        }
        if include(ReferenceKind::Asset) {
            records.extend(
                self.actor
                    .catalog
                    .assets
                    .values()
                    .filter(|value| {
                        query.matches_status(value.status.as_str())
                            && query.matches_text(&[
                                &value.asset_id,
                                &value.code,
                                value.name.as_deref().unwrap_or(""),
                            ])
                    })
                    .cloned()
                    .map(ReferenceRecord::Asset),
            );
        }
        if include(ReferenceKind::Instrument) {
            records.extend(
                self.actor
                    .catalog
                    .instruments
                    .values()
                    .filter(|value| {
                        query.matches_status(value.status.as_str())
                            && query.matches_text(&[
                                &value.instrument_id,
                                &value.symbol,
                                value.name.as_deref().unwrap_or(""),
                                value.instrument_type.as_str(),
                            ])
                            && query.underlying_instrument_id.as_deref().is_none_or(|id| {
                                value.underlying_instrument_id.as_deref() == Some(id)
                            })
                    })
                    .cloned()
                    .map(ReferenceRecord::Instrument),
            );
        }
        if include(ReferenceKind::Listing) {
            records.extend(
                self.actor
                    .catalog
                    .listings
                    .values()
                    .filter(|value| {
                        query.matches_status(value.status.as_str())
                            && query
                                .exchange_id
                                .as_deref()
                                .is_none_or(|exchange| exchange == value.exchange_id.as_str())
                            && query.as_of_unix_nanos.is_none_or(|at| {
                                value.effective_from_unix_nanos <= at
                                    && value.effective_to_unix_nanos.is_none_or(|end| at < end)
                            })
                            && query.matches_text(&[
                                &value.listing_id,
                                &value.instrument_id,
                                &value.exchange_symbol,
                                value.exchange_id.as_str(),
                            ])
                    })
                    .cloned()
                    .map(ReferenceRecord::Listing),
            );
        }
        if include(ReferenceKind::Market) {
            let market_query = MarketQuery {
                exchange_id: query.exchange_id.clone(),
                instrument_kind: query.instrument_kind,
                asset_type: query.asset_type.clone(),
                venue_symbol: query
                    .text
                    .as_deref()
                    .and_then(|value| Symbol::new(value).ok()),
                active_only: query.active_only,
                as_of_unix_nanos: query.as_of_unix_nanos,
                status: query.status.clone(),
                ..MarketQuery::default()
            };
            records.extend(
                self.markets(&market_query)
                    .into_iter()
                    .filter(|market| {
                        query
                            .underlying_instrument_id
                            .as_deref()
                            .is_none_or(|underlying| {
                                self.actor
                                    .catalog
                                    .markets
                                    .get(&market.market_id)
                                    .and_then(|value| value.underlying_instrument_id.as_deref())
                                    == Some(underlying)
                            })
                    })
                    .map(ReferenceRecord::Market),
            );
        }
        if let Some(limit) = query.limit {
            records.truncate(limit);
        }
        records
    }

    #[cfg(test)]
    pub fn record(&self, identifier: &str) -> crate::domain::ReferenceResult<ReferenceRecord> {
        let mut matches = Vec::new();
        if let Some(value) = self.actor.catalog.exchanges.get(identifier) {
            matches.push(ReferenceRecord::Exchange(value.clone()));
        }
        if let Some(value) = self.actor.catalog.assets.get(identifier) {
            matches.push(ReferenceRecord::Asset(value.clone()));
        }
        if let Some(value) = self.actor.catalog.instruments.get(identifier) {
            matches.push(ReferenceRecord::Instrument(value.clone()));
        }
        if let Some(value) = self.actor.catalog.listings.get(identifier) {
            matches.push(ReferenceRecord::Listing(value.clone()));
        }
        if let Some(value) = self.actor.catalog.markets.get(identifier) {
            matches.push(ReferenceRecord::Market(value.clone()));
        }
        match matches.as_slice() {
            [record] => Ok(record.clone()),
            [] => Err(crate::domain::ReferenceError::Invalid(format!(
                "unknown reference identifier: {identifier}"
            ))),
            _ => Err(crate::domain::ReferenceError::Invalid(format!(
                "reference identifier is ambiguous: {identifier}"
            ))),
        }
    }
}
