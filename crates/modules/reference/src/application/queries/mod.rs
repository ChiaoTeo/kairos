//! Read-side reference queries.
//!
//! These types are intentionally provider-neutral. Callers can depend on this
//! query contract without knowing whether the catalog came from SQLite, an
//! in-memory test store, or a running reference process.

use kairos_primitives::reference::{ExchangeId, InstrumentKind, MarketId, Symbol};
use kairos_primitives::time::{Sequence, UnixNanos};
use serde::Serialize;

mod model;

pub use model::ReferenceReadModel;

use crate::application::ReferenceApplication;
use crate::domain::{
    Asset, Exchange, Instrument, LifecycleEvent, Listing, Market, ReferenceResult,
};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum ReferenceKind {
    Exchange,
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
    pub exchange_id: Option<ExchangeId>,
    pub instrument_kind: Option<InstrumentKind>,
    pub asset_type: Option<String>,
    pub underlying_instrument_id: Option<String>,
    pub status: Option<String>,
    pub active_only: bool,
    pub as_of_unix_nanos: Option<UnixNanos>,
    pub sequence_from: Option<Sequence>,
    pub sequence_to: Option<Sequence>,
    pub event_time_from_unix_nanos: Option<UnixNanos>,
    pub event_time_to_unix_nanos: Option<UnixNanos>,
    pub record_kind: Option<String>,
    pub limit: Option<usize>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct LifecycleQuery {
    /// Lifecycle sequence numbers are one-based and stable across restarts.
    pub sequence_from: Option<Sequence>,
    pub sequence_to: Option<Sequence>,
    pub event_type: Option<String>,
    pub market_id: Option<MarketId>,
    pub exchange_id: Option<ExchangeId>,
    pub event_time_from_unix_nanos: Option<UnixNanos>,
    pub event_time_to_unix_nanos: Option<UnixNanos>,
    pub limit: Option<usize>,
}

impl LifecycleQuery {
    pub fn matches(&self, sequence: Sequence, event: &LifecycleEvent) -> bool {
        self.sequence_from.is_none_or(|value| sequence >= value)
            && self.sequence_to.is_none_or(|value| sequence <= value)
            && self
                .event_type
                .as_deref()
                .is_none_or(|value| value.eq_ignore_ascii_case(&event.event_type))
            && self
                .market_id
                .as_deref()
                .is_none_or(|value| event.market_id.as_deref() == Some(value))
            && self
                .exchange_id
                .as_ref()
                .is_none_or(|value| event.exchange_id.as_ref() == Some(value))
            && self
                .event_time_from_unix_nanos
                .is_none_or(|value| event.event_time_unix_nanos >= value)
            && self
                .event_time_to_unix_nanos
                .is_none_or(|value| event.event_time_unix_nanos < value)
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", content = "value")]
pub enum ReferenceRecord {
    Exchange(Exchange),
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
    /// Read a bounded lifecycle page from the durable event history. This is
    /// intentionally separate from the immutable current-state read model so
    /// event history cannot force every reader to clone the complete archive.
    pub async fn lifecycle_events_page(
        &mut self,
        sequence_from: Option<u64>,
        sequence_to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.actor
            .lifecycle_events(sequence_from, sequence_to, limit)
            .await
    }

    pub async fn query_lifecycle_events(
        &mut self,
        query: &ReferenceQuery,
    ) -> ReferenceResult<Vec<ReferenceRecord>> {
        let limit = query.limit.unwrap_or(256).clamp(1, 4096);
        let events = self
            .actor
            .lifecycle_events_filtered(
                query.sequence_from.map(Into::into),
                query.sequence_to.map(Into::into),
                query.event_time_from_unix_nanos.map(Into::into),
                query.event_time_to_unix_nanos.map(Into::into),
                limit,
            )
            .await?;
        Ok(events
            .into_iter()
            .filter(|value| {
                query.matches_status(
                    value
                        .current_status
                        .as_ref()
                        .map(|status| status.as_str())
                        .unwrap_or(""),
                ) && query.matches_text(&[
                    &value.event_id,
                    &value.event_type,
                    value.record_kind.as_deref().unwrap_or(""),
                    value.record_id.as_deref().unwrap_or(""),
                    value.market_id.as_deref().unwrap_or(""),
                    value.venue_symbol.as_deref().unwrap_or(""),
                ]) && query
                    .exchange_id
                    .as_ref()
                    .is_none_or(|exchange| value.exchange_id.as_deref() == Some(exchange.as_str()))
                    && query
                        .record_kind
                        .as_deref()
                        .is_none_or(|kind| value.record_kind.as_deref() == Some(kind))
                    && query
                        .event_time_from_unix_nanos
                        .is_none_or(|from| value.event_time_unix_nanos >= from)
                    && query
                        .event_time_to_unix_nanos
                        .is_none_or(|to| value.event_time_unix_nanos < to)
            })
            .take(limit)
            .map(ReferenceRecord::Event)
            .collect())
    }

    /// Read the append-only lifecycle history by stable sequence and time.
    pub async fn lifecycle_events(
        &mut self,
        query: &LifecycleQuery,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        let limit = query.limit.unwrap_or(4096).clamp(1, 1_000_000);
        let events = self
            .actor
            .lifecycle_events(
                query.sequence_from.map(Into::into),
                query.sequence_to.map(Into::into),
                limit,
            )
            .await?;
        Ok(events
            .into_iter()
            .filter(|event| query.matches(event_sequence(event).into(), event))
            .take(limit)
            .collect())
    }

    /// Replay lifecycle events in their persisted sequence order.
    pub async fn replay_lifecycle_events(
        &mut self,
        sequence_from: Option<Sequence>,
        sequence_to: Option<Sequence>,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.lifecycle_events(&LifecycleQuery {
            sequence_from,
            sequence_to,
            ..LifecycleQuery::default()
        })
        .await
    }

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
    pub fn resolve_market(&self, query: &MarketQuery) -> ReferenceResult<Market> {
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
        if include(ReferenceKind::Event) {
            let lifecycle_query = LifecycleQuery {
                sequence_from: query.sequence_from,
                sequence_to: query.sequence_to,
                exchange_id: query.exchange_id.clone(),
                event_time_from_unix_nanos: query.event_time_from_unix_nanos,
                event_time_to_unix_nanos: query.event_time_to_unix_nanos,
                limit: None,
                ..LifecycleQuery::default()
            };
            records.extend(
                self.recent_lifecycle_events(&lifecycle_query)
                    .into_iter()
                    .filter(|value| {
                        query.matches_status(
                            value
                                .current_status
                                .as_ref()
                                .map(|status| status.as_str())
                                .unwrap_or(""),
                        ) && query.matches_text(&[
                            &value.event_id,
                            &value.event_type,
                            value.record_kind.as_deref().unwrap_or(""),
                            value.record_id.as_deref().unwrap_or(""),
                            value.market_id.as_deref().unwrap_or(""),
                            value.venue_symbol.as_deref().unwrap_or(""),
                        ]) && query
                            .record_kind
                            .as_deref()
                            .is_none_or(|kind| value.record_kind.as_deref() == Some(kind))
                    })
                    .map(ReferenceRecord::Event),
            );
        }
        if let Some(limit) = query.limit {
            records.truncate(limit);
        }
        records
    }

    #[cfg(test)]
    pub fn record(&self, identifier: &str) -> ReferenceResult<ReferenceRecord> {
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
        matches.extend(
            self.actor
                .catalog
                .lifecycle_events
                .iter()
                .filter(|value| value.event_id == identifier)
                .cloned()
                .map(ReferenceRecord::Event),
        );
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

    #[cfg(test)]
    fn recent_lifecycle_events(&self, query: &LifecycleQuery) -> Vec<LifecycleEvent> {
        let mut events = self
            .actor
            .catalog
            .lifecycle_events
            .iter()
            .filter(|event| query.matches(event_sequence(event).into(), event))
            .cloned()
            .collect::<Vec<_>>();
        if let Some(limit) = query.limit {
            events.truncate(limit);
        }
        events
    }
}

fn event_sequence(event: &LifecycleEvent) -> u64 {
    event
        .event_id
        .rsplit(':')
        .next()
        .and_then(|value| value.parse().ok())
        .unwrap_or(0)
}
