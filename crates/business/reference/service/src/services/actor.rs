//! Single-owner Reference actor.

use super::providers::ReferenceSource;
use super::store::CatalogStore;
use crate::domain::{
    unix_nanos, Asset, Instrument, LifecycleEvent, Listing, ProviderCatalog, ProviderHealth,
    ReferenceCatalog, ReferenceResult,
};
use tracing::info;

pub struct ReferenceActor {
    pub actor_id: String,
    pub catalog: ReferenceCatalog,
    source: Box<dyn ReferenceSource>,
    store: Box<dyn CatalogStore>,
}

impl ReferenceActor {
    pub async fn new(
        actor_id: impl Into<String>,
        source: Box<dyn ReferenceSource>,
        mut store: Box<dyn CatalogStore>,
    ) -> ReferenceResult<Self> {
        let catalog = store.load().await?.unwrap_or_default();
        info!(
            event = "reference_state_loaded",
            component = "reference",
            generation = catalog.generation.get(),
            event_sequence = catalog.event_sequence.get(),
            asset_count = catalog.assets.len(),
            market_count = catalog.markets.len(),
            "reference catalog loaded from persistence"
        );
        Ok(Self {
            actor_id: actor_id.into(),
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

    pub async fn refresh(&mut self) -> ReferenceResult<RefreshResult> {
        let started = std::time::Instant::now();
        let incoming = self.source.fetch_catalog().await?;
        self.reconcile(incoming, started).await
    }

    pub async fn refresh_source(&mut self, source_id: &str) -> ReferenceResult<RefreshResult> {
        let started = std::time::Instant::now();
        match self.source.advance_source(source_id).await? {
            Some(incoming) => self.reconcile(incoming, started).await,
            None => Ok(RefreshResult {
                generation: self.catalog.generation,
                event_sequence: self.catalog.event_sequence,
                changed: false,
                events: Vec::new(),
            }),
        }
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
        let previous_generation = self.catalog.generation;
        let mut candidate = self.catalog.clone();
        let events = candidate.apply(incoming, unix_nanos());
        const IN_MEMORY_LIFECYCLE_LIMIT: usize = 4096;
        if candidate.lifecycle_events.len() > IN_MEMORY_LIFECYCLE_LIMIT {
            let keep_from = candidate.lifecycle_events.len() - IN_MEMORY_LIFECYCLE_LIMIT;
            candidate.lifecycle_events.drain(..keep_from);
        }
        let changed = previous_generation != candidate.generation || !events.is_empty();
        if changed {
            self.store.save_refresh(&candidate, &events).await?;
            self.catalog = candidate;
        }
        info!(
            event = "reference_reconcile_completed",
            component = "reference",
            generation = self.catalog.generation.get(),
            event_sequence = self.catalog.event_sequence.get(),
            changed,
            event_count = events.len(),
            duration_ms = started.elapsed().as_millis() as u64,
            "reference reconcile completed"
        );
        Ok(RefreshResult {
            generation: self.catalog.generation,
            event_sequence: self.catalog.event_sequence,
            changed,
            events,
        })
    }

    pub async fn upsert_asset(&mut self, asset: Asset) -> ReferenceResult<()> {
        let mut candidate = self.provider_catalog();
        candidate
            .assets
            .retain(|value| value.asset_id != asset.asset_id);
        candidate.assets.push(asset.clone());
        candidate.validate()?;
        if self.catalog.assets.get(asset.asset_id.as_str()) == Some(&asset) {
            return Ok(());
        }
        let mut next = self.catalog.clone();
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
            ..LifecycleEvent::default()
        };
        next.lifecycle_events.push(event.clone());
        self.store.save_refresh(&next, &[event]).await?;
        self.catalog = next;
        Ok(())
    }

    pub async fn upsert_instrument(&mut self, instrument: Instrument) -> ReferenceResult<()> {
        let mut candidate = self.provider_catalog();
        candidate
            .instruments
            .retain(|value| value.instrument_id != instrument.instrument_id);
        candidate.instruments.push(instrument.clone());
        candidate.validate()?;
        if self.catalog.instruments.get(&instrument.instrument_id) == Some(&instrument) {
            return Ok(());
        }
        let mut next = self.catalog.clone();
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
            ..LifecycleEvent::default()
        };
        next.lifecycle_events.push(event.clone());
        self.store.save_refresh(&next, &[event]).await?;
        self.catalog = next;
        Ok(())
    }

    pub async fn upsert_listing(&mut self, listing: Listing) -> ReferenceResult<()> {
        let mut candidate = self.provider_catalog();
        candidate
            .listings
            .retain(|value| value.listing_id != listing.listing_id);
        candidate.listings.push(listing.clone());
        candidate.validate()?;
        if self.catalog.listings.get(&listing.listing_id) == Some(&listing) {
            return Ok(());
        }
        let mut next = self.catalog.clone();
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
            ..LifecycleEvent::default()
        };
        next.lifecycle_events.push(event.clone());
        self.store.save_refresh(&next, &[event]).await?;
        self.catalog = next;
        Ok(())
    }

    fn provider_catalog(&self) -> ProviderCatalog {
        ProviderCatalog {
            entities: self.catalog.entities.values().cloned().collect(),
            assets: self.catalog.assets.values().cloned().collect(),
            instruments: self.catalog.instruments.values().cloned().collect(),
            listings: self.catalog.listings.values().cloned().collect(),
            markets: self.catalog.markets.values().cloned().collect(),
            financial_products: self.catalog.financial_products.values().cloned().collect(),
            execution_accesses: self.catalog.execution_accesses.values().cloned().collect(),
        }
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

#[derive(Debug)]
pub struct RefreshResult {
    pub generation: kairos_domain_types::Generation,
    pub event_sequence: kairos_domain_types::Sequence,
    pub changed: bool,
    pub events: Vec<LifecycleEvent>,
}
