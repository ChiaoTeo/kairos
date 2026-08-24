use serde::{Deserialize, Serialize};

use super::{SourceState, SourceStatus};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketReadiness {
    #[default]
    Starting,
    Ready,
    Degraded,
    Stopped,
}

pub(crate) fn derive_readiness<'a>(
    sources: impl IntoIterator<Item = &'a SourceState>,
) -> MarketReadiness {
    let sources = sources.into_iter().collect::<Vec<_>>();
    if sources.is_empty() {
        return MarketReadiness::Ready;
    }
    if sources
        .iter()
        .all(|state| state.status == SourceStatus::Stopped)
    {
        MarketReadiness::Stopped
    } else if sources
        .iter()
        .all(|state| matches!(state.status, SourceStatus::Ready | SourceStatus::Paused))
    {
        MarketReadiness::Ready
    } else if sources
        .iter()
        .any(|state| matches!(state.status, SourceStatus::Degraded | SourceStatus::Stopped))
    {
        MarketReadiness::Degraded
    } else {
        MarketReadiness::Starting
    }
}
