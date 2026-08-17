use std::collections::BTreeMap;

use tokio::sync::mpsc;

use super::MarketActor;
use crate::domain::freshness::FeedStatus;
use crate::domain::source::{
    derive_readiness, MarketReadiness, SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId,
    SourceState, SourceStatus,
};
use crate::domain::subscription::SubscriptionId;
use crate::services::source::messages::{ProviderSubscriptionId, SourceCommand, SourceInput};

pub(crate) type BusinessSubscriptionKey = (SubscriptionId, String);

pub(crate) struct AttachedSource {
    pub(crate) descriptor: SourceDescriptor,
    pub(crate) commands: mpsc::Sender<SourceCommand>,
    pub(crate) inputs: mpsc::Receiver<SourceInput>,
    pub(crate) task: Option<tokio::task::JoinHandle<()>>,
    pub(crate) confirmed: BTreeMap<BusinessSubscriptionKey, ProviderSubscriptionId>,
}

pub(crate) enum PendingSourceRequest {
    Subscribe {
        source_id: SourceId,
        key: BusinessSubscriptionKey,
    },
    Unsubscribe {
        source_id: SourceId,
        key: BusinessSubscriptionKey,
    },
    ResyncOrderBook {
        source_id: SourceId,
        market_id: kairos_primitives::MarketId,
    },
}

impl PendingSourceRequest {
    pub(crate) fn source_id(&self) -> &SourceId {
        match self {
            Self::Subscribe { source_id, .. }
            | Self::Unsubscribe { source_id, .. }
            | Self::ResyncOrderBook { source_id, .. } => source_id,
        }
    }
}

impl MarketActor {
    pub(crate) fn register_source(&mut self, descriptor: SourceDescriptor) -> Result<(), String> {
        if self.sources.contains_key(&descriptor.id) {
            return Err(format!("market source already exists: {}", descriptor.id));
        }
        self.sources
            .insert(descriptor.id.clone(), SourceState::starting(descriptor));
        self.refresh_feed_status();
        Ok(())
    }

    pub(crate) fn take_source_handle(
        &mut self,
        source_id: &SourceId,
    ) -> Result<crate::services::source::SourceHandle, String> {
        let mut attached = self
            .attached_sources
            .remove(source_id)
            .ok_or_else(|| format!("market source is not attached: {source_id}"))?;
        self.sources.remove(source_id);
        self.refresh_feed_status();
        Ok(crate::services::source::SourceHandle {
            descriptor: attached.descriptor,
            commands: attached.commands,
            inputs: attached.inputs,
            task: attached
                .task
                .take()
                .ok_or_else(|| format!("market source task is missing: {source_id}"))?,
        })
    }

    pub(crate) fn source_is_stopped(&self, source_id: &SourceId) -> bool {
        self.sources
            .get(source_id)
            .is_some_and(|source| source.status == SourceStatus::Stopped)
    }

    pub(crate) fn source_command_closed(&self, source_id: &SourceId) -> bool {
        self.attached_sources
            .get(source_id)
            .is_some_and(|source| source.commands.is_closed())
    }

    pub(crate) fn apply_source_status(
        &mut self,
        source_id: &SourceId,
        epoch: SourceEpoch,
        status: SourceStatus,
        error: Option<String>,
    ) -> Result<bool, String> {
        let source = self
            .sources
            .get_mut(source_id)
            .ok_or_else(|| format!("unknown market source: {source_id}"))?;
        let changed = source.change_status(epoch, status, error);
        if changed {
            self.refresh_feed_status();
        }
        Ok(changed)
    }

    pub(crate) fn apply_source_failure(
        &mut self,
        source_id: &SourceId,
        epoch: SourceEpoch,
        kind: SourceFailureKind,
        error: String,
    ) -> Result<bool, String> {
        let source = self
            .sources
            .get_mut(source_id)
            .ok_or_else(|| format!("unknown market source: {source_id}"))?;
        let changed = source.fail(epoch, kind, error);
        if changed {
            self.refresh_feed_status();
        }
        Ok(changed)
    }

    pub(super) fn refresh_feed_status(&mut self) {
        self.feed_status = match derive_readiness(self.sources.values()) {
            MarketReadiness::Ready => FeedStatus::Ready,
            MarketReadiness::Degraded => FeedStatus::Degraded,
            MarketReadiness::Stopped => FeedStatus::Disconnected,
            MarketReadiness::Starting => FeedStatus::WarmingUp,
        };
    }
}
