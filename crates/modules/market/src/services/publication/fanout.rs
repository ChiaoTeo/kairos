use tokio::io::AsyncWriteExt;
use tokio::net::UnixStream;
use tokio::sync::mpsc::error::TrySendError;
use tokio::sync::mpsc::{self, Sender};

pub(crate) struct EventFanout {
    queue_capacity: usize,
    pub(super) clients: Vec<Sender<Vec<u8>>>,
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
