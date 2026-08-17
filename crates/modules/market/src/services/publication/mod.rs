//! Private Market event publication and local subscriber fan-out.

mod encoding;

pub(crate) use encoding::encode_event;

use crate::domain::events::MarketEvent;
use kairos_protocol::InstanceIdentity;
use std::collections::VecDeque;
use tokio::io::AsyncWriteExt;
use tokio::net::UnixStream;
use tokio::sync::mpsc::error::TrySendError;
use tokio::sync::mpsc::{self, Sender};

const MAX_PENDING_ENCODED_EVENTS: usize = 8_192;

/// Encodes domain events and moves them into the bounded process publication
/// queue without making the Actor or application facade own wire state.
pub(crate) struct EventPublication {
    actor_id: String,
    identity: InstanceIdentity,
    sender: Sender<Vec<u8>>,
    pending: VecDeque<Vec<u8>>,
}

impl EventPublication {
    pub(crate) fn new(
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
        sender: Sender<Vec<u8>>,
    ) -> Self {
        Self {
            actor_id: actor_id.into(),
            identity,
            sender,
            pending: VecDeque::new(),
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
            self.pending.push_back(encode_event(
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

/// Owns bounded per-client queues. Slow or disconnected consumers are
/// removed without delaying the Actor, control plane, or other consumers.
pub(crate) struct EventFanout {
    queue_capacity: usize,
    clients: Vec<Sender<Vec<u8>>>,
    writers: Vec<tokio::task::JoinHandle<()>>,
}

impl EventFanout {
    pub(crate) fn new(queue_capacity: usize) -> Self {
        Self {
            queue_capacity,
            clients: Vec::new(),
            writers: Vec::new(),
        }
    }

    pub(crate) fn add_client(&mut self, stream: UnixStream) {
        let (sender, receiver) = mpsc::channel(self.queue_capacity);
        self.writers
            .push(tokio::spawn(event_client_writer(stream, receiver)));
        self.clients.push(sender);
    }

    pub(crate) fn publish(&mut self, payload: Vec<u8>) {
        self.clients
            .retain(|client| match client.try_send(payload.clone()) {
                Ok(()) => true,
                Err(TrySendError::Full(_)) | Err(TrySendError::Closed(_)) => {
                    kairos_workspace::logging::record_counter("kairos.queue.rejected", 1);
                    false
                }
            });
    }

    pub(crate) async fn shutdown(&mut self, timeout: std::time::Duration) {
        // Closing every sender lets healthy writers flush their bounded queue
        // before exiting. A client blocked in the OS is aborted after the
        // process shutdown budget and cannot hold the Actor open.
        self.clients.clear();
        let mut writers = std::mem::take(&mut self.writers);
        let completed = tokio::time::timeout(timeout, async {
            for writer in &mut writers {
                let _ = writer.await;
            }
        })
        .await
        .is_ok();
        if !completed {
            for writer in writers {
                writer.abort();
            }
        }
    }
}

async fn event_client_writer(mut stream: UnixStream, mut receiver: mpsc::Receiver<Vec<u8>>) {
    while let Some(payload) = receiver.recv().await {
        let frame = (payload.len() as u32).to_be_bytes();
        if stream.write_all(&frame).await.is_err() || stream.write_all(&payload).await.is_err() {
            break;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{EventFanout, EventPublication};
    use kairos_protocol::InstanceIdentity;
    use std::collections::VecDeque;
    use std::time::Duration;
    use tokio::sync::mpsc;

    fn identity() -> InstanceIdentity {
        InstanceIdentity::new("workspace", "launch", "instance")
    }

    #[tokio::test]
    async fn publication_backlog_flushes_after_queue_capacity_returns() {
        let (sender, mut receiver) = mpsc::channel(1);
        let mut publication = EventPublication::new("market", identity(), sender);
        publication.pending = VecDeque::from([vec![1], vec![2]]);

        publication.flush().unwrap();
        assert_eq!(receiver.recv().await.unwrap(), vec![1]);
        assert_eq!(publication.pending.len(), 1);
        publication.drain(Duration::from_secs(1)).await.unwrap();
        assert_eq!(receiver.recv().await.unwrap(), vec![2]);
        assert!(publication.is_empty());
    }

    #[tokio::test]
    async fn slow_client_is_removed_without_blocking_healthy_client() {
        let (slow_sender, _slow_receiver) = mpsc::channel(1);
        let (healthy_sender, mut healthy_receiver) = mpsc::channel(2);
        let mut fanout = EventFanout::new(1);
        fanout.clients = vec![slow_sender, healthy_sender];

        fanout.publish(vec![1]);
        fanout.publish(vec![2]);

        assert_eq!(fanout.clients.len(), 1);
        assert_eq!(healthy_receiver.recv().await.unwrap(), vec![1]);
        assert_eq!(healthy_receiver.recv().await.unwrap(), vec![2]);
    }
}
