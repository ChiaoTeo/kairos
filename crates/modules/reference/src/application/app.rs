//! Public reference use-case facade.
//!
//! The facade owns the reference actor but does not expose it.  Callers use
//! refresh/publish commands or read-only market queries; persistence,
//! provider connections and publication transports stay behind private services.

use crate::domain::LifecycleEvent;
use crate::domain::ProviderHealth;
use crate::domain::{Asset, Instrument, Listing, ReferenceResult};
#[cfg(test)]
use crate::domain::{Market, ReferenceError};

use crate::application::queries::{LifecycleQuery, ReferenceQuery, ReferenceRecord};
#[cfg(test)]
use crate::application::queries::{MarketQuery, ReferenceKind};
use crate::services::actor::ReferenceActor;
use crate::services::providers::ReferenceSource;
use crate::services::store::CatalogStore;
use kairos_primitives::{Generation, Sequence};
use tracing::{info, warn};

/// Public application boundary for reference data.
pub struct ReferenceApplication<S, C> {
    actor: ReferenceActor<S, C>,
}

/// Immutable read-side view. It contains no provider, SQLite connection, or
/// mutable actor state and can therefore be shared with control/read handlers
/// without serializing them behind the reconcile writer.
#[derive(Clone)]
pub struct ReferenceReadModel {
    actor_id: String,
    source_id: String,
    generation: Generation,
    event_sequence: Sequence,
    market_count: usize,
    provider_health: Vec<ProviderHealth>,
    outbox_depth: usize,
}

/// Complete immutable state used by the typed mmap publisher. This is an
/// application-owned read model, not a persistence record and not a second
/// mutable owner.
#[derive(Clone)]
pub struct ReferenceCurrentView {
    pub actor_id: String,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub catalog: crate::domain::ReferenceCatalog,
    pub provider_health: Vec<ProviderHealth>,
    pub option_underlyings: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceRefreshResult {
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub changed: bool,
    pub change_count: usize,
    pub events: Vec<LifecycleEvent>,
}

#[allow(private_bounds)]
impl<S, C> ReferenceApplication<S, C>
where
    S: ReferenceSource,
    C: CatalogStore,
{
    pub(crate) async fn new(
        actor_id: impl Into<String>,
        source: S,
        store: C,
    ) -> ReferenceResult<Self> {
        Ok(Self {
            actor: ReferenceActor::new(actor_id, source, store).await?,
        })
    }

    pub fn actor_id(&self) -> &str {
        &self.actor.actor_id
    }

    pub async fn read_model(&mut self) -> ReferenceReadModel {
        ReferenceReadModel {
            actor_id: self.actor_id().to_owned(),
            source_id: self.source_id().to_owned(),
            generation: self.actor.metadata.generation,
            event_sequence: self.actor.metadata.event_sequence,
            market_count: self.actor.metadata.market_count,
            provider_health: self.provider_health(),
            outbox_depth: self.actor.pending_event_count().await.unwrap_or(0),
        }
    }

    pub async fn current_view(&mut self) -> ReferenceResult<ReferenceCurrentView> {
        Ok(ReferenceCurrentView {
            actor_id: self.actor_id().to_owned(),
            generation: self.actor.metadata.generation,
            event_sequence: self.actor.metadata.event_sequence,
            catalog: self.actor.current_catalog().await?,
            provider_health: self.provider_health(),
            option_underlyings: self.option_underlyings(),
        })
    }

    pub fn source_id(&self) -> &str {
        self.actor.source_id()
    }

    pub fn provider_health(&self) -> Vec<ProviderHealth> {
        self.actor.provider_health()
    }

    pub fn generation(&self) -> Generation {
        self.actor.metadata.generation
    }

    pub fn event_sequence(&self) -> Sequence {
        self.actor.metadata.event_sequence
    }

    pub fn market_count(&self) -> usize {
        self.actor.metadata.market_count
    }

    pub async fn set_source_paused(
        &mut self,
        source_id: &str,
        paused: bool,
    ) -> ReferenceResult<()> {
        self.actor.set_source_paused(source_id, paused).await
    }

    pub fn option_underlyings(&self) -> Vec<String> {
        self.actor.option_underlyings()
    }

    pub async fn set_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        let result = self
            .actor
            .set_option_underlying(underlying, enabled)
            .await?;
        Ok(ReferenceRefreshResult {
            generation: result.generation,
            event_sequence: result.event_sequence,
            changed: result.changed,
            change_count: result.event_count,
            events: result.events,
        })
    }

    /// Refresh provider data, reconcile lifecycle changes, persist and publish.
    pub async fn refresh(&mut self) -> ReferenceResult<ReferenceRefreshResult> {
        self.refresh_inner(None).await
    }

    /// Advance one configured provider without re-querying unrelated sources.
    /// A completed provider candidate is reconciled through the normal global
    /// refresh path before it becomes visible.
    pub async fn refresh_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        self.refresh_inner(Some(source_id)).await
    }

    async fn refresh_inner(
        &mut self,
        source_id: Option<&str>,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        let started = std::time::Instant::now();
        let requested_source = source_id.unwrap_or_else(|| self.source_id());
        info!(
            event = "reference_refresh_started",
            component = "reference",
            source = requested_source,
            "reference refresh started"
        );
        let result = match source_id {
            Some(source_id) => self.actor.refresh_source(source_id).await,
            None => self.actor.refresh().await,
        };
        let result = match result {
            Ok(result) => result,
            Err(error) => {
                let provider_health = self.provider_health();
                let degraded_providers = provider_health
                    .iter()
                    .filter(|health| health.status != "ready" && health.status != "unknown")
                    .map(|health| health.source_id.as_str())
                    .collect::<Vec<_>>();
                let stale_provider_count =
                    provider_health.iter().filter(|health| health.stale).count();
                warn!(
                    event = "reference_refresh_failed",
                    component = "reference",
                    source = %self.source_id(),
                    duration_ms = started.elapsed().as_millis() as u64,
                    provider_count = provider_health.len(),
                    degraded_provider_count = degraded_providers.len(),
                    stale_provider_count,
                    degraded_providers = ?degraded_providers,
                    fallback = if stale_provider_count > 0 { "last_known_good" } else { "none" },
                    error = %error,
                    "reference refresh failed"
                );
                return Err(error);
            }
        };
        info!(
            event = "reference_refresh_completed",
            component = "reference",
            generation = result.generation.get(),
            event_sequence = result.event_sequence.get(),
            change_count = result.event_count,
            "reference refresh completed"
        );
        Ok(ReferenceRefreshResult {
            generation: result.generation,
            event_sequence: result.event_sequence,
            changed: result.changed,
            change_count: result.event_count,
            events: result.events,
        })
    }

    pub async fn upsert_asset(&mut self, asset: Asset) -> ReferenceResult<Generation> {
        info!(event = "reference_asset_upsert_started", component = "reference", asset_id = %asset.asset_id, "reference asset upsert started");
        self.actor.upsert_asset(asset).await?;
        let generation = self.actor.metadata.generation;
        info!(
            event = "reference_asset_upsert_completed",
            component = "reference",
            generation = generation.get(),
            "reference asset upsert completed"
        );
        Ok(generation)
    }

    pub async fn upsert_instrument(
        &mut self,
        instrument: Instrument,
    ) -> ReferenceResult<Generation> {
        self.actor.upsert_instrument(instrument).await?;
        Ok(self.actor.metadata.generation)
    }

    pub async fn upsert_listing(&mut self, listing: Listing) -> ReferenceResult<Generation> {
        self.actor.upsert_listing(listing).await?;
        Ok(self.actor.metadata.generation)
    }

    pub async fn pending_events(&mut self, limit: usize) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.actor.pending_events(limit).await
    }

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
                    value.source_symbol.as_deref().unwrap_or(""),
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
        sequence_from: Option<kairos_primitives::Sequence>,
        sequence_to: Option<kairos_primitives::Sequence>,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.lifecycle_events(&LifecycleQuery {
            sequence_from,
            sequence_to,
            ..LifecycleQuery::default()
        })
        .await
    }

    pub async fn acknowledge_published_events(
        &mut self,
        event_ids: &[String],
    ) -> ReferenceResult<()> {
        let result = self.actor.acknowledge_pending_events(event_ids).await;
        match &result {
            Ok(()) => info!(
                event = "reference_events_acknowledged",
                component = "reference",
                "reference published events acknowledged"
            ),
            Err(error) => {
                warn!(event = "reference_events_acknowledge_failed", component = "reference", error = %error, "reference published events acknowledgement failed")
            }
        }
        result
    }

    /// Read the current catalog for diagnostics and controlled projections.
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
            [] => Err(ReferenceError::Invalid(query.not_found_message())),
            _ => Err(ReferenceError::Invalid(query.ambiguous_message())),
        }
    }

    /// Execute the complete read-side catalog query used by the verification
    /// CLI. The application owns filtering so server and CLI cannot drift.
    #[cfg(test)]
    pub fn query(&self, query: &ReferenceQuery) -> Vec<ReferenceRecord> {
        let mut records = Vec::new();
        let include = |kind: ReferenceKind| query.kind == ReferenceKind::All || query.kind == kind;
        if include(ReferenceKind::Entity) {
            records.extend(
                self.actor
                    .catalog
                    .entities
                    .values()
                    .filter(|value| {
                        query.matches_status(value.status.as_str())
                            && query.matches_text(&[
                                &value.entity_id,
                                &value.entity_type,
                                &value.name,
                            ])
                            && query
                                .exchange_id
                                .as_deref()
                                .is_none_or(|exchange| exchange == value.entity_id)
                    })
                    .cloned()
                    .map(ReferenceRecord::Entity),
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
                market_type: query.market_type.clone(),
                asset_type: query.asset_type.clone(),
                source_symbol: query
                    .text
                    .as_deref()
                    .and_then(|value| kairos_primitives::Symbol::new(value).ok()),
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
        if include(ReferenceKind::FinancialProduct) {
            records.extend(
                self.actor
                    .catalog
                    .financial_products
                    .values()
                    .filter(|value| {
                        query.matches_status(value.status.as_str())
                            && query.matches_text(&[
                                &value.product_id,
                                &value.provider_product_id,
                                &value.product_type,
                                &value.name,
                            ])
                    })
                    .cloned()
                    .map(ReferenceRecord::FinancialProduct),
            );
        }
        if include(ReferenceKind::ExecutionAccess) {
            records.extend(
                self.actor
                    .catalog
                    .execution_accesses
                    .values()
                    .filter(|value| {
                        query.matches_status(value.status.as_str())
                            && query
                                .exchange_id
                                .as_deref()
                                .is_none_or(|provider| value.provider_id == provider)
                            && query.matches_text(&[
                                &value.access_id,
                                value.market_id.as_deref().unwrap_or_default(),
                                value.provider_id.as_str(),
                                value.provider_product.as_str(),
                                &value.provider_symbol,
                            ])
                    })
                    .cloned()
                    .map(ReferenceRecord::ExecutionAccess),
            );
        }
        if include(ReferenceKind::MarketDataAccess) {
            records.extend(
                self.actor
                    .catalog
                    .market_data_accesses
                    .values()
                    .filter(|value| {
                        query.matches_status(value.status.as_str())
                            && query.matches_text(&[
                                &value.access_id,
                                &value.market_id,
                                value.provider_id.as_str(),
                                value.provider_product.as_str(),
                                &value.provider_symbol,
                            ])
                    })
                    .cloned()
                    .map(ReferenceRecord::MarketDataAccess),
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
                            value.source_symbol.as_deref().unwrap_or(""),
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
        if let Some(value) = self.actor.catalog.entities.get(identifier) {
            matches.push(ReferenceRecord::Entity(value.clone()));
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
        if let Some(value) = self.actor.catalog.financial_products.get(identifier) {
            matches.push(ReferenceRecord::FinancialProduct(value.clone()));
        }
        if let Some(value) = self.actor.catalog.execution_accesses.get(identifier) {
            matches.push(ReferenceRecord::ExecutionAccess(value.clone()));
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
}

#[cfg(test)]
#[allow(private_bounds)]
impl<S, C> ReferenceApplication<S, C>
where
    S: ReferenceSource,
    C: CatalogStore,
{
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

impl ReferenceReadModel {
    pub fn actor_id(&self) -> &str {
        &self.actor_id
    }

    pub fn source_id(&self) -> &str {
        &self.source_id
    }

    pub fn provider_health(&self) -> &[ProviderHealth] {
        &self.provider_health
    }

    pub fn outbox_depth(&self) -> usize {
        self.outbox_depth
    }

    pub fn generation(&self) -> Generation {
        self.generation
    }

    pub fn event_sequence(&self) -> Sequence {
        self.event_sequence
    }

    pub fn market_count(&self) -> usize {
        self.market_count
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
