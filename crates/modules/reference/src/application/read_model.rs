use kairos_primitives::time::{Generation, Sequence, UnixNanos};

use crate::application::ReferenceApplication;
use crate::domain::SourceHealth;

/// Immutable read-side view. It contains no provider, SQLite connection, or
/// mutable actor state and can therefore be shared with control/read handlers
/// without serializing them behind the reconcile writer.
#[derive(Clone)]
pub struct ReferenceReadModel {
    pub(crate) actor_id: String,
    pub(crate) source_id: String,
    pub(crate) generation: Generation,
    pub(crate) event_sequence: Sequence,
    pub(crate) committed_at_unix_nanos: UnixNanos,
    pub(crate) entity_count: usize,
    pub(crate) asset_count: usize,
    pub(crate) instrument_count: usize,
    pub(crate) listing_count: usize,
    pub(crate) market_count: usize,
    pub(crate) active_market_count: usize,
    pub(crate) lifecycle_event_count: usize,
    pub(crate) missing_equity_market_count: usize,
    pub(crate) legacy_exchange_market_id_count: usize,
    pub(crate) legacy_exchange_listing_id_count: usize,
    pub(crate) option_listing_count: usize,
    pub(crate) option_market_count: usize,
    pub(crate) source_health: Vec<SourceHealth>,
    pub(crate) outbox_depth: usize,
    pub(crate) oldest_pending_publication_event_id: Option<String>,
}

impl ReferenceApplication {
    pub async fn read_model(&mut self) -> ReferenceReadModel {
        let outbox_depth = self.actor.pending_event_count().await.unwrap_or(0);
        let oldest_pending_publication_event_id = self
            .actor
            .pending_publications(1)
            .await
            .ok()
            .and_then(|publications| publications.into_iter().next())
            .map(|publication| publication.event_id);
        ReferenceReadModel {
            actor_id: self.actor_id().to_owned(),
            source_id: self.source_id().to_owned(),
            generation: self.actor.metadata.generation,
            event_sequence: self.actor.metadata.event_sequence,
            committed_at_unix_nanos: self.actor.metadata.committed_at_unix_nanos,
            entity_count: self.actor.metadata.entity_count,
            asset_count: self.actor.metadata.asset_count,
            instrument_count: self.actor.metadata.instrument_count,
            listing_count: self.actor.metadata.listing_count,
            market_count: self.actor.metadata.market_count,
            active_market_count: self.actor.metadata.active_market_count,
            lifecycle_event_count: self.actor.metadata.lifecycle_event_count,
            missing_equity_market_count: self.actor.metadata.missing_equity_market_count,
            legacy_exchange_market_id_count: self.actor.metadata.legacy_exchange_market_id_count,
            legacy_exchange_listing_id_count: self.actor.metadata.legacy_exchange_listing_id_count,
            option_listing_count: self.actor.metadata.option_listing_count,
            option_market_count: self.actor.metadata.option_market_count,
            source_health: self.source_health(),
            outbox_depth,
            oldest_pending_publication_event_id,
        }
    }
}

impl ReferenceReadModel {
    pub fn actor_id(&self) -> &str {
        &self.actor_id
    }

    pub fn source_id(&self) -> &str {
        &self.source_id
    }

    pub fn source_health(&self) -> &[SourceHealth] {
        &self.source_health
    }

    pub fn outbox_depth(&self) -> usize {
        self.outbox_depth
    }

    pub fn oldest_pending_publication_event_id(&self) -> Option<&str> {
        self.oldest_pending_publication_event_id.as_deref()
    }

    pub fn generation(&self) -> Generation {
        self.generation
    }

    pub fn event_sequence(&self) -> Sequence {
        self.event_sequence
    }

    pub fn committed_at_unix_nanos(&self) -> UnixNanos {
        self.committed_at_unix_nanos
    }

    pub fn entity_count(&self) -> usize {
        self.entity_count
    }

    pub fn asset_count(&self) -> usize {
        self.asset_count
    }

    pub fn instrument_count(&self) -> usize {
        self.instrument_count
    }

    pub fn listing_count(&self) -> usize {
        self.listing_count
    }

    pub fn market_count(&self) -> usize {
        self.market_count
    }

    pub fn active_market_count(&self) -> usize {
        self.active_market_count
    }

    pub fn lifecycle_event_count(&self) -> usize {
        self.lifecycle_event_count
    }

    pub fn missing_equity_market_count(&self) -> usize {
        self.missing_equity_market_count
    }

    pub fn legacy_exchange_market_id_count(&self) -> usize {
        self.legacy_exchange_market_id_count
    }

    pub fn legacy_exchange_listing_id_count(&self) -> usize {
        self.legacy_exchange_listing_id_count
    }

    pub fn option_listing_count(&self) -> usize {
        self.option_listing_count
    }

    pub fn option_market_count(&self) -> usize {
        self.option_market_count
    }
}
