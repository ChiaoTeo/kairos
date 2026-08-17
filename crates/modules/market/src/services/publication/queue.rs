use super::MarketEventEncoder;
use crate::domain::events::MarketEvent;
use kairos_protocol::InstanceIdentity;
use std::collections::VecDeque;
use tokio::sync::mpsc::error::TrySendError;
use tokio::sync::mpsc::Sender;
const MAX_PENDING_ENCODED_EVENTS: usize = 8_192;

pub(crate) struct HistoryQueue {
    sender: Option<Sender<Vec<(u64, MarketEvent)>>>,
    task: Option<tokio::task::JoinHandle<Result<(), String>>>,
}

impl HistoryQueue {
    pub(crate) fn new(
        sender: Sender<Vec<(u64, MarketEvent)>>,
        task: tokio::task::JoinHandle<Result<(), String>>,
    ) -> Self {
        Self {
            sender: Some(sender),
            task: Some(task),
        }
    }

    pub(crate) async fn record(&self, events: &[(u64, MarketEvent)]) -> Result<(), String> {
        if events.is_empty() {
            return Ok(());
        }
        self.sender
            .as_ref()
            .ok_or_else(|| "market history queue is shutting down".to_owned())?
            .send(events.to_vec())
            .await
            .map_err(|_| "market history writer stopped".to_owned())
    }

    pub(crate) async fn shutdown(&mut self) -> Result<(), String> {
        self.sender.take();
        match self.task.take() {
            Some(task) => task
                .await
                .map_err(|error| format!("market history task failed: {error}"))?,
            None => Ok(()),
        }
    }
}

pub(crate) struct EventPublication {
    actor_id: String,
    identity: InstanceIdentity,
    sender: Sender<Vec<u8>>,
    pub(super) pending: VecDeque<Vec<u8>>,
    encoder: MarketEventEncoder,
}
impl EventPublication {
    pub(crate) fn new(
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
        sender: Sender<Vec<u8>>,
        encoder: MarketEventEncoder,
    ) -> Self {
        Self {
            actor_id: actor_id.into(),
            identity,
            sender,
            pending: VecDeque::new(),
            encoder,
        }
    }

    pub(crate) fn publish(
        &mut self,
        events: impl IntoIterator<Item = (u64, MarketEvent)>,
    ) -> Result<(), String> {
        for (sequence, event) in events {
            if self.pending.len() >= MAX_PENDING_ENCODED_EVENTS {
                return Err(format!(
                    "market encoded event backlog exceeded limit: {MAX_PENDING_ENCODED_EVENTS}"
                ));
            }
            self.pending.push_back((self.encoder)(
                &self.actor_id,
                &self.identity,
                sequence,
                &event,
            )?);
        }
        self.flush()
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.pending.is_empty()
    }

    pub(crate) fn flush(&mut self) -> Result<(), String> {
        while let Some(payload) = self.pending.pop_front() {
            match self.sender.try_send(payload) {
                Ok(()) => {}
                Err(TrySendError::Full(payload)) => {
                    self.pending.push_front(payload);
                    break;
                }
                Err(TrySendError::Closed(_)) => {
                    return Err("market event endpoint is closed".to_owned());
                }
            }
        }
        Ok(())
    }

    /// During shutdown the Actor is no longer ingesting, so it is safe to
    /// await bounded process-queue capacity and commit every already-encoded
    /// event before the engine reports completion.
    pub(crate) async fn drain(&mut self, timeout: std::time::Duration) -> Result<(), String> {
        tokio::time::timeout(timeout, async {
            while let Some(payload) = self.pending.pop_front() {
                self.sender
                    .send(payload)
                    .await
                    .map_err(|_| "market event endpoint is closed".to_owned())?;
            }
            Ok::<(), String>(())
        })
        .await
        .map_err(|_| {
            format!(
                "market event publication drain timed out after {}ms ({} events pending)",
                timeout.as_millis(),
                self.pending.len()
            )
        })?
    }
}
