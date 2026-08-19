use tokio::sync::mpsc::Sender;

use crate::domain::events::MarketEvent;

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
