//! Single-owner Reference actor.

use super::providers::ReferenceSource;
use super::store::CatalogStore;
use crate::domain::{
    unix_nanos, Asset, Instrument, LifecycleEvent, Listing, ProviderCatalog, ProviderHealth,
    ReferenceCatalog, ReferenceResult,
};
use tracing::info;

pub struct ReferenceActor<S, C> {
    pub actor_id: String,
    pub metadata: CatalogMetadata,
    #[cfg(test)]
    pub catalog: ReferenceCatalog,
    source: S,
    store: C,
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

impl<S, C> ReferenceActor<S, C>
where
    S: ReferenceSource,
    C: CatalogStore,
{
    pub async fn new(
        actor_id: impl Into<String>,
        source: S,
        mut store: C,
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
            source,
            store,
        })
    }

    pub fn source_id(&self) -> &str {
        self.source.source_id()
    }

    pub fn provider_health(&self) -> Vec<ProviderHealth> {
        self.source.provider_health()
    }

    pub async fn current_catalog(&mut self) -> ReferenceResult<ReferenceCatalog> {
        self.store
            .load()
            .await
            .map(|value| value.unwrap_or_default())
    }

    pub async fn refresh(&mut self) -> ReferenceResult<RefreshResult> {
        let started = std::time::Instant::now();
        let normalized = self.source.normalized_facts_authoritative();
        let incoming = self.source.fetch_catalog().await?;
        if normalized {
            return self.reconcile_normalized(incoming, started).await;
        }
        self.reconcile(incoming, started).await
    }

    pub async fn refresh_source(&mut self, source_id: &str) -> ReferenceResult<RefreshResult> {
        let started = std::time::Instant::now();
        let normalized = self.source.normalized_facts_authoritative();
        match self.source.advance_source(source_id).await? {
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
        let result = self
            .store
            .reconcile_provider_facts(&overlay, unix_nanos())
            .await?
            .ok_or_else(|| {
                crate::domain::ReferenceError::Persistence(
                    "normalized source requires a normalized catalog store".into(),
                )
            })?;
        self.metadata = CatalogMetadata {
            generation: result.generation,
            event_sequence: result.event_sequence,
            market_count: result.market_count,
        };
        info!(
            event = "reference_reconcile_completed",
            component = "reference",
            generation = result.generation.get(),
            event_sequence = result.event_sequence.get(),
            changed = result.changed,
            event_count = result.event_count,
            duration_ms = started.elapsed().as_millis() as u64,
            mode = "sqlite_normalized",
            "reference normalized source facts reconciled"
        );
        Ok(RefreshResult {
            generation: result.generation,
            event_sequence: result.event_sequence,
            changed: result.changed,
            event_count: result.event_count,
            // Publication reads bounded durable lifecycle batches. Returning
            // all refresh events would recreate the full-change memory spike.
            events: Vec::new(),
        })
    }

    pub async fn set_source_paused(
        &mut self,
        source_id: &str,
        paused: bool,
    ) -> ReferenceResult<()> {
        self.source.set_source_paused(source_id, paused).await
    }

    pub fn option_underlyings(&self) -> Vec<String> {
        self.source.option_underlyings()
    }

    /// Change Massive options coverage and immediately advance that source.
    /// Additions may require more pages and therefore legitimately return an
    /// unchanged catalog until the scoped candidate is complete; removals are
    /// reconciled immediately from the remaining committed scopes.
    pub async fn set_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<RefreshResult> {
        self.source
            .set_option_underlying(underlying, enabled)
            .await?;
        self.refresh_source("massive-options").await
    }

    async fn reconcile(
        &mut self,
        incoming: ProviderCatalog,
        started: std::time::Instant,
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
        if changed {
            self.store.save_refresh(&candidate, &events).await?;
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
            record_payload_json: next
                .assets
                .get(asset_id.as_str())
                .and_then(|value| serde_json::to_string(value).ok()),
            ..LifecycleEvent::default()
        };
        next.lifecycle_events.push(event.clone());
        self.store.save_refresh(&next, &[event]).await?;
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
            record_payload_json: next
                .instruments
                .get(&instrument_id)
                .and_then(|value| serde_json::to_string(value).ok()),
            ..LifecycleEvent::default()
        };
        next.lifecycle_events.push(event.clone());
        self.store.save_refresh(&next, &[event]).await?;
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
            record_payload_json: next
                .listings
                .get(&listing_id)
                .and_then(|value| serde_json::to_string(value).ok()),
            ..LifecycleEvent::default()
        };
        next.lifecycle_events.push(event.clone());
        self.store.save_refresh(&next, &[event]).await?;
        self.metadata = CatalogMetadata::from(&next);
        #[cfg(test)]
        {
            self.catalog = next;
        }
        Ok(())
    }

    pub async fn pending_events(&mut self, limit: usize) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.store.pending_events(limit).await
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

    pub async fn acknowledge_pending_events(
        &mut self,
        event_ids: &[String],
    ) -> ReferenceResult<()> {
        self.store.acknowledge_pending_events(event_ids).await
    }
}

fn provider_catalog(catalog: &ReferenceCatalog) -> ProviderCatalog {
    ProviderCatalog {
        entities: catalog.entities.values().cloned().collect(),
        assets: catalog.assets.values().cloned().collect(),
        instruments: catalog.instruments.values().cloned().collect(),
        listings: catalog.listings.values().cloned().collect(),
        markets: catalog.markets.values().cloned().collect(),
        financial_products: catalog.financial_products.values().cloned().collect(),
        execution_accesses: catalog.execution_accesses.values().cloned().collect(),
        market_data_accesses: catalog.market_data_accesses.values().cloned().collect(),
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
