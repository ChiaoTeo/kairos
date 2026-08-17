use kairos_primitives::{MarketId, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use crate::domain::observation::ObservationKind;

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
    pub source_id: String,
    pub market_id: MarketId,
    pub data_kind: ObservationKind,
    pub last_event_time_unix_nanos: UnixNanos,
    pub last_received_time_unix_nanos: UnixNanos,
    pub event_sequence: Sequence,
    pub status: DataFreshnessStatus,
}
