use std::sync::mpsc::{self, SyncSender};
use std::thread::JoinHandle;

use kairos_primitives::runtime::InstanceIdentity;
use kairos_transport::AeronEndpoint;

use super::{CapitalAeronEventPublisher, CapitalEvent};
use crate::{ContractError, ContractResult};

enum Request {
    Publish {
        event: CapitalEvent,
        reply: SyncSender<Result<(), String>>,
    },
    Stop,
}

/// Send-safe handle for an Aeron publisher pinned to its owning thread.
pub struct QueuedCapitalEventPublisher {
    requests: SyncSender<Request>,
    worker: Option<JoinHandle<()>>,
}

impl QueuedCapitalEventPublisher {
    pub fn start(
        endpoint: AeronEndpoint,
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
        capacity: usize,
    ) -> ContractResult<Self> {
        if capacity == 0 {
            return Err(ContractError::Invalid(
                "Capital event publisher capacity must be positive".into(),
            ));
        }
        let actor_id = actor_id.into();
        let (requests, receiver) = mpsc::sync_channel::<Request>(capacity);
        let (ready_tx, ready_rx) = mpsc::sync_channel(1);
        let worker = std::thread::Builder::new()
            .name(format!("capital-events-{actor_id}"))
            .spawn(move || {
                let mut publisher =
                    match CapitalAeronEventPublisher::connect(&endpoint, actor_id, identity) {
                        Ok(publisher) => {
                            let _ = ready_tx.send(Ok(()));
                            publisher
                        },
                        Err(error) => {
                            let _ = ready_tx.send(Err(error.to_string()));
                            return;
                        },
                    };
                while let Ok(request) = receiver.recv() {
                    match request {
                        Request::Publish { event, reply } => {
                            let result =
                                publisher.publish(&event).map_err(|error| error.to_string());
                            let _ = reply.send(result);
                        },
                        Request::Stop => break,
                    }
                }
            })
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        match ready_rx.recv() {
            Ok(Ok(())) => Ok(Self {
                requests,
                worker: Some(worker),
            }),
            Ok(Err(error)) => {
                let _ = worker.join();
                Err(ContractError::Transport(error))
            },
            Err(error) => {
                let _ = worker.join();
                Err(ContractError::Transport(format!(
                    "Capital event publisher did not initialize: {error}"
                )))
            },
        }
    }

    pub fn publish(&self, event: &CapitalEvent) -> ContractResult<()> {
        let (reply, result) = mpsc::sync_channel(1);
        self.requests
            .send(Request::Publish {
                event: event.clone(),
                reply,
            })
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        result
            .recv()
            .map_err(|error| ContractError::Transport(error.to_string()))?
            .map_err(ContractError::Transport)
    }
}

impl Drop for QueuedCapitalEventPublisher {
    fn drop(&mut self) {
        let _ = self.requests.send(Request::Stop);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}
