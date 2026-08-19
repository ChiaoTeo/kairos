//! Single-owner Reference actor.

use tracing::info;

use super::providers::ReferenceSourcePlan;
use super::publication::{StoredPublication, encode_publications};
#[cfg(not(test))]
use super::source::ConfiguredReferenceSource;
use super::source::ReferenceSource;
use super::sqlx_storage::SqlxCatalogStore;
use crate::domain::{
    Asset, Instrument, LifecycleEvent, Listing, ProviderCatalog, ProviderHealth, ReferenceCatalog,
    ReferenceResult, unix_nanos,
};

#[cfg(not(test))]
type ActorReferenceSource = ConfiguredReferenceSource;
#[cfg(test)]
type ActorReferenceSource = Box<dyn ReferenceSource>;

pub struct ReferenceActor {
    pub actor_id: String,
    pub metadata: CatalogMetadata,
    #[cfg(test)]
    pub catalog: ReferenceCatalog,
    source: Option<ActorReferenceSource>,
    source_plan: Option<ReferenceSourcePlan>,
    store: SqlxCatalogStore,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct CatalogMetadata {
    pub generation: kairos_primitives::Generation,
    pub event_sequence: kairos_primitives::Sequence,
    pub market_count: usize,
}

impl From<&ReferenceCatalog> for CatalogMetadata {
    fn from(catalog: &ReferenceCatalog) -> Self {
        Self {
            generation: catalog.generation,
            event_sequence: catalog.event_sequence,
            market_count: catalog.markets.len(),
        }
    }
}

impl ReferenceActor {
    pub async fn new(
        actor_id: impl Into<String>,
        source_plan: ReferenceSourcePlan,
        mut store: SqlxCatalogStore,
    ) -> ReferenceResult<Self> {
        #[cfg(test)]
        let catalog = store.load().await?.unwrap_or_default();
        #[cfg(test)]
        let metadata = CatalogMetadata::from(&catalog);
        #[cfg(not(test))]
        let metadata = {
            let state = store.load_state().await?;
            CatalogMetadata {
                generation: state.generation,
                event_sequence: state.event_sequence,
                market_count: state.market_count,
            }
        };
        info!(
            event = "reference_state_loaded",
            component = "reference",
            generation = metadata.generation.get(),
            event_sequence = metadata.event_sequence.get(),
            market_count = metadata.market_count,
            "reference catalog loaded from persistence"
        );
        Ok(Self {
            actor_id: actor_id.into(),
            metadata,
            #[cfg(test)]
            catalog,
            source: None,
            source_plan: Some(source_plan),
            store,
        })
    }

    #[cfg(test)]
    pub async fn new_test<S>(
        actor_id: impl Into<String>,
        source: S,
        mut store: SqlxCatalogStore,
    ) -> ReferenceResult<Self>
    where
        S: ReferenceSource + 'static,
    {
        let catalog = store.load().await?.unwrap_or_default();
        let metadata = CatalogMetadata::from(&catalog);
        Ok(Self {
            actor_id: actor_id.into(),
            metadata,
            catalog,
            source: Some(Box::new(source)),
            source_plan: None,
            store,
        })
    }

    pub fn source_id(&self) -> &str {
        self.source
            .as_ref()
            .map(|source| source.source_id())
            .unwrap_or("reference-providers")
    }

    pub fn provider_health(&self) -> Vec<ProviderHealth> {
        self.source
            .as_ref()
            .map(|source| source.provider_health())
            .unwrap_or_default()
    }

    pub async fn activate_sources(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        if self.source.is_some() {
            return Ok(());
        }
        let plan = self.source_plan.take().ok_or_else(|| {
            crate::domain::ReferenceError::Provider("Reference source plan is unavailable".into())
        })?;
        let source = plan.activate(connections).await?;
        #[cfg(not(test))]
        {
            self.source = Some(source);
        }
        #[cfg(test)]
        {
            self.source = Some(Box::new(source));
        }
        Ok(())
    }

    fn source_mut(&mut self) -> ReferenceResult<&mut ActorReferenceSource> {
        self.source.as_mut().ok_or_else(|| {
            crate::domain::ReferenceError::Provider("Reference sources are not active".into())
        })
    }

    pub async fn refresh_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<RefreshResult> {
        let started = std::time::Instant::now();
        let normalized = self.source_mut()?.normalized_facts_authoritative();
        let incoming = self
            .source_mut()?
            .fetch_catalog_with_connections(connections)
            .await?;
        if normalized {
            return self.reconcile_normalized(incoming, started).await;
        }
        self.reconcile(incoming, started).await
    }

    #[cfg(test)]
    pub async fn refresh(&mut self) -> ReferenceResult<RefreshResult> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        self.refresh_with_connections(&mut system.connections())
            .await
    }

    pub async fn refresh_source_with_connections(
        &mut self,
        source_id: &str,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<RefreshResult> {
        let started = std::time::Instant::now();
        let normalized = self.source_mut()?.normalized_facts_authoritative();
        match self
            .source_mut()?
            .advance_source_with_connections(source_id, connections)
            .await?
        {
            Some(incoming) if normalized => self.reconcile_normalized(incoming, started).await,
            Some(incoming) => self.reconcile(incoming, started).await,
            None => Ok(RefreshResult {
                generation: self.metadata.generation,
                event_sequence: self.metadata.event_sequence,
                changed: false,
                event_count: 0,
                events: Vec::new(),
            }),
        }
    }

    async fn reconcile_normalized(
        &mut self,
        overlay: ProviderCatalog,
        started: std::time::Instant,
    ) -> ReferenceResult<RefreshResult> {
        overlay.validate()?;
        let candidate = self.store.load_provider_candidate(&overlay).await?;
        self.reconcile_candidate(candidate, started, true).await
    }

    pub async fn set_source_paused(
        &mut self,
        source_id: &str,
        paused: bool,
    ) -> ReferenceResult<()> {
        self.source_mut()?
            .set_source_paused(source_id, paused)
            .await
    }

    pub fn option_underlyings(&self) -> Vec<String> {
        self.source
            .as_ref()
            .map(|source| source.option_underlyings())
            .unwrap_or_default()
    }

    /// Change Massive options coverage and immediately advance that source.
    /// Additions may require more pages and therefore legitimately return an
    /// unchanged catalog until the scoped candidate is complete; removals are
    /// reconciled immediately from the remaining committed scopes.
    #[cfg(test)]
    pub async fn set_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<RefreshResult> {
        self.source_mut()?
            .set_option_underlying(underlying, enabled)
            .await?;
        self.refresh_source_with_connections("massive-options", connections)
            .await
    }

    #[cfg(not(test))]
    pub fn massive_option_connection_plan(
        &self,
        underlying: &str,
    ) -> ReferenceResult<(
        kairos_conflux::ConnectionKey,
        kairos_conflux::MassiveRestConfig,
    )> {
        self.source
            .as_ref()
            .ok_or_else(|| {
                crate::domain::ReferenceError::Provider(
                    "Reference provider connections have not been activated".into(),
                )
            })?
            .massive_option_connection_plan(underlying)
    }

    #[cfg(not(test))]
    pub async fn set_managed_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
        connection_key: Option<kairos_conflux::ConnectionKey>,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<RefreshResult> {
        self.source_mut()?
            .set_managed_option_underlying(underlying, enabled, connection_key)
            .await?;
        self.refresh_source_with_connections("massive-options", connections)
            .await
    }

    async fn reconcile(
        &mut self,
        incoming: ProviderCatalog,
        started: std::time::Instant,
    ) -> ReferenceResult<RefreshResult> {
        self.reconcile_candidate(incoming, started, false).await
    }

    async fn reconcile_candidate(
        &mut self,
        incoming: ProviderCatalog,
        started: std::time::Instant,
        commit_provider_promotions: bool,
    ) -> ReferenceResult<RefreshResult> {
        incoming.validate()?;
        let mut candidate = self.store.load().await?.unwrap_or_default();
        let previous_generation = candidate.generation;
        let events = candidate.apply(incoming, unix_nanos());
        const IN_MEMORY_LIFECYCLE_LIMIT: usize = 4096;
        if candidate.lifecycle_events.len() > IN_MEMORY_LIFECYCLE_LIMIT {
            let keep_from = candidate.lifecycle_events.len() - IN_MEMORY_LIFECYCLE_LIMIT;
            candidate.lifecycle_events.drain(..keep_from);
        }
        let changed = previous_generation != candidate.generation || !events.is_empty();
        if changed || commit_provider_promotions {
            let publications = encode_publications(&candidate, &events)?;
            self.store
                .save_refresh(&candidate, &events, &publications)
                .await?;
        }
        self.metadata = CatalogMetadata::from(&candidate);
        #[cfg(test)]
        {
            self.catalog = candidate;
        }
        info!(
            event = "reference_reconcile_completed",
            component = "reference",
            generation = self.metadata.generation.get(),
            event_sequence = self.metadata.event_sequence.get(),
            changed,
            event_count = events.len(),
            duration_ms = started.elapsed().as_millis() as u64,
            "reference reconcile completed"
        );
        Ok(RefreshResult {
            generation: self.metadata.generation,
            event_sequence: self.metadata.event_sequence,
            changed,
            event_count: events.len(),
            events,
        })
    }

    pub async fn upsert_asset(&mut self, asset: Asset) -> ReferenceResult<()> {
        let mut current = self.store.load().await?.unwrap_or_default();
        let mut candidate = provider_catalog(&current);
        candidate
            .assets
            .retain(|value| value.asset_id != asset.asset_id);
        candidate.assets.push(asset.clone());
        candidate.validate()?;
        if current.assets.get(asset.asset_id.as_str()) == Some(&asset) {
            return Ok(());
        }
        let mut next = std::mem::take(&mut current);
        let sequence = next.event_sequence + 1;
        let asset_id = asset.asset_id.clone();
        next.assets.insert(asset_id.to_string(), asset);
        next.generation += 1;
        next.event_sequence = sequence;
        let event = LifecycleEvent {
            event_id: format!("reference:{sequence:020}"),
            event_type: "asset_changed".into(),
            event_time_unix_nanos: unix_nanos(),
            record_kind: Some("asset".into()),
            record_id: Some(asset_id.to_string()),
            operation: Some("upsert".into()),
            generation: next.generation.get().into(),
            ..LifecycleEvent::default()
        };
        next.lifecycle_events.push(event.clone());
        let publications = encode_publications(&next, std::slice::from_ref(&event))?;
        self.store
            .save_refresh(&next, std::slice::from_ref(&event), &publications)
            .await?;
        self.metadata = CatalogMetadata::from(&next);
        #[cfg(test)]
        {
            self.catalog = next;
        }
        Ok(())
    }

    pub async fn upsert_instrument(&mut self, instrument: Instrument) -> ReferenceResult<()> {
        let mut current = self.store.load().await?.unwrap_or_default();
        let mut candidate = provider_catalog(&current);
        candidate
            .instruments
            .retain(|value| value.instrument_id != instrument.instrument_id);
        candidate.instruments.push(instrument.clone());
        candidate.validate()?;
        if current.instruments.get(&instrument.instrument_id) == Some(&instrument) {
            return Ok(());
        }
        let mut next = std::mem::take(&mut current);
        let sequence = next.event_sequence + 1;
        let instrument_id = instrument.instrument_id.clone();
        next.instruments.insert(instrument_id.clone(), instrument);
        next.generation += 1;
        next.event_sequence = sequence;
        let event = LifecycleEvent {
            event_id: format!("reference:{sequence:020}"),
            event_type: "instrument_changed".into(),
            event_time_unix_nanos: unix_nanos(),
            record_kind: Some("instrument".into()),
            record_id: Some(instrument_id.to_string()),
            operation: Some("upsert".into()),
            generation: next.generation.get().into(),
            ..LifecycleEvent::default()
        };
        next.lifecycle_events.push(event.clone());
        let publications = encode_publications(&next, std::slice::from_ref(&event))?;
        self.store
            .save_refresh(&next, std::slice::from_ref(&event), &publications)
            .await?;
        self.metadata = CatalogMetadata::from(&next);
        #[cfg(test)]
        {
            self.catalog = next;
        }
        Ok(())
    }

    pub async fn upsert_listing(&mut self, listing: Listing) -> ReferenceResult<()> {
        let mut current = self.store.load().await?.unwrap_or_default();
        let mut candidate = provider_catalog(&current);
        candidate
            .listings
            .retain(|value| value.listing_id != listing.listing_id);
        candidate.listings.push(listing.clone());
        candidate.validate()?;
        if current.listings.get(&listing.listing_id) == Some(&listing) {
            return Ok(());
        }
        let mut next = std::mem::take(&mut current);
        let sequence = next.event_sequence + 1;
        let listing_id = listing.listing_id.clone();
        next.listings.insert(listing_id.clone(), listing);
        next.generation += 1;
        next.event_sequence = sequence;
        let event = LifecycleEvent {
            event_id: format!("reference:{sequence:020}"),
            event_type: "listing_changed".into(),
            event_time_unix_nanos: unix_nanos(),
            record_kind: Some("listing".into()),
            record_id: Some(listing_id.to_string()),
            operation: Some("upsert".into()),
            generation: next.generation.get().into(),
            ..LifecycleEvent::default()
        };
        next.lifecycle_events.push(event.clone());
        let publications = encode_publications(&next, std::slice::from_ref(&event))?;
        self.store
            .save_refresh(&next, std::slice::from_ref(&event), &publications)
            .await?;
        self.metadata = CatalogMetadata::from(&next);
        #[cfg(test)]
        {
            self.catalog = next;
        }
        Ok(())
    }

    pub async fn pending_publications(
        &mut self,
        limit: usize,
    ) -> ReferenceResult<Vec<StoredPublication>> {
        self.store.pending_publications(limit).await
    }

    pub async fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        self.store.pending_event_count().await
    }

    pub async fn lifecycle_events(
        &mut self,
        sequence_from: Option<u64>,
        sequence_to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        let events = self
            .store
            .lifecycle_events(sequence_from, sequence_to, limit)
            .await?;
        #[cfg(test)]
        if events.is_empty() && !self.catalog.lifecycle_events.is_empty() {
            return Ok(self
                .catalog
                .lifecycle_events
                .iter()
                .filter(|event| {
                    let sequence = event
                        .event_id
                        .rsplit(':')
                        .next()
                        .and_then(|value| value.parse::<u64>().ok())
                        .unwrap_or(0);
                    sequence_from.is_none_or(|value| sequence >= value)
                        && sequence_to.is_none_or(|value| sequence <= value)
                })
                .take(limit)
                .cloned()
                .collect());
        }
        Ok(events)
    }

    pub async fn lifecycle_events_filtered(
        &mut self,
        sequence_from: Option<u64>,
        sequence_to: Option<u64>,
        event_time_from_unix_nanos: Option<u64>,
        event_time_to_unix_nanos: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        let events = self
            .store
            .lifecycle_events_filtered(
                sequence_from,
                sequence_to,
                event_time_from_unix_nanos,
                event_time_to_unix_nanos,
                limit,
            )
            .await?;
        #[cfg(test)]
        if events.is_empty() && !self.catalog.lifecycle_events.is_empty() {
            return Ok(self
                .catalog
                .lifecycle_events
                .iter()
                .filter(|event| {
                    let sequence = event
                        .event_id
                        .rsplit(':')
                        .next()
                        .and_then(|value| value.parse::<u64>().ok())
                        .unwrap_or(0);
                    sequence_from.is_none_or(|value| sequence >= value)
                        && sequence_to.is_none_or(|value| sequence <= value)
                        && event_time_from_unix_nanos
                            .is_none_or(|value| event.event_time_unix_nanos >= value.into())
                        && event_time_to_unix_nanos
                            .is_none_or(|value| event.event_time_unix_nanos < value.into())
                })
                .take(limit)
                .cloned()
                .collect());
        }
        Ok(events)
    }

    pub async fn acknowledge_publications(&mut self, event_ids: &[String]) -> ReferenceResult<()> {
        self.store.acknowledge_publications(event_ids).await
    }
}

fn provider_catalog(catalog: &ReferenceCatalog) -> ProviderCatalog {
    ProviderCatalog {
        entities: catalog.entities.values().cloned().collect(),
        assets: catalog.assets.values().cloned().collect(),
        instruments: catalog.instruments.values().cloned().collect(),
        listings: catalog.listings.values().cloned().collect(),
        markets: catalog.markets.values().cloned().collect(),
    }
}

#[derive(Debug)]
pub struct RefreshResult {
    pub generation: kairos_primitives::Generation,
    pub event_sequence: kairos_primitives::Sequence,
    pub changed: bool,
    pub event_count: usize,
    pub events: Vec<LifecycleEvent>,
}
