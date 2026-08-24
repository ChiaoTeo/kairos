use kairos_primitives::reference::MarketId;
use serde::{Deserialize, Serialize};

use super::{FeedDescriptor, SourceEpoch};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SourceStatus {
    #[default]
    Starting,
    Ready,
    Paused,
    Reconnecting,
    WarmingUp,
    Degraded,
    Stopped,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SourceFailureKind {
    InvalidRequest,
    NotReady,
    Unsupported,
    Authentication,
    Authorization,
    Entitlement,
    RateLimited,
    Transport,
    InvalidPayload,
    SequenceGap,
    ResyncRequired,
    Backpressure,
    Unavailable,
    Replay,
    Shutdown,
    Other,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub(crate) struct SourceState {
    pub(crate) descriptor: FeedDescriptor,
    pub(crate) status: SourceStatus,
    pub(crate) epoch: SourceEpoch,
    pub(crate) last_error: Option<String>,
    #[serde(default)]
    pub(crate) last_failure_kind: Option<SourceFailureKind>,
    pub(crate) resyncing_markets: Vec<MarketId>,
}

impl SourceState {
    pub(crate) fn starting(descriptor: FeedDescriptor) -> Self {
        Self {
            descriptor,
            status: SourceStatus::Starting,
            epoch: SourceEpoch::default(),
            last_error: None,
            last_failure_kind: None,
            resyncing_markets: Vec::new(),
        }
    }

    pub(crate) fn change_status(
        &mut self,
        epoch: SourceEpoch,
        status: SourceStatus,
        error: Option<String>,
    ) -> bool {
        if epoch < self.epoch {
            return false;
        }
        self.epoch = epoch;
        self.status = status;
        if let Some(error) = error {
            self.last_error = Some(error);
            self.last_failure_kind = Some(SourceFailureKind::Other);
        } else if matches!(status, SourceStatus::Ready | SourceStatus::Stopped) {
            self.last_error = None;
            self.last_failure_kind = None;
        }
        true
    }

    pub fn fail(&mut self, epoch: SourceEpoch, kind: SourceFailureKind, error: String) -> bool {
        if epoch < self.epoch {
            return false;
        }
        self.epoch = epoch;
        self.status = SourceStatus::Degraded;
        self.last_error = Some(error);
        self.last_failure_kind = Some(kind);
        true
    }
}
