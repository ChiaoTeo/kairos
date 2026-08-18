use super::*;

enum GatewayRequest {
    Submit {
        request: OrderEntryRequest,
        reply: SyncSender<Result<CommandOutcome<OrderEntryEvent>, IntegrationError>>,
    },
    Cancel {
        request: OrderEntryRequest,
        remote_order_id: String,
        at_unix_nanos: u64,
        reply: SyncSender<Result<CommandOutcome<OrderEntryEvent>, IntegrationError>>,
    },
}

pub struct AsyncQueuedOrderEntry {
    sender: tokio::sync::mpsc::Sender<GatewayRequest>,
}

pub struct AsyncGatewayWorker<C> {
    connection: C,
    receiver: tokio::sync::mpsc::Receiver<GatewayRequest>,
}

impl AsyncQueuedOrderEntry {
    pub fn channel<C>(connection: C, capacity: usize) -> (Self, AsyncGatewayWorker<C>)
    where
        C: OrderCommand,
    {
        let (sender, receiver) = tokio::sync::mpsc::channel(capacity.max(1));
        (
            Self { sender },
            AsyncGatewayWorker {
                connection,
                receiver,
            },
        )
    }

    fn request(
        &self,
        request: GatewayRequest,
        reply: Receiver<Result<CommandOutcome<OrderEntryEvent>, IntegrationError>>,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut request = Some(request);
        loop {
            match self
                .sender
                .try_send(request.take().expect("async gateway request present"))
            {
                Ok(()) => break,
                Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => {
                    return Err(IntegrationError::Unavailable(
                        "async order gateway worker is stopped".into(),
                    ));
                }
                Err(tokio::sync::mpsc::error::TrySendError::Full(value)) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(IntegrationError::Backpressure(
                            "async order gateway queue is full".into(),
                        ));
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        reply.recv().map_err(|_| {
            IntegrationError::Unavailable("async order gateway did not respond".into())
        })?
    }
}

impl BlockingOrderCommand for AsyncQueuedOrderEntry {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            GatewayRequest::Submit {
                request: request.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            GatewayRequest::Cancel {
                request: request.clone(),
                remote_order_id: remote_order_id.into(),
                at_unix_nanos,
                reply: reply_tx,
            },
            reply_rx,
        )
    }
}

impl<C> AsyncGatewayWorker<C>
where
    C: OrderCommand,
{
    pub async fn run(mut self, mut shutdown: tokio::sync::watch::Receiver<bool>) {
        loop {
            let request = tokio::select! {
                biased;
                changed = shutdown.changed() => {
                    if changed.is_err() || *shutdown.borrow() {
                        break;
                    }
                    continue;
                }
                request = self.receiver.recv() => request,
            };
            let Some(request) = request else {
                break;
            };
            // Once a command is dequeued it is always driven to completion.
            // Dropping a submitted trading future on shutdown would destroy
            // delivery certainty and could duplicate an order on recovery.
            match request {
                GatewayRequest::Submit { request, reply } => {
                    let _ = reply.send(self.connection.submit_order(&request).await);
                }
                GatewayRequest::Cancel {
                    request,
                    remote_order_id,
                    at_unix_nanos,
                    reply,
                } => {
                    let _ = reply.send(
                        self.connection
                            .cancel_order(&request, &remote_order_id, at_unix_nanos)
                            .await,
                    );
                }
            }
        }
    }
}
