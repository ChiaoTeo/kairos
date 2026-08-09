use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum FeedStatus {
    Disconnected,
    Ready,
    Reconnecting,
    WarmingUp,
    Degraded,
}

impl Default for FeedStatus {
    fn default() -> Self {
        Self::Disconnected
    }
}
