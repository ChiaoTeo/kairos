use std::collections::BTreeMap;

use tokio::sync::mpsc;

use super::MarketActor;
use crate::domain::freshness::FeedStatus;
use crate::domain::market::{ProviderSegmentCode, ResolvedMarket};
use crate::domain::source::{
    FeedDescriptor, MarketFeedId, MarketReadiness, SourceEpoch, SourceFailureKind, SourceState,
    SourceStatus, derive_readiness,
};
use crate::services::source::messages::{ProviderSubscriptionId, SourceCommand, SourceInput};

/// Stable identity for one provider-side subscription desired by Market.
///
/// Logical subscription owners deliberately do not participate in this key:
/// multiple strategies requiring the same source route share one physical
/// handle. Route identity does participate so a Reference/provider change is
/// reconciled as a replacement rather than silently reusing a stale handle.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct PhysicalSubscriptionKey {
    source_id: MarketFeedId,
    member_id: String,
    provider: Option<kairos_primitives::market::Provider>,
    provider_segment: Option<ProviderSegmentCode>,
    subscription_symbol: Option<kairos_primitives::market::SubscriptionSymbol>,
    observation_requirements:
        std::collections::BTreeSet<crate::domain::subscription::ObservationSelector>,
}

impl PhysicalSubscriptionKey {
    pub(crate) fn for_source(descriptor: &FeedDescriptor, market: &ResolvedMarket) -> Option<Self> {
        let (provider, provider_segment, subscription_symbol) = match descriptor.provider.as_ref() {
            Some(provider) => {
                let attachment = market.attach_route(&descriptor.id, provider)?;
                (
                    Some(attachment.route.provider),
                    Some(attachment.provider_segment),
                    Some(attachment.subscription_symbol),
                )
            },
            None => (None, None, None),
        };
        Some(Self {
            source_id: descriptor.id.clone(),
            member_id: market.member_id(),
            provider,
            provider_segment,
            subscription_symbol,
            observation_requirements: market.observation_requirements(),
        })
    }
}

pub(crate) struct AttachedSource {
    pub(crate) descriptor: FeedDescriptor,
    pub(crate) commands: mpsc::Sender<SourceCommand>,
    pub(crate) inputs: Option<mpsc::Receiver<SourceInput>>,
    pub(crate) task: Option<tokio::task::JoinHandle<()>>,
    pub(crate) confirmed: BTreeMap<PhysicalSubscriptionKey, ProviderSubscriptionId>,
}

pub(crate) enum PendingSourceRequest {
    Subscribe {
        source_id: MarketFeedId,
        key: PhysicalSubscriptionKey,
    },
    Unsubscribe {
        source_id: MarketFeedId,
        key: PhysicalSubscriptionKey,
    },
    ResyncOrderBook {
        source_id: MarketFeedId,
        market_id: kairos_primitives::reference::MarketId,
    },
}

impl PendingSourceRequest {
    pub(crate) fn source_id(&self) -> &MarketFeedId {
        match self {
            Self::Subscribe { source_id, .. }
            | Self::Unsubscribe { source_id, .. }
            | Self::ResyncOrderBook { source_id, .. } => source_id,
        }
    }
}

impl MarketActor {
    pub(crate) fn source_states(&self) -> impl Iterator<Item = &SourceState> {
        self.sources.values()
    }

    pub(crate) fn source_state(&self, source_id: &MarketFeedId) -> Option<&SourceState> {
        self.sources.get(source_id)
    }

    pub(crate) fn register_source(&mut self, descriptor: FeedDescriptor) -> Result<(), String> {
        if self.sources.contains_key(&descriptor.id) {
            return Err(format!("market source already exists: {}", descriptor.id));
        }
        self.sources
            .insert(descriptor.id.clone(), SourceState::starting(descriptor));
        self.refresh_feed_status();
        Ok(())
    }

    pub(crate) fn source_is_stopped(&self, source_id: &MarketFeedId) -> bool {
        self.sources
            .get(source_id)
            .is_some_and(|source| source.status == SourceStatus::Stopped)
    }

    pub(crate) fn source_command_closed(&self, source_id: &MarketFeedId) -> bool {
        self.attached_sources.get(source_id).is_some_and(|source| {
            (source.inputs.is_some() || source.task.is_some()) && source.commands.is_closed()
        })
    }

    pub(crate) fn apply_source_status(
        &mut self,
        source_id: &MarketFeedId,
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
        source_id: &MarketFeedId,
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
