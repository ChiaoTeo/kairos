use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};

use super::{ReferenceEventWriter, ReferenceEventWriterConfig};
use crate::ReferencePublication;
use crate::domain::{ReferenceError, ReferenceResult};

const EVENT_PUBLISH_QUEUE_CAPACITY: usize = 8;

struct PublishRequest {
    publications: Vec<ReferencePublication>,
    response: SyncSender<Result<(), String>>,
}

/// Concrete bounded publication worker selected by Reference composition.
///
/// This is a migration boundary: the durable outbox and acknowledgement rule
/// remain Reference-owned, while Conflux will eventually replace this worker's
/// lifecycle with a typed Contract publisher resource.
pub struct ReferenceEventPublisherRuntime {
    requests: SyncSender<PublishRequest>,
}

impl ReferenceEventPublisherRuntime {
    pub fn new(config: ReferenceEventWriterConfig) -> Self {
        let (requests, receiver) =
            mpsc::sync_channel::<PublishRequest>(EVENT_PUBLISH_QUEUE_CAPACITY);
        std::thread::Builder::new()
            .name("reference-event-publisher".into())
            .spawn(move || {
                let mut writer = ReferenceEventWriter::connect(&config)
                    .expect("connect reference event publisher worker");
                while let Ok(request) = receiver.recv() {
                    let result = writer
                        .publish(&request.publications)
                        .map_err(|error| error.to_string());
                    let _ = request.response.send(result);
                }
            })
            .expect("start reference event publisher worker");
        Self { requests }
    }

    pub fn publish(&self, publications: &[ReferencePublication]) -> ReferenceResult<()> {
        let receiver = self.enqueue(publications)?;
        receiver
            .recv()
            .map_err(|error| ReferenceError::Publication(error.to_string()))?
            .map_err(ReferenceError::Publication)
    }

    pub fn enqueue(
        &self,
        publications: &[ReferencePublication],
    ) -> ReferenceResult<Receiver<Result<(), String>>> {
        let (response, receiver) = mpsc::sync_channel(1);
        match self.requests.try_send(PublishRequest {
            publications: publications.to_vec(),
            response,
        }) {
            Ok(()) => Ok(receiver),
            Err(TrySendError::Full(_)) => Err(ReferenceError::Publication(
                "reference event publisher queue is full".into(),
            )),
            Err(TrySendError::Disconnected(_)) => Err(ReferenceError::Publication(
                "reference event publisher is unavailable".into(),
            )),
        }
    }
}
