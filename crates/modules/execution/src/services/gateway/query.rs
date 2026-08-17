use super::*;

enum QueryGatewayRequest {
    Open {
        query: ExternalOrderQuery,
        reply: SyncSender<Result<Vec<ExternalOrder>, IntegrationError>>,
    },
    History {
        query: ExternalOrderQuery,
        reply: SyncSender<Result<Vec<ExternalOrder>, IntegrationError>>,
    },
    Detail {
        query: ExternalOrderQuery,
        reply: SyncSender<Result<Option<ExternalOrder>, IntegrationError>>,
    },
}

pub struct QueuedOrderQuery {
    sender: SyncSender<QueryGatewayRequest>,
}

pub struct QueryGatewayWorker {
    connection: Box<dyn OrderQueryConnection>,
    receiver: Receiver<QueryGatewayRequest>,
}

pub struct AsyncQueuedOrderQuery {
    sender: tokio::sync::mpsc::Sender<QueryGatewayRequest>,
}

pub struct AsyncQueryGatewayWorker<C> {
    connection: C,
    receiver: tokio::sync::mpsc::Receiver<QueryGatewayRequest>,
}

impl QueuedOrderQuery {
    pub fn channel(
        connection: Box<dyn OrderQueryConnection>,
        capacity: usize,
    ) -> (Self, QueryGatewayWorker) {
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        (
            Self { sender },
            QueryGatewayWorker {
                connection,
                receiver,
            },
        )
    }

    fn request<T>(
        &self,
        request: QueryGatewayRequest,
        reply: Receiver<Result<T, IntegrationError>>,
    ) -> Result<T, IntegrationError> {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut request = Some(request);
        loop {
            match self
                .sender
                .try_send(request.take().expect("query gateway request present"))
            {
                Ok(()) => break,
                Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                    return Err(IntegrationError::Unavailable(
                        "order query worker is stopped".into(),
                    ))
                }
                Err(std::sync::mpsc::TrySendError::Full(value)) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(IntegrationError::Backpressure(
                            "order query queue is full".into(),
                        ));
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        reply.recv().map_err(|_| {
            IntegrationError::Unavailable("order query worker did not respond".into())
        })?
    }
}

impl OrderQueryConnection for QueuedOrderQuery {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::Open {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::History {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::Detail {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }
}

impl QueryGatewayWorker {
    pub fn run(mut self, stop: std::sync::Arc<std::sync::atomic::AtomicBool>) {
        use std::sync::atomic::Ordering;
        while !stop.load(Ordering::Acquire) {
            match self.receiver.recv_timeout(Duration::from_millis(50)) {
                Ok(QueryGatewayRequest::Open { query, reply }) => {
                    let _ = reply.send(self.connection.open_orders(&query));
                }
                Ok(QueryGatewayRequest::History { query, reply }) => {
                    let _ = reply.send(self.connection.order_history(&query));
                }
                Ok(QueryGatewayRequest::Detail { query, reply }) => {
                    let _ = reply.send(self.connection.order_detail(&query));
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => break,
            }
        }
    }
}

impl AsyncQueuedOrderQuery {
    pub fn channel<C>(connection: C, capacity: usize) -> (Self, AsyncQueryGatewayWorker<C>)
    where
        C: AsyncOrderQueryConnection,
    {
        let (sender, receiver) = tokio::sync::mpsc::channel(capacity.max(1));
        (
            Self { sender },
            AsyncQueryGatewayWorker {
                connection,
                receiver,
            },
        )
    }

    fn request<T>(
        &self,
        request: QueryGatewayRequest,
        reply: Receiver<Result<T, IntegrationError>>,
    ) -> Result<T, IntegrationError> {
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut request = Some(request);
        loop {
            match self
                .sender
                .try_send(request.take().expect("async query request present"))
            {
                Ok(()) => break,
                Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => {
                    return Err(IntegrationError::Unavailable(
                        "async order query worker is stopped".into(),
                    ));
                }
                Err(tokio::sync::mpsc::error::TrySendError::Full(value)) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(IntegrationError::Backpressure(
                            "async order query queue is full".into(),
                        ));
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        reply.recv().map_err(|_| {
            IntegrationError::Unavailable("async order query worker did not respond".into())
        })?
    }
}

impl OrderQueryConnection for AsyncQueuedOrderQuery {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::Open {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::History {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            QueryGatewayRequest::Detail {
                query: query.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }
}

impl<C> AsyncQueryGatewayWorker<C>
where
    C: AsyncOrderQueryConnection,
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
            match request {
                QueryGatewayRequest::Open { query, reply } => {
                    let _ = reply.send(self.connection.open_orders(&query).await);
                }
                QueryGatewayRequest::History { query, reply } => {
                    let _ = reply.send(self.connection.order_history(&query).await);
                }
                QueryGatewayRequest::Detail { query, reply } => {
                    let _ = reply.send(self.connection.order_detail(&query).await);
                }
            }
        }
    }
}
