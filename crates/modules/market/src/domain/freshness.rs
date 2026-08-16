use kairos_primitives::{MarketId, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

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

/// Freshness watermark for one source/market/data-kind view.
///
/// `last_event_time_unix_nanos` is provider/event time.  The receive time is
/// recorded by the Market actor when the normalized fact is accepted, so
/// consumers can distinguish a delayed provider event from a delayed local
/// process.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketFreshness {
    pub source_id: String,
    pub market_id: MarketId,
    pub data_kind: String,
    pub last_event_time_unix_nanos: UnixNanos,
    pub last_received_time_unix_nanos: UnixNanos,
    pub event_sequence: Sequence,
    pub status: DataFreshnessStatus,
}
