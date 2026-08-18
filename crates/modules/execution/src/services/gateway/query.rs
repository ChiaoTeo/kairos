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

pub struct AsyncQueuedOrderQuery {
    sender: tokio::sync::mpsc::Sender<QueryGatewayRequest>,
}

pub struct AsyncQueryGatewayWorker<C> {
    connection: C,
    receiver: tokio::sync::mpsc::Receiver<QueryGatewayRequest>,
}

impl AsyncQueuedOrderQuery {
    pub fn channel<C>(connection: C, capacity: usize) -> (Self, AsyncQueryGatewayWorker<C>)
    where
        C: OrderQuery,
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

impl BlockingOrderQuery for AsyncQueuedOrderQuery {
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
    C: OrderQuery,
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
