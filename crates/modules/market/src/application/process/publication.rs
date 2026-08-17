use crate::domain::events::MarketChange;

pub trait MarketChangePublisher: Send {
    fn publish(&mut self, change: &MarketChange) -> Result<(), String>;
}

pub(super) enum MarketHistoryRecorder {
    Noop,
    Jsonl(crate::services::history::JsonlMarketHistoryRecorder),
}

impl MarketHistoryRecorder {
    pub(super) async fn record(
        &mut self,
        events: &[(u64, crate::MarketEvent)],
    ) -> Result<(), String> {
        match self {
            Self::Noop => Ok(()),
            Self::Jsonl(recorder) => recorder.record(events).await,
        }
    }

    pub(super) async fn shutdown(&mut self) -> Result<(), String> {
        match self {
            Self::Noop => Ok(()),
            Self::Jsonl(recorder) => recorder.shutdown().await,
        }
    }
}
