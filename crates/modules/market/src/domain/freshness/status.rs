use kairos_primitives::market::SourceId;
use kairos_primitives::time::{Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use crate::domain::observation::{ObservationKind, ObservationScope};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum FeedStatus {
    #[default]
    Disconnected,
    Ready,
    Reconnecting,
    WarmingUp,
    Degraded,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum DataFreshnessStatus {
    #[default]
    Unknown,
    Current,
    Stale,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketFreshness {
    pub source_id: SourceId,
    pub scope: ObservationScope,
    pub data_kind: ObservationKind,
    pub last_event_time_unix_nanos: UnixNanos,
    pub last_received_time_unix_nanos: UnixNanos,
    pub event_sequence: Sequence,
    pub status: DataFreshnessStatus,
}
