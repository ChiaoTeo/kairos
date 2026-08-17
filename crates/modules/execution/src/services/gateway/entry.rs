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

pub struct QueuedOrderEntry {
    sender: SyncSender<GatewayRequest>,
}

pub struct GatewayWorker {
    connection: Box<dyn OrderEntryConnection>,
    receiver: Receiver<GatewayRequest>,
}

/// Synchronous application proxy backed by an async provider connection.
///
/// Execution's state owner remains synchronous and single-threaded, while the
/// provider future is polled by the business process Tokio runtime. The proxy
/// blocks only the state-owner thread waiting for its command result; it never
/// creates or owns a runtime.
pub struct AsyncQueuedOrderEntry {
    sender: tokio::sync::mpsc::Sender<GatewayRequest>,
}

pub struct AsyncGatewayWorker<C> {
    connection: C,
    receiver: tokio::sync::mpsc::Receiver<GatewayRequest>,
}

impl QueuedOrderEntry {
    pub fn channel(
        connection: Box<dyn OrderEntryConnection>,
        capacity: usize,
    ) -> (Self, GatewayWorker) {
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        (
            Self { sender },
            GatewayWorker {
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
                .try_send(request.take().expect("gateway request present"))
            {
                Ok(()) => break,
                Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                    return Err(IntegrationError::Unavailable(
                        "order gateway worker is stopped".into(),
                    ))
                }
                Err(std::sync::mpsc::TrySendError::Full(value)) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(IntegrationError::Backpressure(
                            "order gateway queue is full".into(),
                        ));
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        reply.recv().map_err(|_| {
            IntegrationError::Unavailable("order gateway worker did not respond".into())
        })?
    }
}

impl OrderEntryConnection for QueuedOrderEntry {
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

impl GatewayWorker {
    pub fn run(mut self, stop: std::sync::Arc<std::sync::atomic::AtomicBool>) {
        use std::sync::atomic::Ordering;
        while !stop.load(Ordering::Acquire) {
            match self.receiver.recv_timeout(Duration::from_millis(50)) {
                Ok(GatewayRequest::Submit { request, reply }) => {
                    let _ = reply.send(self.connection.submit_order(&request));
                }
                Ok(GatewayRequest::Cancel {
                    request,
                    remote_order_id,
                    at_unix_nanos,
                    reply,
                }) => {
                    let _ = reply.send(self.connection.cancel_order(
                        &request,
                        &remote_order_id,
                        at_unix_nanos,
                    ));
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => break,
            }
        }
    }
}

impl AsyncQueuedOrderEntry {
    pub fn channel<C>(connection: C, capacity: usize) -> (Self, AsyncGatewayWorker<C>)
    where
        C: AsyncOrderEntryConnection,
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

impl OrderEntryConnection for AsyncQueuedOrderEntry {
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
    C: AsyncOrderEntryConnection,
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
