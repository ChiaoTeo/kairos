//! Deterministic market feed used for warm-up, replay and actor tests.

use std::{collections::VecDeque, fs, path::PathBuf};

use crate::application::protocol::MarketFeed;
use crate::domain::freshness::FeedStatus;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::MarketObservation;
use crate::domain::subscriptions::SubscriptionId;

pub struct ReplayMarketFeed {
    events: VecDeque<MarketObservation>,
    subscribed: bool,
    next_id: u64,
    virtual_time_unix_nanos: Option<u64>,
    completed: bool,
    checkpoint_path: Option<PathBuf>,
    cursor: usize,
}

impl ReplayMarketFeed {
    pub fn new(events: impl IntoIterator<Item = MarketObservation>) -> Self {
        Self::with_window(events, None, None)
    }

    pub fn with_window(
        events: impl IntoIterator<Item = MarketObservation>,
        start_unix_nanos: Option<u64>,
        end_unix_nanos: Option<u64>,
    ) -> Self {
        if let (Some(start), Some(end)) = (start_unix_nanos, end_unix_nanos) {
            assert!(start <= end, "replay start must not be after replay end");
        }
        let mut events: Vec<_> = events
            .into_iter()
            .filter(|event| {
                let time = event.observed_at_unix_nanos();
                start_unix_nanos.is_none_or(|start| time >= start)
                    && end_unix_nanos.is_none_or(|end| time <= end)
            })
            .collect();
        events.sort_by_key(|event| event.observed_at_unix_nanos());
        Self {
            events: events.into_iter().collect(),
            subscribed: false,
            next_id: 1,
            virtual_time_unix_nanos: start_unix_nanos,
            completed: false,
            checkpoint_path: None,
            cursor: 0,
        }
    }

    /// Resume a replay from an instance-owned checkpoint. The source events
    /// remain workspace/data scoped, while this cursor is strictly runtime
    /// state for one launch instance.
    pub fn with_checkpoint(
        events: impl IntoIterator<Item = MarketObservation>,
        start_unix_nanos: Option<u64>,
        end_unix_nanos: Option<u64>,
        checkpoint: impl Into<PathBuf>,
    ) -> Result<Self, String> {
        let checkpoint = checkpoint.into();
        let mut feed = Self::with_window(events, start_unix_nanos, end_unix_nanos);
        if checkpoint.is_file() {
            let value: Checkpoint =
                serde_json::from_slice(&fs::read(&checkpoint).map_err(|e| e.to_string())?)
                    .map_err(|e| format!("invalid replay checkpoint: {e}"))?;
            if value.cursor > feed.events.len() {
                return Err("replay checkpoint cursor exceeds event source".into());
            }
            for _ in 0..value.cursor {
                feed.events.pop_front();
            }
            feed.cursor = value.cursor;
            feed.virtual_time_unix_nanos = value.virtual_time_unix_nanos;
            feed.completed = value.completed;
        }
        feed.checkpoint_path = Some(checkpoint);
        Ok(feed)
    }

    pub fn remaining(&self) -> usize {
        self.events.len()
    }

    pub fn virtual_time_unix_nanos(&self) -> Option<u64> {
        self.virtual_time_unix_nanos
    }

    pub fn completed(&self) -> bool {
        self.completed
    }
}

impl MarketFeed for ReplayMarketFeed {
    fn status(&self) -> FeedStatus {
        if self.subscribed {
            FeedStatus::Ready
        } else {
            FeedStatus::Disconnected
        }
    }

    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        market.validate()?;
        self.subscribed = true;
        let id = SubscriptionId::new(format!("replay:{}", self.next_id))?;
        self.next_id += 1;
        Ok(id)
    }

    fn unsubscribe(&mut self, _subscription: &SubscriptionId) -> Result<(), String> {
        self.subscribed = false;
        Ok(())
    }

    fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
        if !self.subscribed {
            return Err("replay feed is not subscribed".into());
        }
        let events: Vec<_> = self.events.drain(..).collect();
        if let Some(last) = events.last() {
            self.virtual_time_unix_nanos = Some(last.observed_at_unix_nanos());
        }
        self.completed = self.events.is_empty();
        self.cursor += events.len();
        self.persist_checkpoint()?;
        Ok(events)
    }
}

#[derive(serde::Serialize, serde::Deserialize)]
struct Checkpoint {
    cursor: usize,
    virtual_time_unix_nanos: Option<u64>,
    completed: bool,
}

impl ReplayMarketFeed {
    fn persist_checkpoint(&self) -> Result<(), String> {
        let Some(path) = &self.checkpoint_path else {
            return Ok(());
        };
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let temporary = path.with_extension("tmp");
        let bytes = serde_json::to_vec_pretty(&Checkpoint {
            cursor: self.cursor,
            virtual_time_unix_nanos: self.virtual_time_unix_nanos,
            completed: self.completed,
        })
        .map_err(|e| e.to_string())?;
        fs::write(&temporary, bytes).map_err(|e| e.to_string())?;
        fs::rename(temporary, path).map_err(|e| e.to_string())
    }
}
